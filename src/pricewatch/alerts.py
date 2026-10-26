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
from pricewatch.formatting import fmt_money, fmt_number, fmt_pct
from pricewatch.i18n import DEFAULT_LANG, kind_label, tr
from pricewatch.logging_setup import get_logger
from pricewatch.storage.repository import AlertRecord

logger = get_logger(__name__)

# Libellés français conservés pour le dashboard (interface en français)
KIND_LABELS = {kind: kind_label("fr", kind) for kind in ("target_reached", "price_drop", "anomaly")}
_KIND_PRIORITY = {"target_reached": 0, "price_drop": 1, "anomaly": 2}


def _opt(value: object) -> float | None:
    return None if value is None or pd.isna(value) else float(value)  # type: ignore[arg-type]


def alert_message(
    kind: str,
    title: str,
    price: float,
    reference: float | None,
    change_pct: float | None,
    currency: str,
    lang: str = DEFAULT_LANG,
    zscore: float | None = None,
) -> str:
    """Rédige le message d'une alerte dans la langue demandée.

    Le message est reconstruit à partir des champs structurés de l'alerte : un même
    enregistrement peut donc être affiché en français ou en anglais.

    Args:
        kind: ``target_reached``, ``price_drop`` ou ``anomaly``.
        title: Titre du produit.
        price: Prix observé.
        reference: Prix cible (``target_reached``) ou moyenne récente (autres types).
        change_pct: Écart en % au prix de référence.
        currency: Devise ISO.
        lang: ``fr`` ou ``en``.
        zscore: z-score du prix (anomalies), s'il est connu.
    """
    price_txt = fmt_money(price, currency, lang=lang)
    ref_txt = fmt_money(reference, currency, lang=lang)
    if kind == "target_reached":
        return tr(lang, "msg_target_reached", title=title, price=price_txt, ref=ref_txt)
    if kind == "price_drop":
        pct = fmt_pct(abs(change_pct or 0.0), signed=False, lang=lang)
        return tr(lang, "msg_price_drop", title=title, pct=pct, price=price_txt, ref=ref_txt)
    if zscore is not None:
        direction_value: float | None = zscore
    elif change_pct is not None:
        direction_value = change_pct
    else:
        direction_value = None
    if direction_value is None or direction_value == 0:
        direction = tr(lang, "dir_unknown")
    else:
        direction = tr(lang, "dir_high" if direction_value > 0 else "dir_low")
    if zscore is not None:
        z = f"{'+' if zscore > 0 else '−'}{fmt_number(abs(zscore), 1, lang=lang)}"
        return tr(lang, "msg_anomaly_z", title=title, direction=direction, price=price_txt, z=z)
    if reference is not None:
        return tr(
            lang, "msg_anomaly", title=title, direction=direction, price=price_txt, ref=ref_txt
        )
    return tr(lang, "msg_anomaly_plain", title=title, direction=direction, price=price_txt)


def build_alerts(
    stats: pd.DataFrame,
    settings: AlertSettings | None = None,
    recent: Iterable[tuple[int, str]] = (),
) -> list[AlertRecord]:
    """Transforme les indicateurs produits en alertes.

    Args:
        stats: Sortie de :func:`pricewatch.analysis.compute_product_stats`.
        settings: Types d'alertes actifs, plafond par exécution et langue des messages.
        recent: Couples ``(product_id, kind)`` déjà alertés pendant le délai de
            ``cooldown`` ; ils sont ignorés pour éviter le spam.

    Returns:
        Les alertes triées par priorité (cible > baisse > anomalie) puis par ampleur.
    """
    settings = settings or AlertSettings()
    lang = settings.language
    skip = set(recent)
    alerts: list[tuple[int, float, AlertRecord]] = []
    for row in stats.itertuples(index=False):
        pid, cur, title = int(row.product_id), str(row.currency), str(row.title)
        price = float(row.last_price)
        reference, drop = _opt(row.reference_price), _opt(row.drop_pct)
        candidates: list[AlertRecord] = []
        if row.target_reached:
            target = float(row.target_price)
            message = alert_message("target_reached", title, price, target, None, cur, lang)
            candidates.append(AlertRecord(pid, "target_reached", message, price, target, None))
        if row.is_significant_drop:
            message = alert_message("price_drop", title, price, reference, drop, cur, lang)
            candidates.append(AlertRecord(pid, "price_drop", message, price, reference, drop))
        if row.is_anomaly:
            message = alert_message(
                "anomaly", title, price, reference, drop, cur, lang, zscore=float(row.zscore)
            )
            candidates.append(AlertRecord(pid, "anomaly", message, price, reference, drop))
        for alert in candidates:
            if alert.kind in settings.kinds and (pid, alert.kind) not in skip:
                magnitude = -abs(alert.change_pct or 0.0)
                alerts.append((_KIND_PRIORITY[alert.kind], magnitude, alert))
    alerts.sort(key=lambda item: (item[0], item[1]))
    return [alert for *_, alert in alerts[: settings.max_alerts_per_run]]


