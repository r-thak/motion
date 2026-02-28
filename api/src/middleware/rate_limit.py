import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from src.config import settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding window rate limiter using Redis sorted sets."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        redis_client = getattr(request.app.state, "redis", None)
        if redis_client is None:
            return await call_next(request)

        # Rate limit by client IP
        client_ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:{client_ip}"
        now = time.time()
        window = 60.0  # 1 minute window
        limit = settings.rate_limit_per_minute

        pipe = redis_client.pipeline()
        # Remove old entries outside the window
        pipe.zremrangebyscore(key, 0, now - window)
        # Add current request
        pipe.zadd(key, {f"{now}": now})
        # Count requests in window
        pipe.zcard(key)
        # Set expiry on the key
        pipe.expire(key, int(window) + 1)
        results = await pipe.execute()

        request_count = results[2]

        # Add rate limit headers to response
        if request_count > limit:
            retry_after = int(window - (now - (now - window)))
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                        "message": f"Rate limit exceeded. Maximum {limit} requests per minute.",
                    }
                },
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(now + window)),
                    "Retry-After": str(retry_after),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - request_count))
        response.headers["X-RateLimit-Reset"] = str(int(now + window))
        return response
