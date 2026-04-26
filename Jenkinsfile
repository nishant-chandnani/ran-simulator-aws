def SKIP = false
pipeline {
    agent any

    environment {
        AWS_REGION = "ap-southeast-2"
        ECR_REGISTRY = "276594885557.dkr.ecr.ap-southeast-2.amazonaws.com"
        VERSION = ""
    }

    stages {

        stage('Set Version & Check Changes') {
            steps {
                script {
                    // Set VERSION as git commit hash
                    env.VERSION = sh(script: "git rev-parse --short HEAD", returnStdout: true).trim()
                    VERSION = env.VERSION

                    // Detect changes
                    def changes = sh(
                        script: "git diff --name-only HEAD~1 HEAD",
                        returnStdout: true
                    ).trim()

                    def skipLocal
                    if (!changes.contains("cu-service") && !changes.contains("du-service")) {
                        echo "No changes in CU/DU. Skipping build and push."
                        skipLocal = true
                    } else {
                        echo "Changes detected in CU/DU. Proceeding with build."
                        skipLocal = false
                    }

                    // assign once to global variable
                    SKIP = skipLocal
                }
            }
        }

        stage('Build Images') {
            when {
                expression { return !SKIP }
            }
            steps {
                sh """
                echo "Using VERSION: \${VERSION}"

                cd cu-service
                docker build -t cu-service:\${VERSION} .

                cd ../du-service
                docker build -t du-service:\${VERSION} .
                """
            }
        }

        stage('Login to ECR') {
            when {
                expression { return !SKIP }
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
                expression { return !SKIP }
            }
            steps {
                sh """
                echo "Using VERSION for tagging: \${VERSION}"

                docker tag cu-service:\${VERSION} \${ECR_REGISTRY}/ran-simulator-cu:\${VERSION}
                docker tag du-service:\${VERSION} \${ECR_REGISTRY}/ran-simulator-du:\${VERSION}

                docker push \${ECR_REGISTRY}/ran-simulator-cu:\${VERSION}
                docker push \${ECR_REGISTRY}/ran-simulator-du:\${VERSION}
                """
            }
        }

        stage('Update ECR Secret') {
            steps {
                sh '''
                export KUBECONFIG=/var/lib/jenkins/.kube/config

                kubectl delete secret ecr-secret --ignore-not-found

                kubectl create secret docker-registry ecr-secret \
                --docker-server=276594885557.dkr.ecr.ap-southeast-2.amazonaws.com \
                --docker-username=AWS \
                --docker-password=$(aws ecr get-login-password --region ap-southeast-2)
                '''
            }
        }

        stage('Deploy to Kubernetes') {
            when {
                expression { return !SKIP }
            }
            steps {
                sh """
                export KUBECONFIG=/var/lib/jenkins/.kube/config

                echo "Deploying with VERSION: \${VERSION}"

                cd helm-chart
                helm upgrade --install ran-sim . \
                  --set cu.tag=\${VERSION} \
                  --set du.tag=\${VERSION}
                """
            }
        }

        stage('Health Check') {
            steps {
                sh '''
                echo "Checking service availability..."
                sleep 10
                curl -f http://localhost:30000/metrics || exit 1
                '''
            }
        }

        stage('Load Test') {
            steps {
                sh '''
                echo "Resetting metrics before load test..."

                # Reset DU metrics (NodePort)
                curl -s -X POST http://localhost:30000/reset-metrics || true

                # Reset CU metrics (via port-forward)
                kubectl port-forward service/cu-service 8001:8001 > /dev/null 2>&1 &
                PF_RESET_PID=$!
                sleep 5
                curl -s -X POST http://localhost:8001/reset-metrics || true
                kill $PF_RESET_PID || true

                sleep 2

                echo "Starting load test (10 rounds x 30 requests)..."

                TOTAL_REQUESTS=0

                for round in $(seq 1 10); do
                  echo "\n===== ROUND $round ====="

                  for i in $(seq 1 30); do
                    curl -s -X POST http://localhost:30000/attach \
                    -H "Content-Type: application/json" \
                    -d '{"ue_id":"UE'"$round""$i"'"}' &
                  done

                  wait
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

                export KUBECONFIG=/var/lib/jenkins/.kube/config

                echo "Starting port-forward for CU service..."
                kubectl port-forward service/cu-service 8001:8001 > /dev/null 2>&1 &
                PF_PID=$!

                sleep 5

                echo "\nFetching DU metrics (JSON)..."
                DU_JSON=$(curl -s http://localhost:30000/metrics-json)
                echo "$DU_JSON"

                echo "\nFetching CU metrics (JSON)..."
                CU_JSON=$(curl -s http://localhost:8001/metrics-json)
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

                # Cleanup
                kill $PF_PID || true

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