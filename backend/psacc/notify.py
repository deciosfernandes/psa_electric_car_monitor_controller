import logging
from collections.abc import Callable

import requests

from psacc.store import AppConfig

logger = logging.getLogger(__name__)

DISCORD_CONTENT_LIMIT = 2000
TIMEOUT_S = 10


class DiscordNotifier:
    def __init__(self, get_cfg: Callable[[], AppConfig]):
        self._get_cfg = get_cfg

    def alert(self, msg: str) -> None:
        url = self._get_cfg().discord_webhook
        if url:
            self._post(url, msg)

    def info(self, msg: str) -> None:
        cfg = self._get_cfg()
        if cfg.discord_webhook and cfg.discord_info:
            self._post(cfg.discord_webhook, msg)

    def test(self) -> bool:
        url = self._get_cfg().discord_webhook
        if not url:
            return False
        return self._post(url, "✅ PSACC ligado ao Discord")

    def _post(self, url: str, msg: str) -> bool:
        if len(msg) > DISCORD_CONTENT_LIMIT:
            msg = msg[: DISCORD_CONTENT_LIMIT - 1] + "…"
        try:
            response = requests.post(url, json={"content": msg}, timeout=TIMEOUT_S)
            response.raise_for_status()
        except Exception as exc:
            # Never log str(exc): requests errors can embed the webhook URL (a secret).
            logger.warning("Falha ao enviar notificação para o Discord: %s", type(exc).__name__)
            return False
        return True
