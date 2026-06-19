def SHOULD_BUILD_CU_IMAGE = false
def SHOULD_BUILD_DU_IMAGE = false
def CU_IMAGE_TAG = ""
def DU_IMAGE_TAG = ""

pipeline {
    agent any

    environment {
        AWS_REGION = "ap-southeast-2"
        METRICS_SERVER_VERSION = "v0.7.2"
        AWS_ACCOUNT_ID = "${sh(script: 'aws sts get-caller-identity --query Account --output text', returnStdout: true).trim()}"
        ECR_REGISTRY = "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
        VERSION = "${sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()}"
        KUBECONFIG_PATH = "/var/lib/jenkins/.kube/config"
        EKS_CLUSTER_NAME = "ran-simulator-eks"
        ALB_CONTROLLER_ROLE_NAME = "AmazonEKSLoadBalancerControllerRole"
        ECR_REPOSITORY_PREFIX = "ran-simulator"
        LOAD_PHASE_1_ROUNDS = "5"
        LOAD_PHASE_1_REQUESTS = "50"
        LOAD_PHASE_2_ROUNDS = "5"
        LOAD_PHASE_2_REQUESTS = "150"
        LOAD_PHASE_3_ROUNDS = "10"
        LOAD_PHASE_3_REQUESTS = "1000"
        RACH_THRESHOLD = "75"
        ATTACH_THRESHOLD = "79.5"
    }

    stages {

        stage('Update EKS kubeconfig') {
            steps {
                sh '''
                mkdir -p /var/lib/jenkins/.kube

                aws eks update-kubeconfig \
                  --region $AWS_REGION \
                  --name $EKS_CLUSTER_NAME \
                  --kubeconfig $KUBECONFIG_PATH

                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Validating EKS cluster connectivity..."
                kubectl get nodes
                '''
            }
        }

        stage('Install Metrics Server') {
            steps {
                sh '''
                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Installing/Updating Metrics Server..."
                kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/$METRICS_SERVER_VERSION/components.yaml

                echo "Waiting for Metrics Server deployment rollout..."
                kubectl rollout status deployment/metrics-server -n kube-system --timeout=180s

                echo "Validating Metrics API availability..."
                kubectl top nodes || true
                '''
            }
        }

        stage('Install AWS Load Balancer Controller') {
            steps {
                sh '''
                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Checking whether AWS Load Balancer Controller is already installed..."

                if helm status aws-load-balancer-controller -n kube-system > /dev/null 2>&1; then
                  echo "AWS Load Balancer Controller already exists. Skipping Helm install."
                  kubectl rollout status deployment/aws-load-balancer-controller -n kube-system --timeout=300s
                  kubectl get deployment aws-load-balancer-controller -n kube-system
                  exit 0
                fi

                echo "Discovering EKS VPC ID dynamically..."
                EKS_VPC_ID=$(aws eks describe-cluster \
                  --region "$AWS_REGION" \
                  --name "$EKS_CLUSTER_NAME" \
                  --query "cluster.resourcesVpcConfig.vpcId" \
                  --output text)

                if [ -z "$EKS_VPC_ID" ] || [ "$EKS_VPC_ID" = "None" ]; then
                  echo "Unable to discover EKS VPC ID for cluster $EKS_CLUSTER_NAME"
                  exit 1
                fi

                echo "Using EKS VPC ID: $EKS_VPC_ID"

                echo "Discovering AWS Load Balancer Controller IAM role ARN dynamically..."
                ALB_CONTROLLER_ROLE_ARN=$(aws iam get-role \
                  --role-name "$ALB_CONTROLLER_ROLE_NAME" \
                  --query "Role.Arn" \
                  --output text)

                if [ -z "$ALB_CONTROLLER_ROLE_ARN" ] || [ "$ALB_CONTROLLER_ROLE_ARN" = "None" ]; then
                  echo "Unable to discover IAM role ARN for $ALB_CONTROLLER_ROLE_NAME"
                  exit 1
                fi

                echo "Using AWS Load Balancer Controller role ARN: $ALB_CONTROLLER_ROLE_ARN"

                echo "Adding AWS EKS Helm repository..."
                helm repo add eks https://aws.github.io/eks-charts || true
                helm repo update

                echo "Installing AWS Load Balancer Controller..."

                if [ ! -f platform/aws-load-balancer-controller-values.yaml ]; then
                  echo "Missing platform/aws-load-balancer-controller-values.yaml"
                  exit 1
                fi

                helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
                  --namespace kube-system \
                  -f platform/aws-load-balancer-controller-values.yaml \
                  --set vpcId=$EKS_VPC_ID \
                  --set-string serviceAccount.annotations."eks\\.amazonaws\\.com/role-arn"="$ALB_CONTROLLER_ROLE_ARN"

                echo "Waiting for AWS Load Balancer Controller rollout..."
                kubectl rollout status deployment/aws-load-balancer-controller -n kube-system --timeout=300s

                echo "Validating AWS Load Balancer Controller pods..."
                kubectl get pods -n kube-system | grep aws-load-balancer-controller
                '''
            }
        }

        stage('Install Observability Stack') {
            steps {
                sh '''
                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Adding observability Helm repositories..."
                helm repo add prometheus-community https://prometheus-community.github.io/helm-charts || true
                helm repo add grafana https://grafana.github.io/helm-charts || true
                helm repo update

                if [ ! -f observability/kube-prometheus-stack-values.yaml ]; then
                  echo "Missing observability/kube-prometheus-stack-values.yaml"
                  exit 1
                fi

                helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
                  --namespace monitoring \
                  --create-namespace \
                  -f observability/kube-prometheus-stack-values.yaml

                if [ ! -f observability/grafana-image-renderer-values.yaml ]; then
                  echo "Missing observability/grafana-image-renderer-values.yaml"
                  exit 1
                fi

                echo "Installing/Updating Grafana Image Renderer..."
                kubectl apply -f observability/grafana-image-renderer-values.yaml

                echo "Waiting for Prometheus Operator rollout..."
                kubectl rollout status deployment/kube-prometheus-stack-operator -n monitoring --timeout=300s

                echo "Waiting for Grafana rollout..."
                kubectl rollout status deployment/kube-prometheus-stack-grafana -n monitoring --timeout=300s

                echo "Waiting for Grafana Image Renderer rollout..."
                kubectl rollout status deployment/grafana-image-renderer -n monitoring --timeout=300s

                echo "Waiting for Prometheus StatefulSet rollout..."
                kubectl rollout status statefulset/prometheus-kube-prometheus-stack-prometheus -n monitoring --timeout=300s

                echo "Validating observability services..."
                kubectl get svc -n monitoring
                '''
            }
        }

        stage('Decide Image Strategy') {
            steps {
                script {
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

                echo "Checking DU service availability from inside the Kubernetes cluster..."

                kubectl delete pod du-health-check --ignore-not-found=true > /dev/null 2>&1 || true

                kubectl run du-health-check \
                  --rm -i \
                  --restart=Never \
                  --image=curlimages/curl:8.10.1 \
                  --command -- sh -c 'curl -f http://du-service:8000/metrics'

                echo "DU service health check passed from inside the cluster."
                '''
            }
        }

        stage('Capture AIOps Run Start') {
            steps {
                sh '''
                rm -rf reports
                mkdir -p reports
                date +%s > reports/aiops_run_start_epoch.txt
                echo "AIOps run start epoch: $(cat reports/aiops_run_start_epoch.txt)"
                '''
            }
        }

        stage('Load Test') {
            steps {
                sh '''
                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Running load test from inside the Kubernetes cluster..."
                echo "This avoids kubectl port-forward and tests service-to-service traffic directly."

                kubectl delete pod ran-load-test --ignore-not-found=true > /dev/null 2>&1 || true

                cleanup() {
                  kubectl delete pod ran-load-test --ignore-not-found=true > /dev/null 2>&1 || true
                }
                trap cleanup EXIT

                echo "Creating temporary load-test pod..."
                kubectl run ran-load-test \
                  --restart=Never \
                  --image=curlimages/curl:8.10.1 \
                  --env="LOAD_PHASE_1_ROUNDS=$LOAD_PHASE_1_ROUNDS" \
                  --env="LOAD_PHASE_1_REQUESTS=$LOAD_PHASE_1_REQUESTS" \
                  --env="LOAD_PHASE_2_ROUNDS=$LOAD_PHASE_2_ROUNDS" \
                  --env="LOAD_PHASE_2_REQUESTS=$LOAD_PHASE_2_REQUESTS" \
                  --env="LOAD_PHASE_3_ROUNDS=$LOAD_PHASE_3_ROUNDS" \
                  --env="LOAD_PHASE_3_REQUESTS=$LOAD_PHASE_3_REQUESTS" \
                  --command -- sleep 3600

                echo "Waiting for load-test pod to become ready..."
                kubectl wait --for=condition=Ready pod/ran-load-test --timeout=120s

                echo "Executing load-test script inside the pod..."
                kubectl exec -i ran-load-test -- sh <<'LOADTEST'
set -eu

printf '%s\n' "Resetting metrics before load test..."
curl --fail-with-body -s -X POST http://du-service:8000/reset-metrics
curl --fail-with-body -s -X POST http://cu-service:8001/reset-metrics

sleep 2

printf '%s\n' "Sanity-checking one attach request before load test..."
SANITY_RESPONSE=$(curl --fail-with-body -s --connect-timeout 3 --max-time 10 \
  -X POST http://du-service:8000/attach \
  -H 'Content-Type: application/json' \
  --data '{"ue_id":"UE-SANITY"}')

printf '%s\n' "$SANITY_RESPONSE"

printf '%s\n' "Metrics after sanity attach:"
curl -s http://du-service:8000/metrics | grep -E 'total_rach_attempts|successful_rach|failed_rach|end_to_end_latency_samples' || true
curl -s http://cu-service:8001/metrics | grep -E 'total_requests|successful_attach|failed_attach|attach_latency_samples' || true

printf '%s\n' "Resetting metrics again before actual load test..."
curl --fail-with-body -s -X POST http://du-service:8000/reset-metrics
curl --fail-with-body -s -X POST http://cu-service:8001/reset-metrics

sleep 2

run_load_phase() {
  PHASE_NAME="$1"
  PHASE_ROUNDS="$2"
  PHASE_REQUESTS="$3"
  PHASE_COOLDOWN="$4"

  printf '%s\n' "Starting $PHASE_NAME: ${PHASE_ROUNDS} rounds x ${PHASE_REQUESTS} requests..."
  printf '%s\n' "HPA/replica snapshots are collected outside the load-test pod because the curl image intentionally does not include kubectl."

  for round in $(seq 1 "$PHASE_ROUNDS"); do
    GLOBAL_ROUND=$((GLOBAL_ROUND + 1))
    printf '%s\n' "Running $PHASE_NAME round $round/$PHASE_ROUNDS with $PHASE_REQUESTS parallel requests..."

    for i in $(seq 1 "$PHASE_REQUESTS"); do
      (
        UE_ID="${PHASE_NAME}-UE-${round}-${i}"
        PAYLOAD=$(printf '{"ue_id":"%s"}' "$UE_ID")

        curl --fail-with-body -s --connect-timeout 3 --max-time 10 \
          -X POST http://du-service:8000/attach \
          -H 'Content-Type: application/json' \
          --data "$PAYLOAD" > /tmp/curl-${GLOBAL_ROUND}-${i}.out 2>&1 \
        || {
          echo "FAILED" > /tmp/curl-${GLOBAL_ROUND}-${i}.status
          printf '%s\n' "Request failed for $UE_ID" >> /tmp/curl-${GLOBAL_ROUND}-${i}.out
        }
      ) &
    done

    wait

    ROUND_FAILURES=$(ls /tmp/curl-${GLOBAL_ROUND}-*.status 2>/dev/null | wc -l | tr -d ' ')
    FAILED_REQUESTS=$((FAILED_REQUESTS + ROUND_FAILURES))
    TOTAL_REQUESTS=$((TOTAL_REQUESTS + PHASE_REQUESTS))

    if [ "$ROUND_FAILURES" -gt 0 ]; then
      printf '%s\n' "$PHASE_NAME round $round had $ROUND_FAILURES failed curl executions. Failure details suppressed to keep Jenkins logs concise. Refer to the AIOps report for analysis."
    fi

    rm -f /tmp/curl-${GLOBAL_ROUND}-*.out /tmp/curl-${GLOBAL_ROUND}-*.status
    sleep 2
  done

  printf '%s\n' "Completed $PHASE_NAME."
  printf '%s\n' "Cooling down for ${PHASE_COOLDOWN}s after $PHASE_NAME to let HPA observe metrics..."
  sleep "$PHASE_COOLDOWN"
}

printf '%s\n' "Starting staged load test: low → medium → high pressure..."
TOTAL_REQUESTS=0
FAILED_REQUESTS=0
GLOBAL_ROUND=0

run_load_phase "phase1-low" "$LOAD_PHASE_1_ROUNDS" "$LOAD_PHASE_1_REQUESTS" 20
run_load_phase "phase2-medium" "$LOAD_PHASE_2_ROUNDS" "$LOAD_PHASE_2_REQUESTS" 25
run_load_phase "phase3-high" "$LOAD_PHASE_3_ROUNDS" "$LOAD_PHASE_3_REQUESTS" 30

printf '%s\n' "Total requests attempted by load generator: $TOTAL_REQUESTS"
printf '%s\n' "Failed curl executions: $FAILED_REQUESTS"
printf '%s\n' "Skipping direct DU/CU /metrics reads here because service-level /metrics can hit only one backend pod after HPA scaling."
printf '%s\n' "Aggregated KPI validation is performed in the next stage using Prometheus as the source of truth."

if [ "$FAILED_REQUESTS" -gt 0 ]; then
  printf '%s\n' "WARNING: One or more curl executions failed during load test."
  printf '%s\n' "Continuing to Metrics Validation so Prometheus and AIOps can analyze the run."
fi

printf '%s\n' "Load test traffic generation completed"
LOADTEST
                '''
            }
        }

        stage('Metrics Validation') {
            steps {
                sh '''
                echo "Starting KPI validation using Prometheus as the source of truth..."

                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Discovering Prometheus service..."
                PROM_NS=$(kubectl get svc -A | awk '$0 ~ /prometheus/ && $0 ~ /9090/ {print $1; exit}')
                PROM_SVC=$(kubectl get svc -A | awk '$0 ~ /prometheus/ && $0 ~ /9090/ {print $2; exit}')

                if [ -z "$PROM_NS" ] || [ -z "$PROM_SVC" ]; then
                  echo "Could not auto-discover Prometheus service exposing port 9090"
                  kubectl get svc -A | grep -i prometheus || true
                  exit 1
                fi

                echo "Using Prometheus service: $PROM_NS/$PROM_SVC"

                echo "Starting temporary port-forward for Prometheus API..."
                echo "Prometheus remains internal; Jenkins opens a short-lived local tunnel for KPI validation."
                kubectl -n "$PROM_NS" port-forward "service/$PROM_SVC" 19090:9090 > /dev/null 2>&1 &
                PROM_PF_PID=$!

                cleanup() {
                  kill $PROM_PF_PID 2>/dev/null || true
                }
                trap cleanup EXIT

                sleep 5

                echo "Waiting for Prometheus scrape interval to capture latest CU/DU metrics..."
                sleep 35

                mkdir -p reports
                date +%s > reports/aiops_run_end_epoch.txt
                echo "AIOps run end epoch: $(cat reports/aiops_run_end_epoch.txt)"

                query_prometheus() {
                  local promql="$1"
                  local label="$2"
                  local value=""

                  for attempt in 1 2 3; do
                    value=$(curl -sG "http://localhost:19090/api/v1/query" \
                      --data-urlencode "query=$promql" | jq -r '.data.result[0].value[1] // empty')

                    if [ -n "$value" ] && [ "$value" != "NaN" ] && [ "$value" != "+Inf" ] && [ "$value" != "-Inf" ]; then
                      echo "$value"
                      return 0
                    fi

                    echo "No valid Prometheus data yet for $label. Current value: ${value:-empty}. Retry $attempt/3..." >&2
                    sleep 10
                  done

                  echo "Failed to fetch Prometheus metric for $label" >&2
                  return 1
                }

                RACH_SR_QUERY='100 * sum(successful_rach{app="du", pipeline_run_id="'"$BUILD_NUMBER"'"}) / sum(total_rach_attempts{app="du", pipeline_run_id="'"$BUILD_NUMBER"'"})'
                ATTACH_SR_QUERY='100 * sum(successful_attach{app="cu", pipeline_run_id="'"$BUILD_NUMBER"'"}) / sum(total_requests{app="cu", pipeline_run_id="'"$BUILD_NUMBER"'"})'

                echo "PromQL query for RACH SR: $RACH_SR_QUERY"
                echo "PromQL query for Attach SR: $ATTACH_SR_QUERY"

                RACH_SR=$(query_prometheus "$RACH_SR_QUERY" "RACH SR")
                ATTACH_SR=$(query_prometheus "$ATTACH_SR_QUERY" "ATTACH SR")

                echo "$RACH_SR" | jq -e 'tonumber | numbers' > /dev/null 2>&1 || { echo "Invalid RACH SR value: $RACH_SR"; exit 1; }
                echo "$ATTACH_SR" | jq -e 'tonumber | numbers' > /dev/null 2>&1 || { echo "Invalid ATTACH SR value: $ATTACH_SR"; exit 1; }

                echo "KPI results from Prometheus:"
                echo "Configured thresholds: RACH=$RACH_THRESHOLD ATTACH=$ATTACH_THRESHOLD"
                printf "RACH SR   : %.2f%%\n" "$RACH_SR"
                printf "ATTACH SR : %.2f%%\n" "$ATTACH_SR"

                RACH_THRESHOLD="$RACH_THRESHOLD"
                ATTACH_THRESHOLD="$ATTACH_THRESHOLD"

                RACH_CHECK=$(echo "$RACH_SR < $RACH_THRESHOLD" | bc -l 2>/dev/null)
                ATTACH_CHECK=$(echo "$ATTACH_SR < $ATTACH_THRESHOLD" | bc -l 2>/dev/null)

                if [ -z "$RACH_CHECK" ]; then RACH_CHECK=1; fi
                if [ -z "$ATTACH_CHECK" ]; then ATTACH_CHECK=1; fi

                echo "KPI threshold decision:"

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

                echo "AIOps analysis window captured:"
                echo "RUN_ID=$BUILD_NUMBER"
                if [ ! -f reports/aiops_run_start_epoch.txt ]; then
                  echo "Missing reports/aiops_run_start_epoch.txt"
                  ls -lah reports || true
                  exit 1
                fi

                echo "START_EPOCH=$(cat reports/aiops_run_start_epoch.txt)"
                echo "END_EPOCH=$(cat reports/aiops_run_end_epoch.txt)"

                echo "Generating AIOps run analysis report..."
                python3 aiops/analyze_run.py \
                  --run-id "$BUILD_NUMBER" \
                  --start "$(cat reports/aiops_run_start_epoch.txt)" \
                  --end "$(cat reports/aiops_run_end_epoch.txt)" \
                  --output "reports/aiops_report_${BUILD_NUMBER}.txt"

                echo "AIOps report generated successfully: reports/aiops_report_${BUILD_NUMBER}.txt"

                if (( RACH_CHECK )) || (( ATTACH_CHECK )); then
                  echo "❌ KPI validation FAILED (RACH threshold: $RACH_THRESHOLD%, ATTACH threshold: $ATTACH_THRESHOLD%)"
                  echo "Final build result will be decided later from the AIOps report after evidence collection is complete."
                else
                  echo "✅ KPI validation PASSED (RACH threshold: $RACH_THRESHOLD%, ATTACH threshold: $ATTACH_THRESHOLD%)"
                  echo "Final build result will be decided later from the AIOps report after evidence collection is complete."
                fi
                '''
            }
        }
        stage('Capture Grafana Snapshots') {
            steps {
                sh '''
                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Capturing Grafana dashboard and summary snapshots using an ephemeral in-cluster curl pod..."
                echo "This avoids exposing Grafana publicly or using kubectl port-forward from Jenkins."

                mkdir -p reports

                GRAFANA_RENDER_POD="grafana-snapshot-client"
                GRAFANA_URL="http://kube-prometheus-stack-grafana.monitoring.svc.cluster.local"
                DASHBOARD_SNAPSHOT_FILE="reports/grafana_dashboard_${BUILD_NUMBER}.png"
                SECOND_SNAPSHOT_FILE="reports/grafana_replica_summary_${BUILD_NUMBER}.png"

                kubectl delete pod "$GRAFANA_RENDER_POD" --ignore-not-found=true > /dev/null 2>&1 || true

                cleanup() {
                  kubectl delete pod "$GRAFANA_RENDER_POD" --ignore-not-found=true > /dev/null 2>&1 || true
                }
                trap cleanup EXIT

                echo "Creating temporary Grafana snapshot client pod..."
                kubectl run "$GRAFANA_RENDER_POD" \
                  --restart=Never \
                  --image=curlimages/curl:8.10.1 \
                  --command -- sleep 3600

                echo "Waiting for Grafana snapshot client pod to become ready..."
                kubectl wait --for=condition=Ready pod/"$GRAFANA_RENDER_POD" --timeout=120s

                echo "Validating Grafana API health from inside the cluster..."
                kubectl exec "$GRAFANA_RENDER_POD" -- curl --fail-with-body -s -u admin:admin "${GRAFANA_URL}/api/health"

                echo "Discovering provisioned Grafana dashboard UID..."
                DASHBOARD_SEARCH_JSON=$(kubectl exec "$GRAFANA_RENDER_POD" -- curl --fail-with-body -s -u admin:admin "${GRAFANA_URL}/api/search?query=RAN")
                DASHBOARD_UID=$(printf '%s' "$DASHBOARD_SEARCH_JSON" | jq -r 'map(select(.title == "RAN Performance Dashboard")) | .[0].uid // empty')

                if [ -z "$DASHBOARD_UID" ]; then
                  echo "Unable to discover Grafana dashboard UID for title: RAN Performance Dashboard"
                  echo "Available dashboards returned by Grafana search API:"
                  printf '%s\n' "$DASHBOARD_SEARCH_JSON"
                  exit 1
                fi

                echo "Using Grafana dashboard UID: $DASHBOARD_UID"

                START_EPOCH=$(cat reports/aiops_run_start_epoch.txt)
                END_EPOCH=$(cat reports/aiops_run_end_epoch.txt)
                FROM_MS=$((START_EPOCH * 1000))
                TO_MS=$((END_EPOCH * 1000))

                # Main dashboard snapshot (full dashboard)
                DASHBOARD_RENDER_URL="${GRAFANA_URL}/render/d/${DASHBOARD_UID}/ran-performance-dashboard?orgId=1&from=${FROM_MS}&to=${TO_MS}&var-run_id=${BUILD_NUMBER}&width=1600&height=1700&tz=browser"
                # Summary/replica-aware container panel (panelId=48, adjust if needed)
                SUMMARY_RENDER_URL="${GRAFANA_URL}/render/d/${DASHBOARD_UID}/ran-performance-dashboard?orgId=1&from=${FROM_MS}&to=${TO_MS}&var-run_id=${BUILD_NUMBER}&width=1600&height=900&tz=browser&viewPanel=48"

                echo "Rendering Grafana dashboard snapshot for build ${BUILD_NUMBER}..."
                kubectl exec "$GRAFANA_RENDER_POD" -- curl --fail-with-body -s -u admin:admin --max-time 180 -o /tmp/grafana-dashboard.png "$DASHBOARD_RENDER_URL"

                echo "Rendering Grafana summary/replica panel snapshot for build ${BUILD_NUMBER}..."
                kubectl exec "$GRAFANA_RENDER_POD" -- curl --fail-with-body -s -u admin:admin --max-time 180 -o /tmp/grafana-summary.png "$SUMMARY_RENDER_URL"

                echo "Copying Grafana dashboard snapshot back to Jenkins reports directory..."
                kubectl exec "$GRAFANA_RENDER_POD" -- cat /tmp/grafana-dashboard.png > "$DASHBOARD_SNAPSHOT_FILE"

                echo "Copying Grafana summary/replica snapshot back to Jenkins reports directory..."
                kubectl exec "$GRAFANA_RENDER_POD" -- cat /tmp/grafana-summary.png > "$SECOND_SNAPSHOT_FILE"

                if [ ! -s "$DASHBOARD_SNAPSHOT_FILE" ]; then
                  echo "Grafana dashboard snapshot file was not created or is empty: $DASHBOARD_SNAPSHOT_FILE"
                  exit 1
                fi
                if [ ! -s "$SECOND_SNAPSHOT_FILE" ]; then
                  echo "Grafana summary/replica snapshot file was not created or is empty: $SECOND_SNAPSHOT_FILE"
                  exit 1
                fi

                echo "Grafana dashboard and summary snapshots generated successfully:"
                ls -lh "$DASHBOARD_SNAPSHOT_FILE" "$SECOND_SNAPSHOT_FILE"
                '''
            }
        }

        stage('Finalize Pipeline Result') {
            steps {
                script {
                    def aiopsReport = readFile("reports/aiops_report_${env.BUILD_NUMBER}.txt")
                    if (!aiopsReport.contains('Overall verdict        : PASS')) {
                        error('AIOps overall verdict is not PASS. Build marked as FAILURE after evidence collection.')
                    }
                    echo 'AIOps overall verdict is PASS. Build marked SUCCESS.'
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: "reports/*${env.BUILD_NUMBER}*", allowEmptyArchive: true
            archiveArtifacts artifacts: 'reports/aiops_run_start_epoch.txt,reports/aiops_run_end_epoch.txt', allowEmptyArchive: true
        }
    }
}