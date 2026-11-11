"""GET /forecast: return the 48-hour carbon-intensity curve."""

import logging

from common import error_response, iso_z, json_response, utcnow
from planner import fetch_forecast

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    try:
        forecast = fetch_forecast()
    except Exception:
        logger.exception("forecast failure")
        return error_response(
            500, "FORECAST_ERROR", "The forecast could not be generated."
        )

    body = {
        "now": iso_z(utcnow()),
        "source": forecast["source"],
        "mock": forecast["mock"],
        "fetched_at": forecast["fetched_at"],
        "points": forecast["points"],
    }
    if forecast.get("fallback_reason"):
        body["fallback_reason"] = forecast["fallback_reason"]
    return json_response(200, body)
