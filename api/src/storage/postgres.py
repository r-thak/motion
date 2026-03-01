import json
from datetime import datetime, timezone

import asyncpg


async def create_route(
    pool: asyncpg.Pool,
    route_id: str,
    status: str,
    request_body: dict,
    request_hash: bytes,
    response_body: dict | None,
    telemetry: dict | None,
    vehicle_spec: dict,
    departure_time: str | None,
    webhook_url: str | None,
) -> None:
    """INSERT into routes table."""
    dep_time = None
    if departure_time:
        dep_time = datetime.fromisoformat(departure_time.replace("Z", "+00:00"))

    await pool.execute(
        """
        INSERT INTO routes (id, status, request_body, request_hash, response_body,
                           telemetry, vehicle_spec, departure_time, webhook_url)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        route_id,
        status,
        json.dumps(request_body),
        request_hash,
        json.dumps(response_body) if response_body else None,
        json.dumps(telemetry) if telemetry else None,
        json.dumps(vehicle_spec),
        dep_time,
        webhook_url,
    )


async def update_route_complete(
    pool: asyncpg.Pool,
    route_id: str,
    response_body: dict,
    telemetry: dict,
) -> None:
    """UPDATE routes SET status='complete', response_body=..., telemetry=... WHERE id=route_id."""
    await pool.execute(
        """
        UPDATE routes SET status='complete', response_body=$2, telemetry=$3
        WHERE id=$1
        """,
        route_id,
        json.dumps(response_body),
        json.dumps(telemetry),
    )


async def update_route_failed(
    pool: asyncpg.Pool,
    route_id: str,
    error: dict,
) -> None:
    """UPDATE routes SET status='failed', error=... WHERE id=route_id."""
    await pool.execute(
        """
        UPDATE routes SET status='failed', error=$2
        WHERE id=$1
        """,
        route_id,
        json.dumps(error),
    )


async def get_route(pool: asyncpg.Pool, route_id: str) -> dict | None:
    """SELECT from routes by id. Return None if not found or expired."""
    row = await pool.fetchrow(
        """
        SELECT id, object, status, request_body, response_body, telemetry,
               vehicle_spec, error, created_at, expires_at, webhook_url
        FROM routes
        WHERE id=$1 AND expires_at > now()
        """,
        route_id,
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "object": row["object"],
        "status": row["status"],
        "request_body": json.loads(row["request_body"]) if row["request_body"] else None,
        "response_body": json.loads(row["response_body"]) if row["response_body"] else None,
        "telemetry": json.loads(row["telemetry"]) if row["telemetry"] else None,
        "vehicle_spec": json.loads(row["vehicle_spec"]) if row["vehicle_spec"] else None,
        "error": json.loads(row["error"]) if row["error"] else None,
        "created_at": row["created_at"].isoformat().replace("+00:00", "Z"),
        "webhook_url": row["webhook_url"],
    }





async def delete_route(pool: asyncpg.Pool, route_id: str) -> bool:
    """DELETE FROM routes. Return False if not found."""
    result = await pool.execute("DELETE FROM routes WHERE id=$1", route_id)
    return result != "DELETE 0"


async def create_event(
    pool: asyncpg.Pool,
    event_id: str,
    event_type: str,
    route_id: str,
    data: dict,
) -> None:
    """INSERT into events table."""
    await pool.execute(
        """
        INSERT INTO events (id, type, route_id, data)
        VALUES ($1, $2, $3, $4)
        """,
        event_id,
        event_type,
        route_id,
        json.dumps(data),
    )


# --- Idempotency ---


async def get_idempotency_key(pool: asyncpg.Pool, key_hash: bytes) -> dict | None:
    """Get a cached idempotency key if it exists and hasn't expired."""
    row = await pool.fetchrow(
        """
        SELECT key_hash, request_hash, response_body, status_code
        FROM idempotency_keys
        WHERE key_hash=$1 AND expires_at > now() AND locked_at IS NULL
        """,
        key_hash,
    )
    if row is None:
        return None
    return {
        "key_hash": row["key_hash"],
        "request_hash": row["request_hash"],
        "response_body": json.loads(row["response_body"]) if row["response_body"] else None,
        "status_code": row["status_code"],
    }


async def save_idempotency_key(
    pool: asyncpg.Pool,
    key_hash: bytes,
    request_hash: bytes,
    response_body: dict,
    status_code: int,
) -> None:
    """Save or update an idempotency key with response data."""
    await pool.execute(
        """
        INSERT INTO idempotency_keys (key_hash, request_hash, response_body, status_code, locked_at)
        VALUES ($1, $2, $3, $4, NULL)
        ON CONFLICT (key_hash)
        DO UPDATE SET response_body=$3, status_code=$4, locked_at=NULL
        """,
        key_hash,
        request_hash,
        json.dumps(response_body),
        status_code,
    )


async def lock_idempotency_key(pool: asyncpg.Pool, key_hash: bytes) -> bool:
    """Attempt to lock an idempotency key. Returns False if already locked."""
    result = await pool.execute(
        """
        INSERT INTO idempotency_keys (key_hash, request_hash, locked_at)
        VALUES ($1, $2, now())
        ON CONFLICT (key_hash) DO NOTHING
        """,
        key_hash,
        b"",  # placeholder, will be updated on save
    )
    return result != "INSERT 0 0"


async def unlock_idempotency_key(pool: asyncpg.Pool, key_hash: bytes) -> None:
    """Unlock and remove a locked idempotency key (on failure before save)."""
    await pool.execute(
        "DELETE FROM idempotency_keys WHERE key_hash=$1 AND response_body IS NULL",
        key_hash,
    )
