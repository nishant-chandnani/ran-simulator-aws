def SHOULD_BUILD_CU_IMAGE = false
def SHOULD_BUILD_DU_IMAGE = false
def CU_IMAGE_TAG = ""
def DU_IMAGE_TAG = ""
pipeline {
    agent any

    environment {
        AWS_REGION = "ap-southeast-2"
        ECR_REGISTRY = "276594885557.dkr.ecr.ap-southeast-2.amazonaws.com"
        VERSION = "${sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()}"
        KUBECONFIG_PATH = "/var/lib/jenkins/.kube/config"
        ECR_REPOSITORY_PREFIX = "ran-simulator"
        LOAD_TEST_ROUNDS = "30"
        REQUESTS_PER_ROUND = "100"
    }

    stages {

        stage('Decide Image Strategy') {
            steps {
                script {
                    // Detect changes using Jenkins built-in changeSets
                    def changes = ""
                    for (changeSet in currentBuild.changeSets) {
                        for (entry in changeSet.items) {
                            for (file in entry.affectedFiles) {
                                changes += file.path + "\n"
                            }
                        }
                    }

                    echo "Detected changed files:\n${changes}"
                    echo "Current Git image tag candidate: ${env.VERSION}"
                    def cuChanged = changes.readLines().any { changedFile ->
                        changedFile.startsWith("cu-service/")
                    }

                    def duChanged = changes.readLines().any { changedFile ->
                        changedFile.startsWith("du-service/")
                    }

                    def deployedCuTag = sh(
                        script: '''
                            export KUBECONFIG="$KUBECONFIG_PATH"
                            kubectl get deployment cu-deployment -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null | awk -F: '{print $NF}'
                        ''',
                        returnStdout: true
                    ).trim()

                    def deployedDuTag = sh(
                        script: '''
                            export KUBECONFIG="$KUBECONFIG_PATH"
                            kubectl get deployment du-deployment -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null | awk -F: '{print $NF}'
                        ''',
                        returnStdout: true
                    ).trim()

                    if (cuChanged) {
                        echo "CU code changes detected. CU image will be built and pushed with new tag: ${env.VERSION}"
                        SHOULD_BUILD_CU_IMAGE = true
                        CU_IMAGE_TAG = env.VERSION
                    } else if (deployedCuTag) {
                        echo "No CU code changes detected. Reusing currently deployed CU image tag: ${deployedCuTag}"
                        SHOULD_BUILD_CU_IMAGE = false
                        CU_IMAGE_TAG = deployedCuTag
                    } else {
                        echo "No deployed CU image tag found. Falling back to CU build using tag: ${env.VERSION}"
                        SHOULD_BUILD_CU_IMAGE = true
                        CU_IMAGE_TAG = env.VERSION
                    }

                    if (duChanged) {
                        echo "DU code changes detected. DU image will be built and pushed with new tag: ${env.VERSION}"
                        SHOULD_BUILD_DU_IMAGE = true
                        DU_IMAGE_TAG = env.VERSION
                    } else if (deployedDuTag) {
                        echo "No DU code changes detected. Reusing currently deployed DU image tag: ${deployedDuTag}"
                        SHOULD_BUILD_DU_IMAGE = false
                        DU_IMAGE_TAG = deployedDuTag
                    } else {
                        echo "No deployed DU image tag found. Falling back to DU build using tag: ${env.VERSION}"
                        SHOULD_BUILD_DU_IMAGE = true
                        DU_IMAGE_TAG = env.VERSION
                    }

                    echo "Final decision -> SHOULD_BUILD_CU_IMAGE=${SHOULD_BUILD_CU_IMAGE}, CU_IMAGE_TAG=${CU_IMAGE_TAG}"
                    echo "Final decision -> SHOULD_BUILD_DU_IMAGE=${SHOULD_BUILD_DU_IMAGE}, DU_IMAGE_TAG=${DU_IMAGE_TAG}"
                }
            }
        }

        stage('Build CU Image') {
            when {
                expression { return SHOULD_BUILD_CU_IMAGE }
            }
            steps {
                sh """
                echo "Building CU image using VERSION: ${env.VERSION}"

                cd cu-service
                docker build -t cu-service:${env.VERSION} .
                """
            }
        }

        stage('Build DU Image') {
            when {
                expression { return SHOULD_BUILD_DU_IMAGE }
            }
            steps {
                sh """
                echo "Building DU image using VERSION: ${env.VERSION}"

                cd du-service
                docker build -t du-service:${env.VERSION} .
                """
            }
        }

        stage('Login to ECR') {
            when {
                expression { return SHOULD_BUILD_CU_IMAGE || SHOULD_BUILD_DU_IMAGE }
            }
            steps {
                sh '''
                aws ecr get-login-password --region $AWS_REGION | \
                docker login --username AWS --password-stdin $ECR_REGISTRY
                '''
            }
        }


        stage('Tag & Push CU Image') {
            when {
                expression { return SHOULD_BUILD_CU_IMAGE }
            }
            steps {
                sh """
                echo "Tagging and pushing CU image with VERSION: ${env.VERSION}"

                docker tag cu-service:${env.VERSION} ${ECR_REGISTRY}/${ECR_REPOSITORY_PREFIX}-cu:${env.VERSION}
                docker push ${ECR_REGISTRY}/${ECR_REPOSITORY_PREFIX}-cu:${env.VERSION}
                """
            }
        }

        stage('Tag & Push DU Image') {
            when {
                expression { return SHOULD_BUILD_DU_IMAGE }
            }
            steps {
                sh """
                echo "Tagging and pushing DU image with VERSION: ${env.VERSION}"

                docker tag du-service:${env.VERSION} ${ECR_REGISTRY}/${ECR_REPOSITORY_PREFIX}-du:${env.VERSION}
                docker push ${ECR_REGISTRY}/${ECR_REPOSITORY_PREFIX}-du:${env.VERSION}
                """
            }
        }

        stage('Update ECR Secret') {
            steps {
                sh '''
                export KUBECONFIG="$KUBECONFIG_PATH"

                kubectl delete secret ecr-secret --ignore-not-found

                kubectl create secret docker-registry ecr-secret \
                --docker-server=$ECR_REGISTRY \
                --docker-username=AWS \
                --docker-password=$(aws ecr get-login-password --region $AWS_REGION)
                '''
            }
        }

        stage('Deploy to Kubernetes') {
            steps {
                sh """
                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Deploying CU with CU_IMAGE_TAG: ${CU_IMAGE_TAG}"
                echo "Deploying DU with DU_IMAGE_TAG: ${DU_IMAGE_TAG}"

                cd helm-chart
                helm upgrade --install ran-sim . \
                  --set cu.tag=${CU_IMAGE_TAG} \
                  --set du.tag=${DU_IMAGE_TAG} \
                  --set pipeline.runId=${BUILD_NUMBER}

                echo "Forcing fresh rollout after deployment..."
                kubectl rollout restart deployment cu-deployment
                kubectl rollout restart deployment du-deployment
                
                echo "Waiting for CU and DU deployments to become ready..."
                kubectl rollout status deployment/cu-deployment --timeout=120s
                kubectl rollout status deployment/du-deployment --timeout=120s
                """
            }
        }

        stage('Health Check') {
            steps {
                sh '''
                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Checking service availability via Kubernetes port-forward..."
                kubectl port-forward service/du-service 18000:8000 > /dev/null 2>&1 &
                DU_HEALTH_PF_PID=$!

                cleanup() {
                  kill $DU_HEALTH_PF_PID 2>/dev/null || true
                }
                trap cleanup EXIT

                sleep 5

                echo "DU metrics endpoint health check..."
                curl -f http://localhost:18000/metrics || {
                  echo "DU service health check failed"
                  exit 1
                }
                '''
            }
        }

        stage('Load Test') {
            steps {
                sh '''
                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Starting port-forward for DU service..."
                kubectl port-forward service/du-service 18000:8000 > /dev/null 2>&1 &
                DU_PF_PID=$!

                echo "Starting port-forward for CU service..."
                kubectl port-forward service/cu-service 18001:8001 > /dev/null 2>&1 &
                CU_PF_PID=$!

                cleanup() {
                  kill $DU_PF_PID 2>/dev/null || true
                  kill $CU_PF_PID 2>/dev/null || true
                }
                trap cleanup EXIT

                sleep 5

                echo "Resetting metrics before load test..."

                # Reset DU metrics
                curl -s -X POST http://localhost:18000/reset-metrics || true

                # Reset CU metrics
                curl -s -X POST http://localhost:18001/reset-metrics || true

                sleep 2

                echo "Starting load test (${LOAD_TEST_ROUNDS} rounds x ${REQUESTS_PER_ROUND} requests)..."

                TOTAL_REQUESTS=0

                for round in $(seq 1 "$LOAD_TEST_ROUNDS"); do
                  echo "\n===== ROUND $round ====="

                  CURL_PIDS=""

                  for i in $(seq 1 "$REQUESTS_PER_ROUND"); do
                    curl -s --connect-timeout 3 --max-time 10 -X POST http://localhost:18000/attach \
                    -H "Content-Type: application/json" \
                    -d '{"ue_id":"UE'"$round""$i"'"}' &
                    CURL_PIDS="$CURL_PIDS $!"
                  done

                  for pid in $CURL_PIDS; do
                    wait $pid || {
                      echo "One or more attach requests failed or timed out"
                      exit 1
                    }
                  done

                  TOTAL_REQUESTS=$((TOTAL_REQUESTS + REQUESTS_PER_ROUND))
                  sleep 1
                done

                echo "\nTotal requests sent: $TOTAL_REQUESTS"
                echo "Load test completed"
                '''
            }
        }

        stage('Metrics Validation') {
            steps {
                sh '''
                echo "\n===== KPI VALIDATION ====="

                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Starting port-forward for DU service..."
                kubectl port-forward service/du-service 18000:8000 > /dev/null 2>&1 &
                DU_PF_PID=$!

                echo "Starting port-forward for CU service..."
                kubectl port-forward service/cu-service 18001:8001 > /dev/null 2>&1 &
                CU_PF_PID=$!

                cleanup() {
                  kill $DU_PF_PID 2>/dev/null || true
                  kill $CU_PF_PID 2>/dev/null || true
                }
                trap cleanup EXIT

                sleep 5

                echo "\nFetching DU metrics (JSON)..."
                DU_JSON=$(curl -s http://localhost:18000/metrics-json)
                echo "$DU_JSON"

                echo "\nFetching CU metrics (JSON)..."
                CU_JSON=$(curl -s http://localhost:18001/metrics-json)
                echo "$CU_JSON"

                # Validate JSON
                echo "$DU_JSON" | jq . > /dev/null || { echo "Invalid DU JSON"; exit 1; }
                echo "$CU_JSON" | jq . > /dev/null || { echo "Invalid CU JSON"; exit 1; }

                # Extract KPIs safely
                RACH_SR=$(echo "$DU_JSON" | jq -r '.rach_sr_percent // 0' | xargs)
                ATTACH_SR=$(echo "$CU_JSON" | jq -r '.attach_sr_percent // 0' | xargs)

                # Ensure numeric using jq
                echo "$RACH_SR" | jq -e 'numbers' > /dev/null 2>&1 || { echo "Invalid RACH SR value"; exit 1; }
                echo "$ATTACH_SR" | jq -e 'numbers' > /dev/null 2>&1 || { echo "Invalid ATTACH SR value"; exit 1; }

                echo "\n===== KPI RESULTS ====="
                echo "RACH SR   : $RACH_SR"
                echo "ATTACH SR : $ATTACH_SR"

                RACH_THRESHOLD=75
                ATTACH_THRESHOLD=80

                RACH_CHECK=$(echo "$RACH_SR < $RACH_THRESHOLD" | bc -l 2>/dev/null)
                ATTACH_CHECK=$(echo "$ATTACH_SR < $ATTACH_THRESHOLD" | bc -l 2>/dev/null)

                # Fail safe if bc fails or empty
                if [ -z "$RACH_CHECK" ]; then RACH_CHECK=1; fi
                if [ -z "$ATTACH_CHECK" ]; then ATTACH_CHECK=1; fi

                echo "\n===== KPI DECISION ====="

                if (( RACH_CHECK )); then
                  echo "❌ RACH SR BELOW threshold ($RACH_SR < $RACH_THRESHOLD)"
                else
                  echo "✅ RACH SR OK ($RACH_SR >= $RACH_THRESHOLD)"
                fi

                if (( ATTACH_CHECK )); then
                  echo "❌ ATTACH SR BELOW threshold ($ATTACH_SR < $ATTACH_THRESHOLD)"
                else
                  echo "✅ ATTACH SR OK ($ATTACH_SR >= $ATTACH_THRESHOLD)"
                fi

                if (( RACH_CHECK )) || (( ATTACH_CHECK )); then
                  echo "\n❌ KPI validation FAILED (RACH threshold: $RACH_THRESHOLD, ATTACH threshold: $ATTACH_THRESHOLD)"
                  exit 1
                fi

                echo "\n✅ KPI validation PASSED (RACH threshold: $RACH_THRESHOLD, ATTACH threshold: $ATTACH_THRESHOLD)"
                '''
            }
        }
    }
}