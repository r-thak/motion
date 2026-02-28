import logging

import httpx

from src.middleware.errors import RoutingError

logger = logging.getLogger(__name__)


async def compute_route(
    http_client: httpx.AsyncClient,
    valhalla_url: str,
    locations: list[dict],
    costing_options: dict,
    language: str,
    departure_time: dict | None,
    alternates: int = 0,
) -> dict:
    """
    Call the Valhalla HTTP API to compute a route.

    Args:
        http_client: Async HTTP client
        valhalla_url: Base URL for Valhalla (e.g., http://valhalla:8002)
        locations: List of Valhalla location dicts with lat, lon, type
        costing_options: Truck costing options
        language: Language code
        departure_time: Valhalla date_time dict or None
        alternates: Number of alternate routes to request

    Returns:
        Parsed Valhalla JSON response

    Raises:
        RoutingError: On routing failures
    """
    body: dict = {
        "locations": locations,
        "costing": "truck",
        "costing_options": {"truck": costing_options},
        "directions_options": {"units": "kilometers", "language": language},
        "alternates": alternates,
    }

    if departure_time is not None:
        body["date_time"] = departure_time

    try:
        response = await http_client.post(
            f"{valhalla_url}/route",
            json=body,
            timeout=30.0,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        logger.error("Valhalla connection error: %s", exc)
        raise RoutingError(
            code="routing_engine_unavailable",
            message="Routing engine is unavailable. Please try again later.",
        )

    if response.status_code == 200:
        return response.json()

    # Parse Valhalla error
    if response.status_code == 400:
        try:
            error_data = response.json()
            error_code = error_data.get("error_code", 0)
            error_message = error_data.get("error", "Unknown routing error")

            if error_code == 170:
                raise RoutingError(code="no_route_found", message="No route could be found between the specified locations.")
            elif 120 <= error_code <= 124:
                raise RoutingError(code="invalid_location", message=error_message)
            else:
                raise RoutingError(code="routing_error", message=error_message)
        except (ValueError, KeyError):
            raise RoutingError(code="routing_error", message="Routing engine returned an invalid response.")

    # 5xx or other errors
    logger.error("Valhalla returned status %d: %s", response.status_code, response.text[:500])
    raise RoutingError(
        code="routing_engine_unavailable",
        message="Routing engine returned an unexpected error.",
    )
