#!/usr/bin/env python3
"""
AIOps Run Analyzer for the RAN Simulator project.

This script analyzes one Jenkins pipeline run using Prometheus.

Clean design:
- pipeline_run_id tells us WHICH build/run to analyze.
- start/end epoch timestamps tell us the exact build window.
- Prometheus performs historical aggregation using max_over_time().

Why max_over_time()?
During HPA scale-up, multiple CU/DU pods may exist. Later, HPA may scale back down
and some pod time series disappear from instant queries. max_over_time() over the
build window captures the highest counter value reached by each pod during that
run, then sum(...) aggregates those pod-level maxima.
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


PROMETHEUS_DEFAULT_URL = "http://localhost:19090"

DEFAULT_HPA_TARGET_CPU_PERCENT = 60.0
DEFAULT_HPA_MIN_REPLICAS = 1.0
DEFAULT_HPA_MAX_REPLICAS = 3.0
RACH_SR_THRESHOLD = 75.0
ATTACH_SR_THRESHOLD = 79.5


def parse_args() -> argparse.Namespace:
    """Read command-line inputs passed to the script."""
    parser = argparse.ArgumentParser(description="Analyze one RAN Simulator Jenkins run using Prometheus metrics.")
    parser.add_argument("--prom-url", default=PROMETHEUS_DEFAULT_URL, help="Prometheus base URL. Default: http://localhost:19090")
    parser.add_argument("--run-id", required=True, help="Jenkins build number / pipeline_run_id to analyze")
    parser.add_argument("--start", required=True, type=int, help="Run start time as Unix epoch seconds")
    parser.add_argument("--end", required=True, type=int, help="Run end time as Unix epoch seconds")
    parser.add_argument("--output", default=None, help="Optional output report file path")
    parser.add_argument("--debug", action="store_true", help="Print PromQL queries before sending them to Prometheus")
    return parser.parse_args()


def prometheus_query(prom_url: str, promql: str, query_time: int) -> Optional[float]:
    """Call Prometheus /api/v1/query at a specific time and return one numeric value."""
    query_params = urllib.parse.urlencode({"query": promql, "time": query_time})
    url = f"{prom_url.rstrip('/')}/api/v1/query?{query_params}"

    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = response.read().decode("utf-8")
            data = json.loads(payload)
    except Exception as exc:
        raise RuntimeError(f"Failed to query Prometheus for: {promql}\nError: {exc}") from exc

    if data.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed for: {promql}\nResponse: {data}")

    result = data.get("data", {}).get("result", [])
    if not result:
        return None

    raw_value = result[0].get("value", [None, None])[1]
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


def fmt_number(value: Optional[float], decimals: int = 2) -> str:
    """Format a number safely for report output."""
    if value is None:
        return "N/A"
    return f"{value:.{decimals}f}"


def fmt_int(value: Optional[float]) -> str:
    """Format a float as an integer-looking value."""
    if value is None:
        return "N/A"
    return str(int(round(value)))


def value_or_default(value: Optional[float], default: float) -> float:
    """Use a live Prometheus value when available, otherwise fall back to a safe default."""
    return value if value is not None else default


def safe_percent(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """Safely calculate percentage."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100


