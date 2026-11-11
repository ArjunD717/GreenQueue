"""EventBridge target: move due jobs from QUEUED to RUNNING to COMPLETED."""

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from common import iso_z, parse_utc, utcnow

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _all_jobs():
    items = []
    request = {
        "ProjectionExpression": "job_id, #s, scheduled_start, co2_scheduled",
        "ExpressionAttributeNames": {"#s": "status"},
    }
    while True:
        response = TABLE.scan(**request)
        items.extend(response.get("Items", []))
        if not response.get("LastEvaluatedKey"):
            return items
        request["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _transition(job_id: str, next_status: str, values: dict) -> bool:
    names = {"#s": "status"}
    assignments = ["#s = :next", "updated_at = :updated"]
    encoded = {":next": next_status, ":updated": values["updated_at"]}
    if next_status == "RUNNING":
        assignments += ["started_at = :updated"]
    else:
        assignments += ["completed_at = :updated", "actual_co2 = :actual"]
        encoded[":actual"] = values["actual_co2"]
    try:
        TABLE.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET " + ", ".join(assignments),
            ConditionExpression="#s = :current",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={
                **encoded,
                ":current": values["current_status"],
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def handler(event, context):
    now = utcnow()
    now_iso = iso_z(now)
    started = 0
    completed = 0
    checked = 0

    for job in _all_jobs():
        checked += 1
        status = job.get("status")
        if status not in ("QUEUED", "RUNNING"):
            continue

        if status == "QUEUED":
            try:
                due = parse_utc(job.get("scheduled_start", "")) <= now
            except ValueError:
                logger.warning("invalid scheduled_start for %s", job.get("job_id"))
                continue
            if not due:
                continue

            changed = _transition(
                job["job_id"],
                "RUNNING",
                {
                    "current_status": "QUEUED",
                    "updated_at": now_iso,
                },
            )
            if changed:
                started += 1
                logger.info(
                    json.dumps(
                        {
                            "event": "job_started",
                            "job_id": job["job_id"],
                            "at": now_iso,
                        }
                    )
                )
            continue

        # Simulated execution takes one scheduler tick; the real workload would
        # run in AWS Batch or ECS and report back here.
        changed = _transition(
            job["job_id"],
            "COMPLETED",
            {
                "current_status": "RUNNING",
                "updated_at": now_iso,
                "actual_co2": job.get("co2_scheduled", 0),
            },
        )
        if changed:
            completed += 1
            logger.info(
                json.dumps(
                    {
                        "event": "job_completed",
                        "job_id": job["job_id"],
                        "at": now_iso,
                        "co2_scheduled_g": float(job.get("co2_scheduled", 0)),
                    }
                )
            )

    summary = {"checked": checked, "started": started, "completed": completed}
    logger.info(json.dumps({"event": "runner_tick", **summary}))
    return summary
