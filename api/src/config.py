from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    valhalla_url: str = "http://localhost:8002"
    database_url: str = "postgresql://freight:freight@localhost:5432/freight"
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl: int = 21600  # 6 hours in seconds
    epqs_base_url: str = "https://epqs.nationalmap.gov/v1/json"
    elevation_water_floor: float = -500.0  # treat elevations below this as no-data
    rate_limit_per_minute: int = 60
    async_timeout_seconds: float = 3.0  # fast-path threshold for hybrid sync/async
    webhook_retry_attempts: int = 3
    webhook_timeout_seconds: float = 5.0
    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
