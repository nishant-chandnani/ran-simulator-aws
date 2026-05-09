        stage('Metrics Validation') {
            steps {
                sh '''
                echo "\n===== KPI VALIDATION ====="

                export KUBECONFIG="$KUBECONFIG_PATH"

                echo "Discovering Prometheus service..."
                PROM_NS=$(kubectl get svc -A | awk '$3 ~ /ClusterIP/ && $0 ~ /prometheus/ && $0 ~ /9090/ {print $1; exit}')
                PROM_SVC=$(kubectl get svc -A | awk '$3 ~ /ClusterIP/ && $0 ~ /prometheus/ && $0 ~ /9090/ {print $2; exit}')

                if [ -z "$PROM_NS" ] || [ -z "$PROM_SVC" ]; then
                  echo "Could not auto-discover Prometheus service exposing port 9090"
                  kubectl get svc -A | grep -i prometheus || true
                  exit 1
                fi

                echo "Using Prometheus service: $PROM_NS/$PROM_SVC"

                echo "Starting port-forward for Prometheus..."
                kubectl -n "$PROM_NS" port-forward "service/$PROM_SVC" 19090:9090 > /dev/null 2>&1 &
                PROM_PF_PID=$!

                cleanup() {
                  kill $PROM_PF_PID 2>/dev/null || true
                }
                trap cleanup EXIT

                sleep 5

                echo "Waiting for Prometheus to scrape latest CU/DU metrics..."
                sleep 35

                query_prometheus() {
                  local promql="$1"
                  local label="$2"
                  local value=""

                  for attempt in 1 2 3; do
                    value=$(curl -sG "http://localhost:19090/api/v1/query" \
                      --data-urlencode "query=$promql" | jq -r '.data.result[0].value[1] // empty')

                    if [ -n "$value" ]; then
                      echo "$value"
                      return 0
                    fi

                    echo "No Prometheus data yet for $label. Retry $attempt/3..." >&2
                    sleep 10
                  done

                  echo "Failed to fetch Prometheus metric for $label" >&2
                  return 1
                }

                RACH_SR_QUERY='100 * sum(successful_rach{app="du", pipeline_run_id="'"$BUILD_NUMBER"'"}) / sum(total_rach_attempts{app="du", pipeline_run_id="'"$BUILD_NUMBER"'"})'
                ATTACH_SR_QUERY='100 * sum(successful_attach{app="cu", pipeline_run_id="'"$BUILD_NUMBER"'"}) / sum(total_requests{app="cu", pipeline_run_id="'"$BUILD_NUMBER"'"})'

                echo "\nPromQL - RACH SR:"
                echo "$RACH_SR_QUERY"
                echo "\nPromQL - ATTACH SR:"
                echo "$ATTACH_SR_QUERY"

                RACH_SR=$(query_prometheus "$RACH_SR_QUERY" "RACH SR")
                ATTACH_SR=$(query_prometheus "$ATTACH_SR_QUERY" "ATTACH SR")

                # Ensure numeric using jq
                echo "$RACH_SR" | jq -e 'tonumber | numbers' > /dev/null 2>&1 || { echo "Invalid RACH SR value: $RACH_SR"; exit 1; }
                echo "$ATTACH_SR" | jq -e 'tonumber | numbers' > /dev/null 2>&1 || { echo "Invalid ATTACH SR value: $ATTACH_SR"; exit 1; }

                echo "\n===== KPI RESULTS ====="
                printf "RACH SR   : %.2f\n" "$RACH_SR"
                printf "ATTACH SR : %.2f\n" "$ATTACH_SR"

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