"""Webhook delivery with retry."""

import logging

import httpx

from src.config import Settings

logger = logging.getLogger(__name__)


async def deliver_webhook(
    url: str,
    event: dict,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    """
    POST the event JSON to the webhook URL.
    Retry up to settings.webhook_retry_attempts times with exponential backoff (1s, 2s, 4s).
    Use settings.webhook_timeout_seconds per attempt.
    Log failures but do not raise — webhook delivery is best-effort.
    """
    import asyncio

    for attempt in range(settings.webhook_retry_attempts):
        try:
            response = await http_client.post(
                url,
                json=event,
                timeout=settings.webhook_timeout_seconds,
            )
            if response.status_code < 300:
                logger.info("Webhook delivered to %s (attempt %d)", url, attempt + 1)
                return
            logger.warning(
                "Webhook to %s returned status %d (attempt %d/%d)",
                url, response.status_code, attempt + 1, settings.webhook_retry_attempts,
            )
        except (httpx.HTTPError, Exception) as exc:
            logger.warning(
                "Webhook to %s failed (attempt %d/%d): %s",
                url, attempt + 1, settings.webhook_retry_attempts, exc,
            )

        # Exponential backoff: 1s, 2s, 4s
        if attempt < settings.webhook_retry_attempts - 1:
            await asyncio.sleep(2 ** attempt)

    logger.error("Webhook delivery to %s failed after %d attempts", url, settings.webhook_retry_attempts)
