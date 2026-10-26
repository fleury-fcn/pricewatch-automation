"""Règles d'alerte et notifieurs (console, e-mail via smtplib, Telegram, Discord)."""

from __future__ import annotations

import smtplib
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from email.message import EmailMessage
from html import escape

import httpx
import pandas as pd

from pricewatch.config import AlertSettings, Secrets
from pricewatch.formatting import fmt_money, fmt_number
from pricewatch.logging_setup import get_logger
from pricewatch.storage.repository import AlertRecord

logger = get_logger(__name__)

KIND_LABELS = {
    "target_reached": "Prix cible atteint",
    "price_drop": "Baisse significative",
    "anomaly": "Prix anormal",
}
_KIND_PRIORITY = {"target_reached": 0, "price_drop": 1, "anomaly": 2}


def build_alerts(
    stats: pd.DataFrame,
    settings: AlertSettings | None = None,
    recent: Iterable[tuple[int, str]] = (),
) -> list[AlertRecord]:
    """Transforme les indicateurs produits en alertes.

    Args:
        stats: Sortie de :func:`pricewatch.analysis.compute_product_stats`.
        settings: Types d'alertes actifs et plafond par exécution.
        recent: Couples ``(product_id, kind)`` déjà alertés pendant le délai de
            ``cooldown`` ; ils sont ignorés pour éviter le spam.

    Returns:
        Les alertes triées par priorité (cible > baisse > anomalie) puis par ampleur.
    """
    settings = settings or AlertSettings()
    skip = set(recent)
    alerts: list[tuple[int, float, AlertRecord]] = []
    for row in stats.itertuples(index=False):
        pid, cur = int(row.product_id), str(row.currency)
        candidates: list[AlertRecord] = []
        if row.target_reached:
            candidates.append(
                AlertRecord(
                    pid,
                    "target_reached",
                    f"« {row.title} » est à {fmt_money(row.last_price, cur)} "
                    f"(cible {fmt_money(row.target_price, cur)}).",
                    float(row.last_price),
                    float(row.target_price),
                    None,
                )
            )
        if row.is_significant_drop:
            candidates.append(
                AlertRecord(
                    pid,
                    "price_drop",
                    f"« {row.title} » baisse de {fmt_number(abs(row.drop_pct), 1)} % : "
                    f"{fmt_money(row.last_price, cur)} contre {fmt_money(row.reference_price, cur)} "
                    "en moyenne récente.",
                    float(row.last_price),
                    float(row.reference_price),
                    float(row.drop_pct),
                )
            )
        if row.is_anomaly:
            direction = "anormalement haut" if row.zscore > 0 else "anormalement bas"
            candidates.append(
                AlertRecord(
                    pid,
                    "anomaly",
                    f"« {row.title} » : prix {direction} ({fmt_money(row.last_price, cur)}, "
                    f"z-score {'+' if row.zscore > 0 else '−'}{fmt_number(abs(row.zscore), 1)}).",
                    float(row.last_price),
                    None if pd.isna(row.reference_price) else float(row.reference_price),
                    None if pd.isna(row.drop_pct) else float(row.drop_pct),
                )
            )
        for alert in candidates:
            if alert.kind in settings.kinds and (pid, alert.kind) not in skip:
                magnitude = -abs(alert.change_pct or 0.0)
                alerts.append((_KIND_PRIORITY[alert.kind], magnitude, alert))
    alerts.sort(key=lambda item: (item[0], item[1]))
    return [alert for *_, alert in alerts[: settings.max_alerts_per_run]]


def format_text(alerts: Sequence[AlertRecord], title: str = "PriceWatch") -> str:
    """Résumé texte brut des alertes."""
    lines = [f"{title} — {len(alerts)} alerte(s)"]
    lines += [f"• [{KIND_LABELS.get(a.kind, a.kind)}] {a.message}" for a in alerts]
    return "\n".join(lines)


def format_html(alerts: Sequence[AlertRecord], title: str = "PriceWatch") -> str:
    """Résumé HTML des alertes (corps d'e-mail)."""
    items = "".join(
        f"<li><strong>{escape(KIND_LABELS.get(a.kind, a.kind))}</strong> — {escape(a.message)}</li>"
        for a in alerts
    )
    return f"<h2>{escape(title)} — {len(alerts)} alerte(s)</h2><ul>{items}</ul>"


class Notifier(ABC):
    """Canal de notification."""

    name: str

    @abstractmethod
    def send(self, alerts: Sequence[AlertRecord]) -> None:
        """Envoie les alertes ; lève une exception en cas d'échec."""


