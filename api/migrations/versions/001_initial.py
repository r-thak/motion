"""Initial schema migration.

Revision ID: 001_initial
Create Date: 2026-02-28
"""

from alembic import op

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS routes (
            id              TEXT PRIMARY KEY,
            object          TEXT NOT NULL DEFAULT 'route',
            status          TEXT NOT NULL DEFAULT 'processing',
            request_body    JSONB NOT NULL,
            request_hash    BYTEA NOT NULL,
            response_body   JSONB,
            telemetry       JSONB,
            vehicle_spec    JSONB NOT NULL,
            departure_time  TIMESTAMPTZ,
            webhook_url     TEXT,
            error           JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '6 hours'
        );

        CREATE INDEX IF NOT EXISTS idx_routes_request_hash ON routes (request_hash);
        CREATE INDEX IF NOT EXISTS idx_routes_expires_at ON routes (expires_at);
        CREATE INDEX IF NOT EXISTS idx_routes_status ON routes (status);

        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key_hash        BYTEA PRIMARY KEY,
            request_hash    BYTEA NOT NULL,
            response_body   JSONB,
            status_code     SMALLINT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            locked_at       TIMESTAMPTZ,
            expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '24 hours'
        );

        CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys (expires_at);

        CREATE TABLE IF NOT EXISTS events (
            id              TEXT PRIMARY KEY,
            type            TEXT NOT NULL,
            route_id        TEXT NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            data            JSONB NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS idx_events_route_id ON events (route_id);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events (type);

        CREATE TABLE IF NOT EXISTS zone_polygons (
            id              SERIAL PRIMARY KEY,
            zone_type       TEXT NOT NULL,
            name            TEXT,
            geometry        JSONB NOT NULL,
            bbox_min_lat    DOUBLE PRECISION NOT NULL,
            bbox_max_lat    DOUBLE PRECISION NOT NULL,
            bbox_min_lng    DOUBLE PRECISION NOT NULL,
            bbox_max_lng    DOUBLE PRECISION NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_zones_type ON zone_polygons (zone_type);
        CREATE INDEX IF NOT EXISTS idx_zones_bbox ON zone_polygons (bbox_min_lat, bbox_max_lat, bbox_min_lng, bbox_max_lng);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS zone_polygons CASCADE;
        DROP TABLE IF EXISTS events CASCADE;
        DROP TABLE IF EXISTS idempotency_keys CASCADE;
        DROP TABLE IF EXISTS routes CASCADE;
    """)