def evaluate_hpa_behavior(
    component: str,
    peak_cpu_util: Optional[float],
    observed_max_replicas: Optional[float],
    hpa_target_cpu: Optional[float],
    hpa_min_replicas: Optional[float],
    hpa_max_replicas: Optional[float],
) -> tuple[bool, str]:
    """
    Evaluate whether HPA behavior matched observed CPU pressure and live HPA configuration.

    Expected behavior:
    - If CPU crosses the HPA target and HPA maxReplicas allows scaling, replicas should rise above minReplicas.
    - If CPU stays below the HPA target and replicas stay at minReplicas, the workload behaved normally.
    - If CPU crosses the target but maxReplicas equals minReplicas, no scaling is possible by design.
    - If replicas increased but the run-level observed peak CPU is below target, do not fail automatically.
      HPA may have reacted to a short earlier spike, then additional replicas diluted later average utilization.
      This report treats that case as an explainable scaling event, not a failed HPA behavior.
    """
    if peak_cpu_util is None or observed_max_replicas is None:
        return False, f"{component}: insufficient data to evaluate HPA behavior"

    target = value_or_default(hpa_target_cpu, DEFAULT_HPA_TARGET_CPU_PERCENT)
    min_replicas = value_or_default(hpa_min_replicas, DEFAULT_HPA_MIN_REPLICAS)
    max_replicas = value_or_default(hpa_max_replicas, DEFAULT_HPA_MAX_REPLICAS)

    scaled = observed_max_replicas > min_replicas
    cpu_crossed_target = peak_cpu_util >= target
    scaling_allowed = max_replicas > min_replicas

    if cpu_crossed_target and scaled:
        return True, (
            f"{component}: PASS - CPU crossed HPA target "
            f"({peak_cpu_util:.2f}% >= {target:.1f}%) and workload scaled to {fmt_int(observed_max_replicas)} replicas"
        )

    if not cpu_crossed_target and not scaled:
        return True, (
            f"{component}: PASS - CPU stayed below HPA target "
            f"({peak_cpu_util:.2f}% < {target:.1f}%) and workload correctly remained at {fmt_int(observed_max_replicas)} replica"
        )

    if cpu_crossed_target and not scaled and not scaling_allowed:
        return True, (
            f"{component}: PASS - CPU crossed HPA target "
            f"({peak_cpu_util:.2f}% >= {target:.1f}%), but HPA maxReplicas={fmt_int(max_replicas)} "
            f"equals minReplicas={fmt_int(min_replicas)}, so no scaling was possible by design"
        )

    if cpu_crossed_target and not scaled:
        return False, (
            f"{component}: FAIL - CPU crossed HPA target "
            f"({peak_cpu_util:.2f}% >= {target:.1f}%) but workload did not scale beyond {fmt_int(observed_max_replicas)} replica "
            f"even though HPA maxReplicas={fmt_int(max_replicas)} allowed scaling"
        )

    return True, (
        f"{component}: CAUTION - run-level observed peak CPU stayed below HPA target "
        f"({peak_cpu_util:.2f}% < {target:.1f}%), but workload scaled to {fmt_int(observed_max_replicas)} replicas. "
        f"This can happen when HPA reacts to a short earlier spike and later extra replicas dilute average CPU utilization; "
        f"treat this as an explainable scaling event rather than an HPA failure."
    )


# ---------------------------------------------------------------------
# Scaling pattern classification and interpretation functions
# ---------------------------------------------------------------------

def classify_scaling_pattern(
    du_max_replicas: Optional[float],
    cu_max_replicas: Optional[float],
    du_min_replicas: Optional[float],
    cu_min_replicas: Optional[float],
) -> str:
    """Classify which part of the simulated RAN workload scaled during the run."""
    du_min = value_or_default(du_min_replicas, DEFAULT_HPA_MIN_REPLICAS)
    cu_min = value_or_default(cu_min_replicas, DEFAULT_HPA_MIN_REPLICAS)

    du_scaled = du_max_replicas is not None and du_max_replicas > du_min
    cu_scaled = cu_max_replicas is not None and cu_max_replicas > cu_min

    if du_scaled and cu_scaled:
        return "DU and CU scaled"
    if du_scaled and not cu_scaled:
        return "DU-only scaling"
    if cu_scaled and not du_scaled:
        return "CU-only scaling"
    return "No scaling"


