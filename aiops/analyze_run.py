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


def safe_percent(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """Safely calculate percentage."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100


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

    rach_threshold = 75.0
    attach_threshold = 79.5

    pass_rach = du_sr is not None and du_sr >= rach_threshold
    pass_attach = cu_sr is not None and cu_sr >= attach_threshold
    pass_scaling = (du_max_replicas or 0) >= 2 and (cu_max_replicas or 0) >= 2
    overall_pass = pass_rach and pass_attach and pass_scaling

    report_lines = [
        "=" * 72,
        f"RAN AIOps Run Analysis Report - Build #{run_id}",
        "=" * 72,
        f"Analysis window: {start} → {end} epoch seconds ({duration_seconds}s)",
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
        f"RACH threshold check   : {'PASS' if pass_rach else 'FAIL'} ({fmt_number(du_sr)}% >= {rach_threshold}%)",
        f"Attach threshold check : {'PASS' if pass_attach else 'FAIL'} ({fmt_number(cu_sr)}% >= {attach_threshold}%)",
        f"HPA scaling check      : {'PASS' if pass_scaling else 'FAIL'} (CU max={fmt_int(cu_max_replicas)}, DU max={fmt_int(du_max_replicas)})",
        "",
        f"Overall verdict        : {'PASS' if overall_pass else 'FAIL'}",
        "",
        "Interpretation",
        "-" * 72,
    ]

    if overall_pass:
        report_lines.append("The run passed the core telecom KPI gates and HPA reacted to load by scaling the CU/DU workloads.")
    else:
        report_lines.append("The run needs investigation because one or more KPI/scaling checks failed.")

    if du_total and cu_total and du_total > cu_total:
        report_lines.append("DU attempts are higher than CU requests, which is expected when some RACH attempts fail before reaching CU attach processing.")

    if du_peak_cpu_util and du_peak_cpu_util > 100:
        report_lines.append("DU CPU utilization exceeded 100% of requested CPU, indicating the DU pod was under strong CPU pressure before/while HPA scaled.")

    if cu_peak_cpu_util and cu_peak_cpu_util > 100:
        report_lines.append("CU CPU utilization exceeded 100% of requested CPU, indicating the CU pod was under strong CPU pressure before/while HPA scaled.")

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