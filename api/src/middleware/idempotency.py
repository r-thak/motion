import hashlib

from fastapi import Request
from fastapi.responses import JSONResponse

from src.services.cache import compute_request_hash
from src.storage import postgres as db


async def check_idempotency(
    request: Request,
    request_body_dict: dict,
) -> JSONResponse | None:
    """
    Check for Idempotency-Key header and return cached response if available.

    Returns:
        - JSONResponse if a cached response is found (or error for hash mismatch/contention)
        - None if no idempotency key header, or key is new and locked for processing
    """
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return None

    pool = request.app.state.pg_pool
    key_hash = hashlib.sha256(idempotency_key.encode()).digest()
    request_hash = compute_request_hash(request_body_dict)

    # Check for existing key
    existing = await db.get_idempotency_key(pool, key_hash)
    if existing is not None:
        # Found an existing completed key
        if existing["request_hash"] != request_hash:
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "type": "invalid_request_error",
                        "code": "idempotency_key_reuse",
                        "message": "Idempotency key has already been used with a different request body.",
                    }
                },
            )
        # Same request hash — return cached response
        return JSONResponse(
            status_code=existing["status_code"],
            content=existing["response_body"],
        )

    # Try to lock the key
    locked = await db.lock_idempotency_key(pool, key_hash)
    if not locked:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "type": "invalid_request_error",
                    "code": "idempotency_key_in_progress",
                    "message": "A request with this idempotency key is already in progress.",
                }
            },
            headers={"Retry-After": "1"},
        )

    # Key is locked for this request — store key_hash and request_hash on request state
    request.state.idempotency_key_hash = key_hash
    request.state.idempotency_request_hash = request_hash
    return None


async def save_idempotency_response(
    request: Request,
    response_body: dict,
    status_code: int,
) -> None:
    """Save the response for an idempotency key after successful computation."""
    key_hash = getattr(request.state, "idempotency_key_hash", None)
    if key_hash is None:
        return

    request_hash = request.state.idempotency_request_hash
    pool = request.app.state.pg_pool
    await db.save_idempotency_key(pool, key_hash, request_hash, response_body, status_code)