def format_text(
    alerts: Sequence[AlertRecord], title: str = "PriceWatch", lang: str = DEFAULT_LANG
) -> str:
    """Résumé texte brut des alertes."""
    lines = [tr(lang, "alerts_header", title=title, n=len(alerts))]
    lines += [f"• [{kind_label(lang, a.kind)}] {a.message}" for a in alerts]
    return "\n".join(lines)


def format_html(
    alerts: Sequence[AlertRecord], title: str = "PriceWatch", lang: str = DEFAULT_LANG
) -> str:
    """Résumé HTML des alertes (corps d'e-mail)."""
    items = "".join(
        f"<li><strong>{escape(kind_label(lang, a.kind))}</strong> — {escape(a.message)}</li>"
        for a in alerts
    )
    header = escape(tr(lang, "alerts_header", title=title, n=len(alerts)))
    return f"<h2>{header}</h2><ul>{items}</ul>"


class Notifier(ABC):
    """Canal de notification (``lang`` : langue des messages envoyés)."""

    name: str
    lang: str = DEFAULT_LANG

    @abstractmethod
    def send(self, alerts: Sequence[AlertRecord]) -> None:
        """Envoie les alertes ; lève une exception en cas d'échec."""


class ConsoleNotifier(Notifier):
    """Écrit les alertes dans les logs (toujours disponible)."""

    name = "console"

    def send(self, alerts: Sequence[AlertRecord]) -> None:
        """Journalise chaque alerte au niveau INFO."""
        for line in format_text(alerts, lang=self.lang).splitlines():
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
        """Envoie un e-mail multipart (texte + HTML) récapitulant les alertes."""
        s = self.secrets
        message = EmailMessage()
        message["Subject"] = tr(self.lang, "alerts_subject", n=len(alerts))
        message["From"] = s.alert_email_from or s.smtp_user or "pricewatch@localhost"
        message["To"] = s.alert_email_to
        message.set_content(format_text(alerts, lang=self.lang))
        message.add_alternative(format_html(alerts, lang=self.lang), subtype="html")
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
        """Envoie le récapitulatif via sendMessage, découpé sous la limite de Telegram."""
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for chunk in _split(format_text(alerts, lang=self.lang), self.MAX_LENGTH):
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
        """Poste le récapitulatif sur le webhook, découpé sous la limite de Discord."""
        for chunk in _split(format_text(alerts, lang=self.lang), self.MAX_LENGTH):
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


def build_notifiers(
    channels: Iterable[str], secrets: Secrets, lang: str = DEFAULT_LANG
) -> list[Notifier]:
    """Instancie les notifieurs demandés ; ceux mal configurés sont ignorés (avec log)."""
    notifiers: list[Notifier] = []
    for channel in channels:
        cls = _NOTIFIERS[channel]
        try:
            notifier = cls() if cls is ConsoleNotifier else cls(secrets)  # type: ignore[call-arg]
        except ValueError as exc:
            logger.warning("Canal %s désactivé : %s", channel, exc)
            continue
        notifier.lang = lang
        notifiers.append(notifier)
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
