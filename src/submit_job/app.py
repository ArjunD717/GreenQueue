"""POST /jobs: validate a request, schedule it on the cleanest grid window."""

import base64
import json
import logging
import math
import os
import uuid
from datetime import timedelta
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

from common import error_response, iso_z, json_response, parse_utc, utcnow
from planner import PlannerError, plan_job

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
MAX_BODY_BYTES = 16 * 1024


def _read_payload(event: dict) -> dict:
    raw = event.get("body")
    if raw is None or raw == "":
        raise ValueError("request body is required")
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        raise OverflowError("request body is too large")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("request body must be a JSON object")
    return payload


def _validate(payload: dict) -> tuple[dict, dict]:
    errors: dict[str, str] = {}

    name = payload.get("name")
    if not isinstance(name, str):
        errors["name"] = "Give the job a name."
    else:
        name = " ".join(name.split())
        if not name:
            errors["name"] = "Give the job a name."
        elif len(name) > 80:
            errors["name"] = "Keep the name under 80 characters."

    duration = payload.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        errors["duration"] = "Use a number of hours."
    else:
        duration = float(duration)
        if not math.isfinite(duration) or duration < 0.5 or duration > 24:
            errors["duration"] = "Choose between 0.5 and 24 hours."
        elif abs(duration * 2 - round(duration * 2)) > 1e-9:
            errors["duration"] = "Use 30-minute increments."

    deadline = payload.get("deadline")
    if not isinstance(deadline, str) or not deadline.strip():
        errors["deadline"] = "Choose a deadline."
    else:
        try:
            deadline = parse_utc(deadline)
        except ValueError:
            errors["deadline"] = "Use an ISO-8601 date and time."
        else:
            now = utcnow()
            if deadline <= now:
                errors["deadline"] = "The deadline must be in the future."
            elif "duration" not in errors and deadline < now + timedelta(
                hours=duration
            ):
                errors["deadline"] = "The deadline is too soon for this job to finish."

    return {"name": name, "duration": duration, "deadline": deadline}, errors


def _for_dynamodb(job: dict) -> dict:
    """boto3's resource API requires Decimal instead of float."""
    numeric_fields = (
        "duration",
        "avg_intensity",
        "immediate_intensity",
        "energy_kwh",
        "co2_now",
        "co2_scheduled",
        "co2_saved",
        "reduction_pct",
    )
    item = dict(job)
    for field in numeric_fields:
        item[field] = Decimal(str(job[field]))
    return item


def handler(event, context):
    try:
        payload = _read_payload(event)
    except OverflowError as exc:
        return error_response(413, "PAYLOAD_TOO_LARGE", str(exc))
    except (ValueError, TypeError):
        return error_response(400, "INVALID_JSON", "Send a valid JSON object.")

    validated, errors = _validate(payload)
    if errors:
        return error_response(
            400,
            "VALIDATION_ERROR",
            "Check the highlighted fields.",
            fields=errors,
        )

    now = utcnow()
    try:
        plan = plan_job(validated["duration"], validated["deadline"], now=now)
    except PlannerError as exc:
        return error_response(422, "UNSCHEDULABLE", str(exc))
    except Exception:
        logger.exception("planner failure")
        return error_response(
            500, "INTERNAL_ERROR", "The forecast could not be read. Try again."
        )

    job_id = "job-" + uuid.uuid4().hex[:10]
    job = {
        "job_id": job_id,
        "name": validated["name"],
        "duration": validated["duration"],
        "deadline": iso_z(validated["deadline"]),
        "scheduled_start": plan["scheduled_start"],
        "scheduled_end": plan["scheduled_end"],
        "status": "QUEUED",
        "avg_intensity": plan["avg_intensity"],
        "immediate_intensity": plan["immediate_intensity"],
        "energy_kwh": plan["energy_kwh"],
        "co2_now": plan["co2_now"],
        "co2_scheduled": plan["co2_scheduled"],
        "co2_saved": plan["co2_saved"],
        "reduction_pct": plan["reduction_pct"],
        "forecast_source": plan["forecast_source"],
        "created_at": iso_z(now),
        "updated_at": iso_z(now),
    }

    try:
        TABLE.put_item(Item=_for_dynamodb(job))
    except ClientError:
        logger.exception("put_item failed for %s", job_id)
        return error_response(500, "STORAGE_ERROR", "The job could not be saved.")

    logger.info(
        json.dumps(
            {
                "event": "job_submitted",
                "job_id": job_id,
                "duration": job["duration"],
                "scheduled_start": job["scheduled_start"],
                "co2_saved_g": job["co2_saved"],
                "forecast_source": job["forecast_source"],
            }
        )
    )
    return json_response(201, {"job": job})