class ConsoleNotifier(Notifier):
    """Écrit les alertes dans les logs (toujours disponible)."""

    name = "console"

    def send(self, alerts: Sequence[AlertRecord]) -> None:
        for line in format_text(alerts).splitlines():
            logger.info("%s", line)


class EmailNotifier(Notifier):
    """Envoi SMTP avec STARTTLS (ex. Gmail avec mot de passe d'application)."""

    name = "email"

    def __init__(self, secrets: Secrets, smtp_factory: type[smtplib.SMTP] = smtplib.SMTP) -> None:
        if not (secrets.smtp_host and secrets.alert_email_to):
            raise ValueError("SMTP_HOST et ALERT_EMAIL_TO sont requis pour les alertes e-mail")
        self.secrets = secrets
        self._smtp_factory = smtp_factory

    def send(self, alerts: Sequence[AlertRecord]) -> None:
        s = self.secrets
        message = EmailMessage()
        message["Subject"] = f"[PriceWatch] {len(alerts)} alerte(s) de prix"
        message["From"] = s.alert_email_from or s.smtp_user or "pricewatch@localhost"
        message["To"] = s.alert_email_to
        message.set_content(format_text(alerts))
        message.add_alternative(format_html(alerts), subtype="html")
        with self._smtp_factory(s.smtp_host or "", s.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if s.smtp_user and s.smtp_password:
                smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
            smtp.send_message(message)


class TelegramNotifier(Notifier):
    """Message via l'API Bot Telegram."""

    name = "telegram"
    MAX_LENGTH = 4000

    def __init__(self, secrets: Secrets, client: httpx.Client | None = None) -> None:
        if not (secrets.telegram_bot_token and secrets.telegram_chat_id):
            raise ValueError("TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID sont requis")
        self.token = secrets.telegram_bot_token.get_secret_value()
        self.chat_id = secrets.telegram_chat_id
        self.client = client or httpx.Client(timeout=15)

    def send(self, alerts: Sequence[AlertRecord]) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for chunk in _split(format_text(alerts), self.MAX_LENGTH):
            self.client.post(url, json={"chat_id": self.chat_id, "text": chunk}).raise_for_status()


class DiscordNotifier(Notifier):
    """Message via un webhook Discord."""

    name = "discord"
    MAX_LENGTH = 1900

    def __init__(self, secrets: Secrets, client: httpx.Client | None = None) -> None:
        if not secrets.discord_webhook_url:
            raise ValueError("DISCORD_WEBHOOK_URL est requis")
        self.webhook = secrets.discord_webhook_url.get_secret_value()
        self.client = client or httpx.Client(timeout=15)

    def send(self, alerts: Sequence[AlertRecord]) -> None:
        for chunk in _split(format_text(alerts), self.MAX_LENGTH):
            self.client.post(self.webhook, json={"content": chunk}).raise_for_status()


def _split(text: str, size: int) -> list[str]:
    """Découpe un texte en morceaux d'au plus ``size`` caractères, aux sauts de ligne."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return [c.rstrip("\n") for c in chunks]


_NOTIFIERS: dict[str, type[Notifier]] = {
    "console": ConsoleNotifier,
    "email": EmailNotifier,
    "telegram": TelegramNotifier,
    "discord": DiscordNotifier,
}


def build_notifiers(channels: Iterable[str], secrets: Secrets) -> list[Notifier]:
    """Instancie les notifieurs demandés ; ceux mal configurés sont ignorés (avec log)."""
    notifiers: list[Notifier] = []
    for channel in channels:
        cls = _NOTIFIERS[channel]
        try:
            notifiers.append(cls() if cls is ConsoleNotifier else cls(secrets))  # type: ignore[call-arg]
        except ValueError as exc:
            logger.warning("Canal %s désactivé : %s", channel, exc)
    return notifiers


def dispatch(alerts: Sequence[AlertRecord], notifiers: Iterable[Notifier]) -> list[str]:
    """Envoie les alertes sur chaque canal ; un canal en échec n'empêche pas les autres.

    Returns:
        Noms des canaux sur lesquels l'envoi a réussi.
    """
    if not alerts:
        return []
    delivered: list[str] = []
    for notifier in notifiers:
        try:
            notifier.send(alerts)
            delivered.append(notifier.name)
        except Exception as exc:  # réseau, SMTP, quota... : on journalise et on continue
            logger.error("Échec d'envoi via %s : %s", notifier.name, exc)
    return delivered
