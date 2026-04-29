from fastapi import FastAPI
from pydantic import BaseModel
import time
import random
import logging
from fastapi import Response

app = FastAPI()

# Setup logging
logging.basicConfig(level=logging.INFO)

# KPI counters
total_requests = 0
successful_attach = 0
failed_attach = 0
min_attach_latency_ms = None
max_attach_latency_ms = 0.0
total_attach_latency_ms = 0.0
attach_latency_samples = 0

#adding a harmless comment

class UERequest(BaseModel):
    ue_id: str

@app.post("/attach")
def attach(req: UERequest):
    global total_requests, successful_attach, failed_attach
    global min_attach_latency_ms, max_attach_latency_ms
    global total_attach_latency_ms, attach_latency_samples

    total_requests += 1

    processing_time = random.uniform(0.1, 0.5)
    time.sleep(processing_time)
    latency_ms = round(processing_time * 1000, 2)
    min_attach_latency_ms = latency_ms if min_attach_latency_ms is None else min(min_attach_latency_ms, latency_ms)
    max_attach_latency_ms = max(max_attach_latency_ms, latency_ms)
    total_attach_latency_ms += latency_ms
    attach_latency_samples += 1

    # Simulate failure (20%)
    if random.random() < 0.2:
        failed_attach += 1

        reason = random.choice([
            "PLMN_NOT_ALLOWED",
            "AUTH_TIMEOUT"
        ])

        logging.warning(f"{req.ue_id} → ATTACH FAILED → {reason}")

        return {
            "status": "ATTACH_FAILED",
            "reason": reason,
            "latency_ms": latency_ms
        }

    # Success case
    successful_attach += 1

    logging.info(f"{req.ue_id} → ATTACH SUCCESS")

    return {
        "status": "ATTACH_SUCCESS",
        "ue_id": req.ue_id,
        "latency_ms": latency_ms
    }


@app.get("/metrics")
def metrics():
    sr = (successful_attach / total_requests * 100) if total_requests > 0 else 0
    avg_attach_latency_ms = (total_attach_latency_ms / attach_latency_samples) if attach_latency_samples > 0 else 0.0
    exposed_min_attach_latency_ms = min_attach_latency_ms if min_attach_latency_ms is not None else 0.0

    metrics_data = f"""
total_requests {total_requests}
successful_attach {successful_attach}
failed_attach {failed_attach}
attach_sr_percent {round(sr, 2)}
min_attach_latency_ms {exposed_min_attach_latency_ms}
max_attach_latency_ms {max_attach_latency_ms}
avg_attach_latency_ms {round(avg_attach_latency_ms, 2)}
attach_latency_samples {attach_latency_samples}
"""

    return Response(content=metrics_data, media_type="text/plain")


# JSON version of metrics endpoint
@app.get("/metrics-json")
def metrics_json():
    sr = (successful_attach / total_requests * 100) if total_requests > 0 else 0
    avg_attach_latency_ms = (total_attach_latency_ms / attach_latency_samples) if attach_latency_samples > 0 else 0.0
    exposed_min_attach_latency_ms = min_attach_latency_ms if min_attach_latency_ms is not None else 0.0

    return {
        "total_requests": total_requests,
        "successful_attach": successful_attach,
        "failed_attach": failed_attach,
        "attach_sr_percent": round(sr, 2),
        "min_attach_latency_ms": exposed_min_attach_latency_ms,
        "max_attach_latency_ms": max_attach_latency_ms,
        "avg_attach_latency_ms": round(avg_attach_latency_ms, 2),
        "attach_latency_samples": attach_latency_samples
    }

@app.post("/reset-metrics")
def reset_metrics():
    global total_requests, successful_attach, failed_attach
    global min_attach_latency_ms, max_attach_latency_ms
    global total_attach_latency_ms, attach_latency_samples

    total_requests = 0
    successful_attach = 0
    failed_attach = 0
    min_attach_latency_ms = None
    max_attach_latency_ms = 0.0
    total_attach_latency_ms = 0.0
    attach_latency_samples = 0

    return {
        "status": "CU metrics reset"
    }