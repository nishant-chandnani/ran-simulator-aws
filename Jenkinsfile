pipeline {
    agent any

    environment {
        AWS_REGION = "ap-southeast-2"
        ECR_REGISTRY = "276594885557.dkr.ecr.ap-southeast-2.amazonaws.com"
        VERSION = "build-${BUILD_NUMBER}"
    }

    stages {

        stage('Build Images') {
            steps {
                sh '''
                cd cu-service
                docker build -t cu-service:${VERSION} .

                cd ../du-service
                docker build -t du-service:${VERSION} .
                '''
            }
        }

        stage('Login to ECR') {
            steps {
                sh '''
                aws ecr get-login-password --region $AWS_REGION | \
                docker login --username AWS --password-stdin $ECR_REGISTRY
                '''
            }
        }

        stage('Tag & Push Images') {
            steps {
                sh '''
                docker tag cu-service:${VERSION} $ECR_REGISTRY/ran-simulator-cu:${VERSION}
                docker tag du-service:${VERSION} $ECR_REGISTRY/ran-simulator-du:${VERSION}

                docker push $ECR_REGISTRY/ran-simulator-cu:${VERSION}
                docker push $ECR_REGISTRY/ran-simulator-du:${VERSION}
                '''
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
            steps {
                sh '''
                export KUBECONFIG=/var/lib/jenkins/.kube/config
                
                cd helm-chart
                helm upgrade --install ran-sim . \
                  --set cu.tag=${VERSION} \
                  --set du.tag=${VERSION}
                '''
            }
        }

        stage('Health Check') {
            steps {
                sh '''
                echo "Checking service availability..."
                sleep 10
                curl -f -X POST http://localhost:30000/attach \
                -H "Content-Type: application/json" \
                -d '{"ue_id":"healthcheck"}' || exit 1
                '''
            }
        }

        stage('Load Test') {
            steps {
                sh '''
                echo "Starting load test..."

                for round in {1..5}; do
                  echo "Round $round"

                  for i in {1..20}; do
                    curl -s -X POST http://localhost:30000/attach \
                    -H "Content-Type: application/json" \
                    -d "{\"ue_id\":\"UE$i\"}" &
                  done

                  wait
                  sleep 2
                done

                echo "Load test completed"
                '''
            }
        }

        stage('Metrics Validation') {
            steps {
                sh '''
                echo "Checking metrics..."

                curl -s http://localhost:30000/metrics | grep -i ue || exit 1

                echo "Metrics validation passed"
                '''
            }
        }
    }
}   