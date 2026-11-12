import logging
from unittest.mock import MagicMock

import pytest

from psacc import notify
from psacc.notify import DiscordNotifier
from psacc.store import AppConfig

URL = "https://discord.com/api/webhooks/123/SECRET-TOKEN"


def make(cfg: AppConfig) -> DiscordNotifier:
    return DiscordNotifier(lambda: cfg)


@pytest.fixture
def post(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(notify.requests, "post", mock)
    return mock


def test_alert_posts_content(post):
    make(AppConfig(discord_webhook=URL)).alert("falha")

    post.assert_called_once_with(URL, json={"content": "falha"}, timeout=10)


def test_info_suppressed_by_default(post):
    make(AppConfig(discord_webhook=URL)).info("carga iniciada")

    post.assert_not_called()


def test_info_posted_when_enabled(post):
    make(AppConfig(discord_webhook=URL, discord_info=True)).info("carga iniciada")

    post.assert_called_once_with(URL, json={"content": "carga iniciada"}, timeout=10)


def test_no_webhook_noop(post):
    notifier = make(AppConfig(discord_webhook=None, discord_info=True))

    notifier.alert("falha")
    notifier.info("carga iniciada")

    assert notifier.test() is False
    post.assert_not_called()


def test_notifier_swallows_errors(post, caplog):
    post.side_effect = ConnectionError(f"Max retries exceeded with url: {URL}")

    with caplog.at_level(logging.WARNING, logger="psacc.notify"):
        make(AppConfig(discord_webhook=URL)).alert("falha")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "SECRET-TOKEN" not in caplog.text
    assert URL not in caplog.text


def test_long_message_truncated_to_discord_limit(post):
    make(AppConfig(discord_webhook=URL)).alert("x" * 5000)

    content = post.call_args.kwargs["json"]["content"]
    assert len(content) == 2000
    assert content.endswith("…")


def test_test_sends_confirmation(post):
    assert make(AppConfig(discord_webhook=URL)).test() is True

    post.assert_called_once_with(URL, json={"content": "✅ PSACC ligado ao Discord"}, timeout=10)
