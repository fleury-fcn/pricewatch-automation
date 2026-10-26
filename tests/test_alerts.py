from email.message import EmailMessage
from typing import Any

import httpx
import pytest
import respx

from pricewatch.alerts import (
    ConsoleNotifier,
    DiscordNotifier,
    EmailNotifier,
    Notifier,
    TelegramNotifier,
    _split,
    build_alerts,
    build_notifiers,
    dispatch,
    format_html,
    format_text,
)
from pricewatch.analysis import compute_product_stats
from pricewatch.config import AlertSettings, AnalysisSettings, Secrets
from pricewatch.storage import AlertRecord
from tests.factories import history_from_prices

BASE = [20.0, 20.2, 19.8, 20.1, 19.9, 20.0, 20.1, 19.9]


def stats(**targets: float):  # type: ignore[no-untyped-def]
    history = history_from_prices({1: [*BASE, 14.0], 2: [*BASE, 31.0], 3: [*BASE, 20.0]})
    return compute_product_stats(
        history,
        AnalysisSettings(min_history_points=5),
        targets={"https://a.test/3": 25.0},
    )


def test_build_alerts_rules_and_priority() -> None:
    alerts = build_alerts(stats())
    kinds = [(a.product_id, a.kind) for a in alerts]
    assert kinds[0] == (3, "target_reached")
    assert (1, "price_drop") in kinds
    assert (1, "anomaly") in kinds and (2, "anomaly") in kinds
    drop = next(a for a in alerts if a.kind == "price_drop")
    assert drop.change_pct is not None and drop.change_pct < -10
    assert "baisse de" in drop.message
    spike = next(a for a in alerts if a.product_id == 2)
    assert "anormalement haut" in spike.message


def test_build_alerts_filters_kinds_cooldown_and_cap() -> None:
    only_drops = build_alerts(stats(), AlertSettings(kinds=["price_drop"]))
    assert {a.kind for a in only_drops} == {"price_drop"}
    cooled = build_alerts(stats(), recent=[(3, "target_reached"), (1, "anomaly")])
    assert (3, "target_reached") not in [(a.product_id, a.kind) for a in cooled]
    assert len(build_alerts(stats(), AlertSettings(max_alerts_per_run=2))) == 2


def test_formatting() -> None:
    alerts = [AlertRecord(1, "price_drop", "Baisse <b>forte</b>", 10.0, 20.0, -50.0)]
    assert "Baisse significative" in format_text(alerts)
    assert "&lt;b&gt;" in format_html(alerts)


def test_split_long_text() -> None:
    text = "\n".join(["ligne"] * 50 + ["x" * 120])
    chunks = _split(text, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent: EmailMessage | None = None
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        self.calls.append("quit")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}:{password}")

    def send_message(self, message: EmailMessage) -> None:
        self.sent = message


ALERTS = [AlertRecord(1, "price_drop", "Baisse", 10.0, 20.0, -50.0)]


def test_email_notifier() -> None:
    secrets = Secrets(
        smtp_host="smtp.test",
        smtp_port=2525,
        smtp_user="me",
        smtp_password="pw",
        alert_email_to="you@test",
    )
    EmailNotifier(secrets, smtp_factory=FakeSMTP).send(ALERTS)  # type: ignore[arg-type]
    smtp = FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.test", 2525)
    assert smtp.calls == ["starttls", "login:me:pw", "quit"]
    assert smtp.sent is not None and smtp.sent["To"] == "you@test"
    assert smtp.sent["From"] == "me"
    assert "1 alerte" in str(smtp.sent["Subject"])
    with pytest.raises(ValueError):
        EmailNotifier(Secrets(_env_file=None))


@respx.mock
def test_telegram_and_discord_notifiers() -> None:
    tg = respx.post("https://api.telegram.org/botTOKEN/sendMessage").respond(200)
    dc = respx.post("https://discord.test/hook").respond(204)
    secrets = Secrets(
        telegram_bot_token="TOKEN",
        telegram_chat_id="42",
        discord_webhook_url="https://discord.test/hook",
    )
    TelegramNotifier(secrets).send(ALERTS)
    DiscordNotifier(secrets).send(ALERTS)
    assert tg.calls.last.request.read().count(b'"chat_id":"42"') == 1
    assert b"Baisse" in dc.calls.last.request.read()
    with pytest.raises(ValueError):
        TelegramNotifier(Secrets(_env_file=None))
    with pytest.raises(ValueError):
        DiscordNotifier(Secrets(_env_file=None))


def test_build_notifiers_skips_misconfigured(caplog: pytest.LogCaptureFixture) -> None:
    notifiers = build_notifiers(["console", "email", "discord"], Secrets(_env_file=None))
    assert [n.name for n in notifiers] == ["console"]


class Broken(Notifier):
    name = "broken"

    def send(self, alerts):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("down")


def test_dispatch_isolates_failures() -> None:
    assert dispatch(ALERTS, [Broken(), ConsoleNotifier()]) == ["console"]
    assert dispatch([], [ConsoleNotifier()]) == []
