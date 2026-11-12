import logging
import queue
import threading
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


class QueuedNotifier:
    """Delivers through ``inner`` on a daemon thread, so slow webhooks never block the engine or the poller.

    Errors from ``inner`` are logged and dropped.
    """

    def __init__(self, inner):
        self._inner = inner
        self._queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="notifier", daemon=True)
        self._thread.start()

    def alert(self, msg: str) -> None:
        self._queue.put(("alert", msg))

    def info(self, msg: str) -> None:
        self._queue.put(("info", msg))

    def stop(self) -> None:
        """Let the delivery thread exit once the messages queued so far were handled."""
        self._queue.put(None)

    def join(self) -> None:
        """Block until every queued message was handled (tests)."""
        self._queue.join()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            kind, msg = item
            try:
                getattr(self._inner, kind)(msg)
            except Exception as exc:
                # not logger.exception: the error text may embed the webhook URL (a secret)
                logger.warning("notification failed: %s", type(exc).__name__)
            finally:
                self._queue.task_done()
