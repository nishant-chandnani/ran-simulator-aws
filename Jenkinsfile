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

        stage('Deploy to Kubernetes') {
            steps {
                sh '''
                cd helm-chart
                helm upgrade --install ran-sim . \
                  --set cu.tag=${VERSION} \
                  --set du.tag=${VERSION}
                '''
            }
        }
    }
}