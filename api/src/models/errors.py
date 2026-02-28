from pydantic import BaseModel


class ErrorDetail(BaseModel):
    type: str  # "invalid_request_error", "routing_error", "rate_limit_error", "api_error", "enrichment_error"
    code: str  # e.g. "missing_origin", "no_route_found", "route_not_found", "unsupported_waypoint_type"
    message: str
    param: str | None = None
    doc_url: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
