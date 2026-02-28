import hashlib
import json


def compute_request_hash(request_body: dict) -> bytes:
    """SHA-256 hash of the canonicalized request JSON (sorted keys, no whitespace)."""
    canonical = json.dumps(request_body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).digest()


def request_cache_key(request_hash: bytes) -> str:
    return f"req_cache:{request_hash.hex()}"


def route_cache_key(route_id: str) -> str:
    return f"route:{route_id}"


def telemetry_cache_key(route_id: str) -> str:
    return f"telemetry:{route_id}"
