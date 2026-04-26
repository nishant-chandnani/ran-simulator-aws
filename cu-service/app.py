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

class UERequest(BaseModel):
    ue_id: str

@app.post("/attach")
def attach(req: UERequest):
    global total_requests, successful_attach, failed_attach

    total_requests += 1

    processing_time = random.uniform(0.1, 0.5)
    time.sleep(processing_time)

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
            "latency_ms": round(processing_time * 1000, 2)
        }

    # Success case
    successful_attach += 1

    logging.info(f"{req.ue_id} → ATTACH SUCCESS")

    return {
        "status": "ATTACH_SUCCESS",
        "ue_id": req.ue_id,
        "latency_ms": round(processing_time * 1000, 2)
    }


@app.get("/metrics")
def metrics():
    sr = (successful_attach / total_requests * 100) if total_requests > 0 else 0

    metrics_data = f"""
total_requests {total_requests}
successful_attach {successful_attach}
failed_attach {failed_attach}
attach_sr_percent {round(sr, 2)}
"""

    return Response(content=metrics_data, media_type="text/plain")


# JSON version of metrics endpoint
@app.get("/metrics-json")
def metrics_json():
    sr = (successful_attach / total_requests * 100) if total_requests > 0 else 0

    return {
        "total_requests": total_requests,
        "successful_attach": successful_attach,
        "failed_attach": failed_attach,
        "attach_sr_percent": round(sr, 2)
    }

@app.post("/reset-metrics")
def reset_metrics():
    global total_requests, successful_attach, failed_attach

    total_requests = 0
    successful_attach = 0
    failed_attach = 0

    return {
        "status": "CU metrics reset"
    }