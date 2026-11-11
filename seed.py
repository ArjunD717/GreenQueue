#!/usr/bin/env python3
"""Seed GreenQueueJobs with 15 realistic jobs so the dashboard looks lived-in.

Uses boto3 when available and falls back to the AWS CLI, so it works with a
standard AWS CLI setup even if boto3 is not installed in the local Python.

Usage:
    python seed.py --table GreenQueueJobs --region eu-west-2
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src", "planner_layer"))

from common import iso_z, utcnow  # noqa: E402


def _next_half_hour(value):
    minute = 0 if value.minute < 30 else 30
    boundary = value.replace(minute=minute, second=0, microsecond=0)
    return boundary if boundary >= value else boundary + timedelta(minutes=30)


def _metrics(duration, immediate_intensity, scheduled_intensity):
    energy = round(duration * 0.1, 3)
    co2_now = round(immediate_intensity * energy, 1)
    co2_scheduled = round(scheduled_intensity * energy, 1)
    co2_saved = round(max(0.0, co2_now - co2_scheduled), 1)
    reduction = (
        round(co2_saved / co2_now * 100, 1) if co2_now > 0 else 0.0
    )
    return energy, co2_now, co2_scheduled, co2_saved, reduction


def _completed(index, name, duration, days_ago, hour, immediate, scheduled, now):
    start = (now - timedelta(days=days_ago)).replace(
        hour=hour, minute=30 if index % 2 else 0, second=0, microsecond=0
    )
    end = start + timedelta(hours=duration)
    deadline = end + timedelta(hours=4)
    created = start - timedelta(hours=10)
    energy, co2_now, co2_scheduled, co2_saved, reduction = _metrics(
        duration, immediate, scheduled
    )
    return {
        "job_id": f"seed-{index:02d}",
        "name": name,
        "duration": duration,
        "deadline": iso_z(deadline),
        "scheduled_start": iso_z(start),
        "scheduled_end": iso_z(end),
        "status": "COMPLETED",
        "avg_intensity": float(scheduled),
        "immediate_intensity": float(immediate),
        "energy_kwh": energy,
        "co2_now": co2_now,
        "co2_scheduled": co2_scheduled,
        "actual_co2": co2_scheduled,
        "co2_saved": co2_saved,
        "reduction_pct": reduction,
        "forecast_source": "seed",
        "created_at": iso_z(created),
        "updated_at": iso_z(end),
        "started_at": iso_z(start),
        "completed_at": iso_z(end),
    }


def _running(index, name, duration, immediate, scheduled, now):
    start = now - timedelta(minutes=38)
    end = start + timedelta(hours=duration)
    deadline = now + timedelta(hours=5)
    created = now - timedelta(hours=4)
    energy, co2_now, co2_scheduled, co2_saved, reduction = _metrics(
        duration, immediate, scheduled
    )
    return {
        "job_id": f"seed-{index:02d}",
        "name": name,
        "duration": duration,
        "deadline": iso_z(deadline),
        "scheduled_start": iso_z(start),
        "scheduled_end": iso_z(end),
        "status": "RUNNING",
        "avg_intensity": float(scheduled),
        "immediate_intensity": float(immediate),
        "energy_kwh": energy,
        "co2_now": co2_now,
        "co2_scheduled": co2_scheduled,
        "co2_saved": co2_saved,
        "reduction_pct": reduction,
        "forecast_source": "seed",
        "created_at": iso_z(created),
        "updated_at": iso_z(start),
        "started_at": iso_z(start),
    }


def _queued(index, name, duration, hours_ahead, immediate, scheduled, now):
    start = _next_half_hour(now + timedelta(hours=hours_ahead))
    end = start + timedelta(hours=duration)
    deadline = end + timedelta(hours=6)
    created = now - timedelta(hours=1, minutes=index)
    energy, co2_now, co2_scheduled, co2_saved, reduction = _metrics(
        duration, immediate, scheduled
    )
    return {
        "job_id": f"seed-{index:02d}",
        "name": name,
        "duration": duration,
        "deadline": iso_z(deadline),
        "scheduled_start": iso_z(start),
        "scheduled_end": iso_z(end),
        "status": "QUEUED",
        "avg_intensity": float(scheduled),
        "immediate_intensity": float(immediate),
        "energy_kwh": energy,
        "co2_now": co2_now,
        "co2_scheduled": co2_scheduled,
        "co2_saved": co2_saved,
        "reduction_pct": reduction,
        "forecast_source": "seed",
        "created_at": iso_z(created),
        "updated_at": iso_z(created),
    }


def build_seed_jobs(now=None):
    now = now or utcnow()
    completed_specs = [
        ("Revenue model refresh", 2, 6, 3, 318, 112),
        ("Nightly CRM sync", 3, 5, 2, 296, 104),
        ("Churn cohort backfill", 6, 4, 1, 327, 118),
        ("Search index rebuild", 4, 3, 3, 288, 96),
        ("Climate risk tiles", 8, 3, 0, 305, 125),
        ("Podcast episode transcode", 1, 2, 4, 342, 89),
        ("Quarterly ledger import", 5, 2, 1, 312, 109),
        ("Experiment results snapshot", 2, 1, 5, 278, 128),
        ("Image moderation sweep", 3.5, 1, 2, 330, 94),
        ("Warehouse compaction", 6, 1, 0, 301, 117),
        ("Usage report build", 2.5, 2, 4, 291, 102),
    ]
    jobs = [
        _completed(index + 1, *spec, now)
        for index, spec in enumerate(completed_specs)
    ]
    jobs.append(_running(12, "Market data replay", 4, 330, 205, now))
    queued_specs = [
        ("Cohort model retrain", 12, 3, 322, 141),
        ("Public dataset export", 8, 7, 305, 124),
        ("Invoice PDF batch", 1, 2, 287, 118),
    ]
    jobs.extend(
        _queued(13 + index, *spec, now) for index, spec in enumerate(queued_specs)
    )
    return jobs


def _to_decimal(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_decimal(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_decimal(item) for item in value]
    return value


def _attribute_value(value):
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float, Decimal)):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [_attribute_value(item) for item in value]}
    raise TypeError(f"unsupported seed value: {type(value).__name__}")


def write_with_boto3(jobs, table_name, region):
    import boto3

    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    with table.batch_writer(overwrite_by_pkeys=["job_id"]) as batch:
        for job in jobs:
            batch.put_item(Item=_to_decimal(job))


def write_with_aws_cli(jobs, table_name, region):
    pending = [{"PutRequest": {"Item": {
        key: _attribute_value(value) for key, value in job.items()
    }}} for job in jobs]

    for _attempt in range(3):
        if not pending:
            return
        payload = {table_name: pending}
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8"
            ) as handle:
                json.dump(payload, handle)
                path = Path(handle.name).resolve().as_posix()
            result = subprocess.run(
                [
                    "aws",
                    "dynamodb",
                    "batch-write-item",
                    "--request-items",
                    f"file://{path}",
                    "--region",
                    region,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            response = json.loads(result.stdout or "{}")
            pending = response.get("UnprocessedItems", {}).get(table_name, [])
        finally:
            if path:
                Path(path).unlink(missing_ok=True)
    if pending:
        raise RuntimeError(f"{len(pending)} seed items were not written")


def main():
    parser = argparse.ArgumentParser(description="Seed the GreenQueue dashboard")
    parser.add_argument(
        "--table",
        default=os.environ.get("TABLE_NAME", "GreenQueueJobs"),
        help="DynamoDB table name (default: GreenQueueJobs)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "eu-west-2",
        help="AWS region (default: eu-west-2)",
    )
    args = parser.parse_args()

    jobs = build_seed_jobs()
    try:
        import boto3  # noqa: F401

        writer = write_with_boto3
    except ImportError:
        writer = write_with_aws_cli

    try:
        writer(jobs, args.table, args.region)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or str(exc))
        raise SystemExit(1)
    except Exception as exc:
        raise SystemExit(f"Seed failed: {exc}")

    print(
        f"Seeded {len(jobs)} jobs into {args.table} in {args.region} "
        f"({sum(job['status'] == 'COMPLETED' for job in jobs)} completed, "
        f"{sum(job['status'] == 'RUNNING' for job in jobs)} running, "
        f"{sum(job['status'] == 'QUEUED' for job in jobs)} queued)."
    )


if __name__ == "__main__":
    main()
