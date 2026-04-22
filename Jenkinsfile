pipeline {
    agent any

    stages {

        stage('Check Workspace') {
            steps {
                sh 'ls -l /workspace'
            }
        }

        stage('Build CU Image') {
            steps {
                sh 'docker build -t ran-simulator-cu:latest /workspace/cu-service'
            }
        }

        stage('Build DU Image') {
            steps {
                sh 'docker build -t ran-simulator-du:latest /workspace/du-service'
            }
        }

        stage('Load Images into kind') {
            steps {
                echo "Loading images directly into kind containerd..."

                sh '''
                docker save ran-simulator-cu:latest | docker exec -i kind-control-plane ctr -n k8s.io images import -
                docker save ran-simulator-du:latest | docker exec -i kind-control-plane ctr -n k8s.io images import -

                echo "Verifying images inside kind..."
                docker exec kind-control-plane ctr -n k8s.io images ls | grep ran-simulator || true
                '''
            }
        }

        stage('Deploy to Kubernetes') {
            steps {
                sh 'kubectl apply -f /workspace/cu-deployment.yaml'
                sh 'kubectl apply -f /workspace/cu-service.yaml'
                sh 'kubectl apply -f /workspace/du-deployment.yaml'
                sh 'kubectl apply -f /workspace/du-service.yaml'
            }
        }

        stage('Wait for Pods Ready') {
            steps {
                sh '''
                    kubectl wait --for=condition=ready pod -l app=cu --timeout=60s
                    kubectl wait --for=condition=ready pod -l app=du --timeout=60s
                '''
            }
        }

        stage('Verify Pods') {
            steps {
                sh 'kubectl get pods'
            }
        }

        stage('Test DU Service') {
            steps {
                sh '''
                    echo "Starting E2E DU-CU validation..."

                    kubectl port-forward service/du-service 8000:8000 &
                    PF_DU_PID=$!

                    kubectl port-forward service/cu-service 8001:8001 &
                    PF_CU_PID=$!

                    sleep 5

                    echo "Running 10 attach iterations..."

                    for i in $(seq 1 10)
                    do
                        curl -s -X POST http://localhost:8000/attach \
                        -H "Content-Type: application/json" \
                        -d '{"ue_id":"UE700"}'
                        echo ""
                        sleep 1
                    done

                    echo "Fetching DU metrics..."
                    DU_METRICS=$(curl -s http://localhost:8000/metrics)

                    echo "Fetching CU metrics..."
                    CU_METRICS=$(curl -s http://localhost:8001/metrics)

                    echo "DU Metrics: $DU_METRICS"
                    echo "CU Metrics: $CU_METRICS"

                    DU_SR=$(echo "$DU_METRICS" | jq '.rach_sr_percent')
                    CU_SR=$(echo "$CU_METRICS" | jq '.attach_sr_percent')

                    echo "DU Success Rate: $DU_SR"
                    echo "CU Success Rate: $CU_SR"

                    kill $PF_DU_PID || true
                    kill $PF_CU_PID || true

                    if [ "$(echo "$CU_SR >= 70" | bc -l)" -eq 1 ] && [ "$(echo "$DU_SR >= 70" | bc -l)" -eq 1 ]; then
                        echo "E2E Test Passed ✅"
                    else
                        echo "E2E Test Failed ❌"
                        exit 1
                    fi
                '''
            }
        }

    }
}
