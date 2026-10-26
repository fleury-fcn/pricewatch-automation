"""Rapports HTML/PDF et graphiques."""

from pricewatch.reporting.report import (
    ReportData,
    build_report_data,
    executive_summary,
    generate_reports,
    render_html,
    render_pdf,
)

__all__ = [
    "ReportData",
    "build_report_data",
    "executive_summary",
    "generate_reports",
    "render_html",
    "render_pdf",
]
