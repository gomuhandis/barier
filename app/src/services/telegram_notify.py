"""Direct HTTP send to Telegram API — avoids coupling to the running bot loop."""
from __future__ import annotations

import logging

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)


async def send_message(chat_id: int, text: str) -> bool:
    token = get_settings().telegram_bot_token
    if not token:
        logger.debug("Telegram token not configured; skipping send to %s", chat_id)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
            if resp.status_code != 200:
                logger.warning(
                    "Telegram sendMessage failed chat=%s status=%s body=%s",
                    chat_id,
                    resp.status_code,
                    resp.text[:200],
                )
                return False
            return True
    except httpx.HTTPError as exc:
        logger.warning("Telegram sendMessage network error: %s", exc)
        return False
