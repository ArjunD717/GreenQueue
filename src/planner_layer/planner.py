"""Carbon-aware scheduling for GreenQueue.

The planner fetches the next 48 hours of UK grid carbon intensity, then finds
the lowest-average window long enough for a job that still finishes before its
deadline. If the live API is unavailable (or MOCK_MODE is on), it builds a
realistic synthetic curve with an evening peak and an overnight dip.
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.request import Request, urlopen

from common import iso_z, parse_utc, utcnow

CARBON_API = "https://api.carbonintensity.org.uk/intensity/{start}/fwd48h"
SLOT_MINUTES = 30
FORECAST_HOURS = 48
MIN_DURATION_HOURS = 0.5
MAX_DURATION_HOURS = 24.0
WATTS_PER_JOB = 100.0  # one job-hour uses 100 Wh, or 0.1 kWh
API_TIMEOUT_SECONDS = 8

_MOCK_VALUES = {"1", "true", "yes", "on"}


class PlannerError(ValueError):
    """A request that cannot be scheduled with the available forecast."""


def mock_enabled() -> bool:
    return os.environ.get("MOCK_MODE", "").strip().lower() in _MOCK_VALUES


def _as_utc(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = parse_utc(value)
    else:
        raise PlannerError(f"{field} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _floor_slot(value: datetime) -> datetime:
    minute = 0 if value.minute < SLOT_MINUTES else SLOT_MINUTES
    return value.replace(minute=minute, second=0, microsecond=0)


def _next_slot(value: datetime) -> datetime:
    floored = _floor_slot(value)
    if floored < value:
        return floored + timedelta(minutes=SLOT_MINUTES)
    return floored


def intensity_index(value: float) -> str:
    """Map gCO2/kWh to the UK carbon-intensity index bands."""
    if value <= 30:
        return "very low"
    if value <= 100:
        return "low"
    if value <= 200:
        return "moderate"
    if value <= 300:
        return "high"
    return "very high"


def _synthetic_intensity(when: datetime) -> int:
    """Deterministic daily shape: ~360 g at 19:00, ~105 g at 03:00."""
    hour = when.hour + when.minute / 60
    day = when.toordinal()
    evening_peak = 165 * math.exp(-((hour - 19.0) ** 2) / (2 * 2.1**2))
    overnight_dip = 92 * math.exp(-((hour - 3.2) ** 2) / (2 * 2.6**2))
    midday_solar = 38 * math.exp(-((hour - 12.5) ** 2) / (2 * 2.2**2))
    shoulder = 16 * math.sin((hour / 24) * math.pi * 4 + 0.8)
    daily = 7 * math.sin(day * 0.9)
    value = 198 + daily + evening_peak - overnight_dip - midday_solar + shoulder
    return max(40, int(round(value)))


def synthetic_forecast(now: datetime | None = None, reason: str | None = None) -> dict:
    """Build 96 half-hour points beginning at the next half-hour boundary."""
    now = _as_utc(now or utcnow(), "now")
    start = _next_slot(now)
    points = []
    for index in range(FORECAST_HOURS * 2):
        point_from = start + timedelta(minutes=SLOT_MINUTES * index)
        point_to = point_from + timedelta(minutes=SLOT_MINUTES)
        value = _synthetic_intensity(point_from) + int(
            round(3 * math.sin(index * 0.7))
        )
        value = max(40, value)
        points.append(
            {
                "from": iso_z(point_from),
                "to": iso_z(point_to),
                "intensity": value,
                "index": intensity_index(value),
            }
        )
    forecast = {
        "source": "mock",
        "mock": True,
        "fetched_at": iso_z(now),
        "points": points,
    }
    if reason:
        forecast["fallback_reason"] = reason[:180]
    return forecast


def _live_forecast(now: datetime) -> dict:
    start = _floor_slot(now)
    stamp = start.strftime("%Y-%m-%dT%H:%MZ")
    request = Request(
        CARBON_API.format(start=stamp),
        headers={"Accept": "application/json", "User-Agent": "GreenQueue/1.0"},
    )
    with urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))

    raw_points = payload.get("data")
    if not isinstance(raw_points, list):
        raise ValueError("carbon API returned no data array")

    points = []
    for item in raw_points:
        try:
            point_from = parse_utc(item["from"])
            point_to = parse_utc(item["to"])
            intensity = item.get("intensity") or {}
            value = intensity.get("forecast")
            if value is None:
                value = intensity.get("actual")
            if value is None or point_from < now:
                continue
            numeric = int(round(float(value)))
        except (KeyError, TypeError, ValueError):
            continue
        points.append(
            {
                "from": iso_z(point_from),
                "to": iso_z(point_to),
                "intensity": numeric,
                "index": str(intensity.get("index") or intensity_index(numeric)).lower(),
            }
        )

    points.sort(key=lambda point: point["from"])
    points = points[: FORECAST_HOURS * 2]
    if len(points) < 8:
        raise ValueError("carbon API returned too few future forecast points")

    return {
        "source": "live",
        "mock": False,
        "fetched_at": iso_z(now),
        "points": points,
    }


def fetch_forecast(now: datetime | None = None) -> dict:
    """Return a forecast, falling back to synthetic data on any API failure."""
    now = _as_utc(now or utcnow(), "now")
    if mock_enabled():
        return synthetic_forecast(now)
    try:
        return _live_forecast(now)
    except Exception as exc:  # any API/parse failure must never break the demo
        reason = f"{type(exc).__name__}: {exc}"
        return synthetic_forecast(now, reason=reason)


def _validate_duration(duration_hours: Any) -> float:
    if isinstance(duration_hours, bool):
        raise PlannerError("duration must be a number of hours")
    try:
        duration = float(duration_hours)
    except (TypeError, ValueError):
        raise PlannerError("duration must be a number of hours") from None
    if not math.isfinite(duration):
        raise PlannerError("duration must be a finite number")
    if duration < MIN_DURATION_HOURS or duration > MAX_DURATION_HOURS:
        raise PlannerError(
            f"duration must be between {MIN_DURATION_HOURS:g} and "
            f"{MAX_DURATION_HOURS:g} hours"
        )
    if abs(duration * 2 - round(duration * 2)) > 1e-9:
        raise PlannerError("duration must use 30-minute increments")
    return duration


def plan_job(
    duration_hours: Any,
    deadline: datetime | str,
    now: datetime | None = None,
) -> dict:
    """Choose the cleanest feasible window and quantify the CO2 avoided."""
    now = _as_utc(now or utcnow(), "now")
    duration = _validate_duration(duration_hours)
    deadline_utc = _as_utc(deadline, "deadline")

    if deadline_utc <= now:
        raise PlannerError("deadline must be in the future")
    if deadline_utc < now + timedelta(hours=duration):
        raise PlannerError("deadline is too soon for this job to finish")

    forecast = fetch_forecast(now)
    points = forecast["points"]
    slots_needed = int(round(duration / (SLOT_MINUTES / 60)))
    if len(points) < slots_needed:
        raise PlannerError("the forecast is too short for this job")

    earliest_values = [point["intensity"] for point in points[:slots_needed]]
    immediate_average = sum(earliest_values) / len(earliest_values)

    best: dict | None = None
    for index in range(0, len(points) - slots_needed + 1):
        window = points[index : index + slots_needed]
        start = parse_utc(window[0]["from"])
        end = start + timedelta(hours=duration)
        if end > deadline_utc:
            break
        if parse_utc(window[-1]["to"]) < end:
            continue
        average = sum(point["intensity"] for point in window) / slots_needed
        if best is None or average < best["average"] - 1e-9:
            best = {"start": start, "end": end, "average": average, "index": index}

    if best is None:
        raise PlannerError(
            "no full low-carbon window fits before that deadline; "
            "move the deadline later or shorten the job"
        )

    energy_kwh = duration * (WATTS_PER_JOB / 1000.0)
    co2_now = immediate_average * energy_kwh
    co2_scheduled = best["average"] * energy_kwh
    co2_saved = max(0.0, co2_now - co2_scheduled)
    reduction_pct = (co2_saved / co2_now * 100.0) if co2_now > 0 else 0.0

    return {
        "scheduled_start": iso_z(best["start"]),
        "scheduled_end": iso_z(best["end"]),
        "duration": duration,
        "avg_intensity": round(best["average"], 1),
        "immediate_intensity": round(immediate_average, 1),
        "energy_kwh": round(energy_kwh, 3),
        "co2_now": round(co2_now, 1),
        "co2_scheduled": round(co2_scheduled, 1),
        "co2_saved": round(co2_saved, 1),
        "reduction_pct": round(reduction_pct, 1),
        "forecast_source": forecast["source"],
    }