def build_scaling_interpretation(
    scaling_pattern: str,
    du_peak_cpu_util: Optional[float],
    cu_peak_cpu_util: Optional[float],
    du_max_latency: Optional[float],
    cu_max_latency: Optional[float],
) -> list[str]:
    """
    Build cautious AIOps interpretation text.

    Important: this function does not claim that scaling improved latency.
    We only claim that HPA behavior matched CPU pressure and identify where
    the pressure appeared during the run.
    """
    lines: list[str] = []

    if scaling_pattern == "No scaling":
        lines.append(
            "Scaling pattern: No scaling was observed. This can be healthy when CPU stays below target, or expected when HPA maxReplicas equals minReplicas for a controlled single-replica experiment."
        )
    elif scaling_pattern == "DU-only scaling":
        lines.append(
            "Scaling pattern: DU-only scaling was observed. This indicates DU-side CPU pressure while CU remained comparatively comfortable."
        )
    elif scaling_pattern == "CU-only scaling":
        lines.append(
            "Scaling pattern: CU-only scaling was observed. This indicates CU-side CPU pressure while DU remained comparatively comfortable."
        )
    elif scaling_pattern == "DU and CU scaled":
        lines.append(
            "Scaling pattern: Both DU and CU scaled. This indicates end-to-end load pressure across both simulated RAN components."
        )

    if du_peak_cpu_util is not None and cu_peak_cpu_util is not None:
        if du_peak_cpu_util > cu_peak_cpu_util * 1.5:
            lines.append("CPU pressure was DU-dominant during this run.")
        elif cu_peak_cpu_util > du_peak_cpu_util * 1.5:
            lines.append("CPU pressure was CU-dominant during this run.")
        else:
            lines.append("CPU pressure was broadly distributed across CU and DU during this run.")

    lines.append(
        "HPA interpretation is based on run-level Prometheus aggregates. If replicas increased while observed peak CPU appears below target, the most likely explanation is timing: HPA may have sampled a short pressure spike before the final run-level aggregate settled lower after scale-out."
    )

    if du_max_latency is not None and du_max_latency >= 1000:
        lines.append(
            "DU max latency crossed 1000 ms. Treat this as a latency stress signal for DU-side processing, not automatic proof that HPA improved or worsened latency."
        )

    if cu_max_latency is not None and cu_max_latency >= 1000:
        lines.append(
            "CU max latency crossed 1000 ms. Treat this as a latency stress signal for CU-side processing, not automatic proof that HPA improved or worsened latency."
        )

    lines.append(
        "Latency benefit is not asserted in this report because the current test captures run-level maxima and averages, not before-vs-after scaling latency recovery."
    )

    return lines


