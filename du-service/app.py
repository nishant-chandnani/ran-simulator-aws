from fastapi import FastAPI
from pydantic import BaseModel
import requests
import time
import logging
import os
import random
from fastapi import Response

app = FastAPI()

logging.basicConfig(level=logging.INFO)

CU_HOST = os.getenv("CU_HOST", "cu-service:8001")
CU_URL = f"http://{CU_HOST}/attach"

# DU KPI counters
total_rach_attempts = 0
successful_rach = 0
failed_rach = 0
last_end_to_end_latency_ms = 0.0

class UERequest(BaseModel):
    ue_id: str

@app.post("/attach")
def attach(req: UERequest):
    global total_rach_attempts, successful_rach, failed_rach, last_end_to_end_latency_ms

    total_rach_attempts += 1

    # 🔴 Simulate RACH failure (20%)
    if random.random() < 0.2:
        failed_rach += 1

        logging.warning(f"{req.ue_id} → RACH FAILED")

        return {
            "du_status": "FAILED",
            "reason": "RACH_FAILURE"
        }

    # 🟢 RACH success
    successful_rach += 1

    start_time = time.time()

    try:
        cu_response = requests.post(
            CU_URL,
            json={"ue_id": req.ue_id},
            timeout=5
        )

        total_time = (time.time() - start_time) * 1000
        end_to_end_latency_ms = round(total_time, 2)
        last_end_to_end_latency_ms = end_to_end_latency_ms

        logging.info(f"{req.ue_id} → RACH SUCCESS → forwarded to CU")

        return {
            "du_status": "FORWARDED",
            "cu_response": cu_response.json(),
            "end_to_end_latency_ms": end_to_end_latency_ms
        }

    except requests.exceptions.Timeout:
        logging.error(f"{req.ue_id} → CU TIMEOUT")

        return {
            "du_status": "FAILED",
            "reason": "CU_TIMEOUT"
        }

    except Exception as e:
        logging.error(f"{req.ue_id} → CU ERROR: {repr(e)}")

        return {
            "du_status": "FAILED",
            "reason": "CU_UNREACHABLE",
            "error_detail": str(e)
        }


@app.get("/metrics")
def metrics():
    sr = (successful_rach / total_rach_attempts * 100) if total_rach_attempts > 0 else 0

    metrics_data = f"""
total_rach_attempts {total_rach_attempts}
successful_rach {successful_rach}
failed_rach {failed_rach}
rach_sr_percent {round(sr, 2)}
last_end_to_end_latency_ms {last_end_to_end_latency_ms}
"""

    return Response(content=metrics_data, media_type="text/plain")

@app.get("/metrics-json")
def metrics_json():
    sr = (successful_rach / total_rach_attempts * 100) if total_rach_attempts > 0 else 0

    return {
        "total_rach_attempts": total_rach_attempts,
        "successful_rach": successful_rach,
        "failed_rach": failed_rach,
        "rach_sr_percent": round(sr, 2),
        "last_end_to_end_latency_ms": last_end_to_end_latency_ms
    }


# Endpoint to reset DU metrics
@app.post("/reset-metrics")
def reset_metrics():
    global total_rach_attempts, successful_rach, failed_rach, last_end_to_end_latency_ms

    total_rach_attempts = 0
    successful_rach = 0
    failed_rach = 0
    last_end_to_end_latency_ms = 0.0

    return {
        "status": "DU metrics reset"
    }
