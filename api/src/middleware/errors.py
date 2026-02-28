import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class RoutingError(Exception):
    """Raised when the Valhalla routing engine returns an error."""

    def __init__(self, code: str, message: str, param: str | None = None):
        self.code = code
        self.message = message
        self.param = param
        super().__init__(message)


class RouteNotFoundError(Exception):
    """Raised when a route ID is not found."""

    def __init__(self, route_id: str):
        self.route_id = route_id
        super().__init__(f"Route {route_id} not found")


class ValidationError(Exception):
    """Raised for request validation errors."""

    def __init__(self, code: str, message: str, param: str | None = None):
        self.code = code
        self.message = message
        self.param = param
        super().__init__(message)


def register_error_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(RoutingError)
    async def routing_error_handler(request: Request, exc: RoutingError) -> JSONResponse:
        status = 400 if exc.code != "routing_engine_unavailable" else 503
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "type": "routing_error",
                    "code": exc.code,
                    "message": exc.message,
                    "param": exc.param,
                }
            },
        )

    @app.exception_handler(RouteNotFoundError)
    async def route_not_found_handler(request: Request, exc: RouteNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "type": "invalid_request_error",
                    "code": "route_not_found",
                    "message": str(exc),
                    "param": "route_id",
                }
            },
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "invalid_request_error",
                    "code": exc.code,
                    "message": exc.message,
                    "param": exc.param,
                }
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "type": "api_error",
                    "code": "internal_error",
                    "message": "An internal error occurred.",
                }
            },
        )
