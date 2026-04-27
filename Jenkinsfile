def SHOULD_BUILD_IMAGES = false
def IMAGE_TAG = ""
pipeline {
    agent any

    environment {
        AWS_REGION = "ap-southeast-2"
        ECR_REGISTRY = "276594885557.dkr.ecr.ap-southeast-2.amazonaws.com"
        VERSION = "${sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()}"
        KUBECONFIG_PATH = "/var/lib/jenkins/.kube/config"
        ECR_REPOSITORY_PREFIX = "ran-simulator"
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

                    def cuDuChanged = changes.readLines().any { changedFile ->
                        changedFile.startsWith("cu-service/") || changedFile.startsWith("du-service/")
                    }

                    if (cuDuChanged) {
                        echo "CU/DU code changes detected. Build, push, and deploy will use new image tag: ${env.VERSION}"
                        SHOULD_BUILD_IMAGES = true
                        IMAGE_TAG = env.VERSION
                    } else {
                        echo "No CU/DU code changes detected. Build and push will be skipped; deployment will reuse the currently deployed image tag."

                        def deployedTag = sh(
                            script: '''
                                export KUBECONFIG="$KUBECONFIG_PATH"
                                kubectl get deployment cu-deployment -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null | awk -F: '{print $NF}'
                            ''',
                            returnStdout: true
                        ).trim()

                        if (deployedTag) {
                            echo "Reusing currently deployed image tag: ${deployedTag}"
                            SHOULD_BUILD_IMAGES = false
                            IMAGE_TAG = deployedTag
                        } else {
                            echo "No deployed CU image tag found. Falling back to full build using tag: ${env.VERSION}"
                            SHOULD_BUILD_IMAGES = true
                            IMAGE_TAG = env.VERSION
                        }
                    }

                    echo "Final decision -> SHOULD_BUILD_IMAGES=${SHOULD_BUILD_IMAGES}, IMAGE_TAG=${IMAGE_TAG}"
                }
            }
        }

        stage('Build Images') {
            when {
                expression { return SHOULD_BUILD_IMAGES }
            }
            steps {
                sh """
                echo "Using VERSION: ${env.VERSION}"

                cd cu-service
                docker build -t cu-service:${env.VERSION} .

                cd ../du-service
                docker build -t du-service:${env.VERSION} .
                """
            }
        }

        stage('Login to ECR') {
            when {
                expression { return SHOULD_BUILD_IMAGES }
            }
            steps {
                sh '''
                aws ecr get-login-password --region $AWS_REGION | \
                docker login --username AWS --password-stdin $ECR_REGISTRY
                '''
            }
        }

        stage('Tag & Push Images') {
            when {
                expression { return SHOULD_BUILD_IMAGES }
            }
            steps {
                sh """
                echo "Using VERSION for tagging: ${env.VERSION}"

                docker tag cu-service:${env.VERSION} ${ECR_REGISTRY}/${ECR_REPOSITORY_PREFIX}-cu:${env.VERSION}
                docker tag du-service:${env.VERSION} ${ECR_REGISTRY}/${ECR_REPOSITORY_PREFIX}-du:${env.VERSION}

                docker push ${ECR_REGISTRY}/${ECR_REPOSITORY_PREFIX}-cu:${env.VERSION}
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

                echo "Deploying with IMAGE_TAG: ${IMAGE_TAG}"

                cd helm-chart
                helm upgrade --install ran-sim . \
                  --set cu.tag=${IMAGE_TAG} \
                  --set du.tag=${IMAGE_TAG}

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

                echo "Starting load test (10 rounds x 30 requests)..."

                TOTAL_REQUESTS=0

                for round in $(seq 1 10); do
                  echo "\n===== ROUND $round ====="

                  CURL_PIDS=""

                  for i in $(seq 1 30); do
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

                  TOTAL_REQUESTS=$((TOTAL_REQUESTS + 30))
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