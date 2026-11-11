"""GET /jobs: return all jobs for the dashboard, newest first."""

import logging
import os

import boto3
from botocore.exceptions import ClientError

from common import error_response, json_response

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _scan_all():
    items = []
    request = {}
    while True:
        response = TABLE.scan(**request)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        request["ExclusiveStartKey"] = last_key
    return items


def handler(event, context):
    try:
        jobs = _scan_all()
    except ClientError:
        logger.exception("scan failed")
        return error_response(500, "STORAGE_ERROR", "Jobs could not be loaded.")

    jobs.sort(key=lambda job: job.get("created_at", ""), reverse=True)
    return json_response(200, {"jobs": jobs, "count": len(jobs)})