def build_report(args: argparse.Namespace) -> str:
    """Query Prometheus and build a human-readable AIOps report."""
    run_id = args.run_id
    prom_url = args.prom_url
    start = args.start
    end = args.end
    duration_seconds = end - start
    range_window = f"{duration_seconds}s"

    def q(promql: str) -> Optional[float]:
        if args.debug:
            print(f"\nDEBUG PromQL @ {end}:\n{promql}\n", file=sys.stderr)
        return prometheus_query(prom_url, promql, end)

    # ---------------------------------------------------------------------
    # App metrics: use sum(max_over_time(...[build_window]))
    # This matches the dashboard logic and handles scaled-down pods correctly.
    # ---------------------------------------------------------------------
    du_total = q(f'sum(max_over_time(total_rach_attempts{{app="du",pipeline_run_id="{run_id}"}}[{range_window}]))')
    du_success = q(f'sum(max_over_time(successful_rach{{app="du",pipeline_run_id="{run_id}"}}[{range_window}]))')
    du_failed = q(f'sum(max_over_time(failed_rach{{app="du",pipeline_run_id="{run_id}"}}[{range_window}]))')
    du_samples = q(f'sum(max_over_time(end_to_end_latency_samples{{app="du",pipeline_run_id="{run_id}"}}[{range_window}]))')

    cu_total = q(f'sum(max_over_time(total_requests{{app="cu",pipeline_run_id="{run_id}"}}[{range_window}]))')
    cu_success = q(f'sum(max_over_time(successful_attach{{app="cu",pipeline_run_id="{run_id}"}}[{range_window}]))')
    cu_failed = q(f'sum(max_over_time(failed_attach{{app="cu",pipeline_run_id="{run_id}"}}[{range_window}]))')
    cu_samples = q(f'sum(max_over_time(attach_latency_samples{{app="cu",pipeline_run_id="{run_id}"}}[{range_window}]))')

    # Weighted average latency across pods.
    # This avoids a simple average of pod averages.
    du_avg_latency = q(
        f'sum(max_over_time(avg_end_to_end_latency_ms{{app="du",pipeline_run_id="{run_id}"}}[{range_window}]) '
        f'* max_over_time(end_to_end_latency_samples{{app="du",pipeline_run_id="{run_id}"}}[{range_window}])) '
        f'/ sum(max_over_time(end_to_end_latency_samples{{app="du",pipeline_run_id="{run_id}"}}[{range_window}]))'
    )
    cu_avg_latency = q(
        f'sum(max_over_time(avg_attach_latency_ms{{app="cu",pipeline_run_id="{run_id}"}}[{range_window}]) '
        f'* max_over_time(attach_latency_samples{{app="cu",pipeline_run_id="{run_id}"}}[{range_window}])) '
        f'/ sum(max_over_time(attach_latency_samples{{app="cu",pipeline_run_id="{run_id}"}}[{range_window}]))'
    )

    du_max_latency = q(f'max(max_over_time(max_end_to_end_latency_ms{{app="du",pipeline_run_id="{run_id}"}}[{range_window}]))')
    cu_max_latency = q(f'max(max_over_time(max_attach_latency_ms{{app="cu",pipeline_run_id="{run_id}"}}[{range_window}]))')

    # ---------------------------------------------------------------------
    # HPA metrics: use HPA label join to keep the selected run_id.
    # ---------------------------------------------------------------------
    du_hpa_series = (
        f'kube_horizontalpodautoscaler_status_current_replicas{{namespace="default",horizontalpodautoscaler="du-hpa"}} '
        f'* on(namespace,horizontalpodautoscaler) group_left(label_pipeline_run_id) '
        f'kube_horizontalpodautoscaler_labels{{namespace="default",horizontalpodautoscaler="du-hpa",label_pipeline_run_id="{run_id}"}}'
    )
    cu_hpa_series = (
        f'kube_horizontalpodautoscaler_status_current_replicas{{namespace="default",horizontalpodautoscaler="cu-hpa"}} '
        f'* on(namespace,horizontalpodautoscaler) group_left(label_pipeline_run_id) '
        f'kube_horizontalpodautoscaler_labels{{namespace="default",horizontalpodautoscaler="cu-hpa",label_pipeline_run_id="{run_id}"}}'
    )

    du_max_replicas = q(f'max(max_over_time(({du_hpa_series})[{range_window}:]))')
    cu_max_replicas = q(f'max(max_over_time(({cu_hpa_series})[{range_window}:]))')

    du_hpa_min_replicas = q(
        f'max(max_over_time((kube_horizontalpodautoscaler_spec_min_replicas{{namespace="default",horizontalpodautoscaler="du-hpa"}} '
        f'* on(namespace,horizontalpodautoscaler) group_left(label_pipeline_run_id) '
        f'kube_horizontalpodautoscaler_labels{{namespace="default",horizontalpodautoscaler="du-hpa",label_pipeline_run_id="{run_id}"}})[{range_window}:]))'
    )
    cu_hpa_min_replicas = q(
        f'max(max_over_time((kube_horizontalpodautoscaler_spec_min_replicas{{namespace="default",horizontalpodautoscaler="cu-hpa"}} '
        f'* on(namespace,horizontalpodautoscaler) group_left(label_pipeline_run_id) '
        f'kube_horizontalpodautoscaler_labels{{namespace="default",horizontalpodautoscaler="cu-hpa",label_pipeline_run_id="{run_id}"}})[{range_window}:]))'
    )
    du_hpa_max_replicas = q(
        f'max(max_over_time((kube_horizontalpodautoscaler_spec_max_replicas{{namespace="default",horizontalpodautoscaler="du-hpa"}} '
        f'* on(namespace,horizontalpodautoscaler) group_left(label_pipeline_run_id) '
        f'kube_horizontalpodautoscaler_labels{{namespace="default",horizontalpodautoscaler="du-hpa",label_pipeline_run_id="{run_id}"}})[{range_window}:]))'
    )
    cu_hpa_max_replicas = q(
        f'max(max_over_time((kube_horizontalpodautoscaler_spec_max_replicas{{namespace="default",horizontalpodautoscaler="cu-hpa"}} '
        f'* on(namespace,horizontalpodautoscaler) group_left(label_pipeline_run_id) '
        f'kube_horizontalpodautoscaler_labels{{namespace="default",horizontalpodautoscaler="cu-hpa",label_pipeline_run_id="{run_id}"}})[{range_window}:]))'
    )
    du_hpa_target_cpu = q(
        f'max(max_over_time((kube_horizontalpodautoscaler_spec_target_metric{{namespace="default",horizontalpodautoscaler="du-hpa",metric_name="cpu",metric_target_type="utilization"}} '
        f'* on(namespace,horizontalpodautoscaler) group_left(label_pipeline_run_id) '
        f'kube_horizontalpodautoscaler_labels{{namespace="default",horizontalpodautoscaler="du-hpa",label_pipeline_run_id="{run_id}"}})[{range_window}:]))'
    )
    cu_hpa_target_cpu = q(
        f'max(max_over_time((kube_horizontalpodautoscaler_spec_target_metric{{namespace="default",horizontalpodautoscaler="cu-hpa",metric_name="cpu",metric_target_type="utilization"}} '
        f'* on(namespace,horizontalpodautoscaler) group_left(label_pipeline_run_id) '
        f'kube_horizontalpodautoscaler_labels{{namespace="default",horizontalpodautoscaler="cu-hpa",label_pipeline_run_id="{run_id}"}})[{range_window}:]))'
    )

    # ---------------------------------------------------------------------
    # CPU metrics: same PromQL design as Grafana CPU panels.
    # ---------------------------------------------------------------------
    du_cpu_mcpu_series = (
        f'1000 * sum(rate(container_cpu_usage_seconds_total{{namespace="default",pod=~"du-deployment-.*",container!="",container!="POD"}}[1m]) '
        f'* on(namespace,pod) group_left(label_pipeline_run_id) '
        f'kube_pod_labels{{namespace="default",label_app="du",label_pipeline_run_id="{run_id}"}})'
    )
    cu_cpu_mcpu_series = (
        f'1000 * sum(rate(container_cpu_usage_seconds_total{{namespace="default",pod=~"cu-deployment-.*",container!="",container!="POD"}}[1m]) '
        f'* on(namespace,pod) group_left(label_pipeline_run_id) '
        f'kube_pod_labels{{namespace="default",label_app="cu",label_pipeline_run_id="{run_id}"}})'
    )

    du_peak_cpu_mcpu = q(f'max_over_time(({du_cpu_mcpu_series})[{range_window}:])')
    cu_peak_cpu_mcpu = q(f'max_over_time(({cu_cpu_mcpu_series})[{range_window}:])')

    du_cpu_util_series = (
        f'100 * sum(rate(container_cpu_usage_seconds_total{{namespace="default",pod=~"du-deployment-.*",container!="",container!="POD"}}[1m]) '
        f'* on(namespace,pod) group_left(label_pipeline_run_id) '
        f'kube_pod_labels{{namespace="default",label_app="du",label_pipeline_run_id="{run_id}"}}) '
        f'/ sum(kube_pod_container_resource_requests{{namespace="default",pod=~"du-deployment-.*",container!="",resource="cpu"}} '
        f'* on(namespace,pod) group_left(label_pipeline_run_id) '
        f'kube_pod_labels{{namespace="default",label_app="du",label_pipeline_run_id="{run_id}"}})'
    )
    cu_cpu_util_series = (
        f'100 * sum(rate(container_cpu_usage_seconds_total{{namespace="default",pod=~"cu-deployment-.*",container!="",container!="POD"}}[1m]) '
        f'* on(namespace,pod) group_left(label_pipeline_run_id) '
        f'kube_pod_labels{{namespace="default",label_app="cu",label_pipeline_run_id="{run_id}"}}) '
        f'/ sum(kube_pod_container_resource_requests{{namespace="default",pod=~"cu-deployment-.*",container!="",resource="cpu"}} '
        f'* on(namespace,pod) group_left(label_pipeline_run_id) '
        f'kube_pod_labels{{namespace="default",label_app="cu",label_pipeline_run_id="{run_id}"}})'
    )

    du_peak_cpu_util = q(f'max_over_time(({du_cpu_util_series})[{range_window}:])')
    cu_peak_cpu_util = q(f'max_over_time(({cu_cpu_util_series})[{range_window}:])')

    du_sr = safe_percent(du_success, du_total)
    cu_sr = safe_percent(cu_success, cu_total)

    pass_rach = du_sr is not None and du_sr >= RACH_SR_THRESHOLD
    pass_attach = cu_sr is not None and cu_sr >= ATTACH_SR_THRESHOLD

    pass_du_hpa, du_hpa_message = evaluate_hpa_behavior(
        "DU",
        du_peak_cpu_util,
        du_max_replicas,
        du_hpa_target_cpu,
        du_hpa_min_replicas,
        du_hpa_max_replicas,
    )
    pass_cu_hpa, cu_hpa_message = evaluate_hpa_behavior(
        "CU",
        cu_peak_cpu_util,
        cu_max_replicas,
        cu_hpa_target_cpu,
        cu_hpa_min_replicas,
        cu_hpa_max_replicas,
    )
    pass_scaling = pass_du_hpa and pass_cu_hpa
    scaling_pattern = classify_scaling_pattern(
        du_max_replicas,
        cu_max_replicas,
        du_hpa_min_replicas,
        cu_hpa_min_replicas,
    )
    scaling_interpretation = build_scaling_interpretation(
        scaling_pattern,
        du_peak_cpu_util,
        cu_peak_cpu_util,
        du_max_latency,
        cu_max_latency,
    )

    overall_pass = pass_rach and pass_attach and pass_scaling

    report_lines = [
        "=" * 72,
        f"RAN AIOps Run Analysis Report - Build #{run_id}",
        "=" * 72,
        f"Analysis window: {start} → {end} epoch seconds ({duration_seconds}s)",
        "",
        "Scaling Configuration",
        "-" * 72,
        f"DU HPA target CPU      : {fmt_number(value_or_default(du_hpa_target_cpu, DEFAULT_HPA_TARGET_CPU_PERCENT))}%",
        f"DU HPA min replicas    : {fmt_int(value_or_default(du_hpa_min_replicas, DEFAULT_HPA_MIN_REPLICAS))}",
        f"DU HPA max replicas    : {fmt_int(value_or_default(du_hpa_max_replicas, DEFAULT_HPA_MAX_REPLICAS))}",
        f"CU HPA target CPU      : {fmt_number(value_or_default(cu_hpa_target_cpu, DEFAULT_HPA_TARGET_CPU_PERCENT))}%",
        f"CU HPA min replicas    : {fmt_int(value_or_default(cu_hpa_min_replicas, DEFAULT_HPA_MIN_REPLICAS))}",
        f"CU HPA max replicas    : {fmt_int(value_or_default(cu_hpa_max_replicas, DEFAULT_HPA_MAX_REPLICAS))}",
        "",
        "DU / RACH Summary",
        "-" * 72,
        f"Observed RACH attempts : {fmt_int(du_total)}",
        f"Successful RACH        : {fmt_int(du_success)}",
        f"Failed RACH            : {fmt_int(du_failed)}",
        f"RACH SR                : {fmt_number(du_sr)}%",
        f"Latency samples        : {fmt_int(du_samples)}",
        f"Avg E2E latency        : {fmt_number(du_avg_latency)} ms",
        f"Max E2E latency        : {fmt_number(du_max_latency)} ms",
        f"Peak DU CPU            : {fmt_number(du_peak_cpu_mcpu)} mCPU",
        f"Peak DU CPU utilization: {fmt_number(du_peak_cpu_util)}%",
        f"Max DU replicas        : {fmt_int(du_max_replicas)}",
        "",
        "CU / Attach Summary",
        "-" * 72,
        f"Observed CU requests   : {fmt_int(cu_total)}",
        f"Successful attach      : {fmt_int(cu_success)}",
        f"Failed attach          : {fmt_int(cu_failed)}",
        f"Attach SR              : {fmt_number(cu_sr)}%",
        f"Latency samples        : {fmt_int(cu_samples)}",
        f"Avg attach latency     : {fmt_number(cu_avg_latency)} ms",
        f"Max attach latency     : {fmt_number(cu_max_latency)} ms",
        f"Peak CU CPU            : {fmt_number(cu_peak_cpu_mcpu)} mCPU",
        f"Peak CU CPU utilization: {fmt_number(cu_peak_cpu_util)}%",
        f"Max CU replicas        : {fmt_int(cu_max_replicas)}",
        "",
        "AIOps Assessment",
        "-" * 72,
        f"RACH threshold check   : {'PASS' if pass_rach else 'FAIL'} ({fmt_number(du_sr)}% >= {RACH_SR_THRESHOLD}%)",
        f"Attach threshold check : {'PASS' if pass_attach else 'FAIL'} ({fmt_number(cu_sr)}% >= {ATTACH_SR_THRESHOLD}%)",
        f"HPA behavior check     : {'PASS' if pass_scaling else 'FAIL'}",
        f"  - {du_hpa_message}",
        f"  - {cu_hpa_message}",
        f"Scaling pattern        : {scaling_pattern}",
        "",
        f"Overall verdict        : {'PASS' if overall_pass else 'FAIL'}",
        "",
        "Interpretation",
        "-" * 72,
    ]

    if overall_pass:
        report_lines.append("The run passed the core telecom KPI gates and HPA behavior was explainable from observed scaling and CPU pressure.")
    else:
        report_lines.append("The run needs investigation because one or more KPI checks failed or HPA did not scale when clear CPU pressure was observed.")

    if du_total and cu_total and du_total > cu_total:
        report_lines.append("DU RACH attempts are higher than CU attach requests, which is expected when some RACH attempts fail at DU before progressing to CU attach processing.")

    if du_peak_cpu_util and du_peak_cpu_util > 100:
        report_lines.append("DU CPU utilization exceeded 100% of requested CPU, indicating strong DU-side CPU pressure during the run.")

    if cu_peak_cpu_util and cu_peak_cpu_util > 100:
        report_lines.append("CU CPU utilization exceeded 100% of requested CPU, indicating strong CU-side CPU pressure during the run.")

    report_lines.append("")
    report_lines.append("Scaling Interpretation")
    report_lines.append("-" * 72)
    report_lines.extend(scaling_interpretation)
    report_lines.append("=" * 72)

    return "\n".join(report_lines)


def main() -> int:
    args = parse_args()

    if args.end <= args.start:
        print("ERROR: --end must be greater than --start", file=sys.stderr)
        return 1

    report = build_report(args)
    print(report)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report + "\n", encoding="utf-8")
        print(f"\nReport written to: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())