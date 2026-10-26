import pytest

from pricewatch.alerts import alert_message, build_notifiers, format_text
from pricewatch.config import AppConfig, Secrets
from pricewatch.formatting import fmt_date, fmt_money, fmt_number, fmt_pct
from pricewatch.i18n import LANGUAGES, TEXTS, check_lang, kind_label, tr, trend_label
from pricewatch.storage import AlertRecord


def test_every_key_exists_in_every_language() -> None:
    keys = {lang: set(TEXTS[lang]) for lang in LANGUAGES}
    assert keys["fr"] == keys["en"]


def test_placeholders_match_between_languages() -> None:
    import string

    def fields(text: str) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}

    for key in TEXTS["fr"]:
        assert fields(TEXTS["fr"][key]) == fields(TEXTS["en"][key]), key


def test_tr_and_labels() -> None:
    assert tr("en", "summary_alerts", n=3) == "3 alert(s) raised during the latest run."
    assert tr("fr", "section_summary") == "Résumé exécutif"
    assert trend_label("en", "baisse") == "falling" and trend_label("fr", "hausse") == "hausse"
    assert trend_label("en", "inconnu") == "inconnu"
    assert kind_label("en", "price_drop") == "Significant drop"
    with pytest.raises(ValueError):
        check_lang("de")


def test_english_formatting() -> None:
    assert fmt_number(1234.5, lang="en") == "1,234.50"
    assert fmt_money(51.77, "GBP", lang="en") == "£51.77"
    assert fmt_money(12, "CHF", lang="en") == "CHF 12.00"
    assert fmt_money(None, "GBP", lang="en") == "—"
    assert fmt_pct(-12.34, lang="en") == "−12.3%"
    assert fmt_pct(4, lang="en") == "+4.0%"
    assert fmt_date("2026-10-26T06:00:00Z", lang="en") == "26 Oct 2026"
    assert fmt_date("2026-01-05T06:00:00Z", with_time=True, lang="en") == "5 Jan 2026 06:00 UTC"


@pytest.mark.parametrize(
    ("kind", "args", "fr", "en"),
    [
        ("target_reached", (47.47, 48.0, None), "est à 47,47", "is at £47.47 (target £48.00)"),
        ("price_drop", (20.41, 23.85, -14.4), "baisse de 14,4", "is down 14.4%: £20.41"),
        (
            "anomaly",
            (35.0, 24.0, 45.8),
            "anormalement haut (35,00",
            "abnormally high price (£35.00 vs",
        ),
        ("anomaly", (15.0, 24.0, -37.5), "anormalement bas", "abnormally low"),
        ("anomaly", (15.0, None, None), "prix anormal (15,00", "abnormal price (£15.00)"),
    ],
)
def test_alert_messages_in_both_languages(
    kind: str, args: tuple[float, float | None, float | None], fr: str, en: str
) -> None:
    price, ref, pct = args
    assert fr in alert_message(kind, "Livre", price, ref, pct, "GBP", "fr")
    assert en in alert_message(kind, "Book", price, ref, pct, "GBP", "en")


def test_anomaly_message_with_zscore() -> None:
    assert "z-score +3.2" in alert_message("anomaly", "B", 30, 20, 50, "GBP", "en", zscore=3.2)
    assert "z-score −2,7" in alert_message("anomaly", "B", 10, 20, -50, "GBP", "fr", zscore=-2.7)


def test_notifications_follow_alert_language(test_config: AppConfig) -> None:
    alerts = [AlertRecord(1, "price_drop", "Book is down", 10.0, 20.0, -50.0)]
    assert format_text(alerts, lang="en").startswith("PriceWatch — 1 alert(s)")
    assert "[Significant drop]" in format_text(alerts, lang="en")
    notifier = build_notifiers(["console"], Secrets(_env_file=None), lang="en")[0]
    assert notifier.lang == "en"


def test_config_languages_validation(test_config: AppConfig) -> None:
    from pydantic import ValidationError

    from pricewatch.config import ReportSettings

    assert test_config.report.languages == ["fr", "en"]
    with pytest.raises(ValidationError):
        ReportSettings(languages=[])
    with pytest.raises(ValidationError):
        ReportSettings(languages=["fr", "fr"])
    with pytest.raises(ValidationError):
        ReportSettings(languages=["de"])  # type: ignore[list-item]
