from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup


def extract_csrf_from_html(html_text: str) -> str | None:
    soup = BeautifulSoup(html_text, "html.parser")

    meta = soup.select_one('meta[name="csrf-token"]')
    if meta and meta.get("content"):
        return meta["content"]

    hidden = soup.select_one('input[name="_csrf"]')
    if hidden and hidden.get("value"):
        return hidden["value"]

    patterns = [
        r'csrf-token"\s+content="([^"]+)"',
        r'name="_csrf"\s+value="([^"]+)"',
        r'"_csrf"\s*:\s*"([^"]+)"',
        r"'_csrf'\s*:\s*'([^']+)'",
    ]
    for pattern in patterns:
        m = re.search(pattern, html_text, re.I)
        if m:
            return m.group(1)

    return None


def find_hidden_csrf_field(html_text: str) -> str | None:
    soup = BeautifulSoup(html_text, "html.parser")

    for selector in ('input[name="_csrf"]', 'input[name="csrf"]', 'input[name*="csrf"]'):
        hidden = soup.select_one(selector)
        if hidden and hidden.get("value"):
            return hidden["value"]

    patterns = [
        r'name="_csrf"\s+value="([^"]+)"',
        r'name="[^"]*csrf[^"]*"\s+value="([^"]+)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html_text, re.I)
        if m:
            return m.group(1)

    return None


def extract_amlcheckup(obj: Any) -> str | None:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)

    patterns = [
        r'urlParams\[amlcheckup\][^0-9a-fA-F]*([0-9a-fA-F\-]{36})',
        r'urlParams%5Bamlcheckup%5D=([0-9a-fA-F\-]{36})',
        r'amlcheckup=([0-9a-fA-F\-]{36})',
        r'"amlcheckup"\s*:\s*"([0-9a-fA-F\-]{36})"',
        r"'amlcheckup'\s*:\s*'([0-9a-fA-F\-]{36})'",
        r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def extract_amlcheckup_from_redirect_header(headers: dict[str, Any]) -> str | None:
    redirect_value = headers.get("x-redirect") or headers.get("X-Redirect")
    if not redirect_value:
        return None
    return extract_amlcheckup(redirect_value)


def _extract_label_value(text: str, label: str) -> str | None:
    pattern = rf'{re.escape(label)}\s*:\s*<span>(.*?)</span>'
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None

    value = re.sub(r"<.*?>", "", m.group(1), flags=re.S)
    value = html.unescape(value).strip()
    return value or None


def parse_report_preview(preview_html: str, amlcheckup: str, *, base_url: str, lang: str) -> dict[str, Any]:
    soup = BeautifulSoup(preview_html, "html.parser")

    info_block = soup.select_one("#report-info")
    text = str(info_block) if info_block else preview_html

    report_date = _extract_label_value(text, "Report date")
    aml_provider = _extract_label_value(text, "AML Provider")
    blockchain = _extract_label_value(text, "Blockchain")
    token = _extract_label_value(text, "Token")
    type_value = _extract_label_value(text, "Type")
    hash_value = _extract_label_value(text, "Hash")
    counterparty = _extract_label_value(text, "Counterparty")

    risk_percent = ""
    risk_label = ""
    risk_match = re.search(
        r'Risk level:\s*<span>\s*([^<]+?)\s*</span>\s*\((.*?)\)',
        text,
        re.I | re.S,
    )
    if risk_match:
        risk_percent = html.unescape(risk_match.group(1)).strip()
        risk_label = re.sub(r"<.*?>", "", risk_match.group(2), flags=re.S).strip()

    formatted_date = report_date or ""
    if report_date:
        try:
            dt = datetime.strptime(report_date, "%Y-%m-%d %H:%M (UTC)")
            formatted_date = dt.strftime("%d.%m.%Y %H:%M")
        except ValueError:
            pass

    asset_name_parts: list[str] = []
    if blockchain and blockchain.strip():
        asset_name_parts.append(blockchain.strip())
    if token and token.strip():
        asset_name_parts.append(token.strip())
    asset_name = " ".join(asset_name_parts).strip() or (token or "Адрес")

    risk_emoji = "🟢"
    risk_label_ru = "Низкий уровень риска"
    risk_label_lower = risk_label.lower()

    if "high" in risk_label_lower or "critical" in risk_label_lower:
        risk_emoji = "🔴"
        risk_label_ru = "Высокий уровень риска"
    elif "medium" in risk_label_lower or "moderate" in risk_label_lower:
        risk_emoji = "🟡"
        risk_label_ru = "Средний уровень риска"

    def parse_source_group(title_en: str) -> list[str]:
        groups = soup.select(".report-source-list .source")

        for group in groups:
            title_el = group.select_one(".source-title")
            if not title_el:
                continue

            group_title = title_el.get_text(" ", strip=True).lower()
            if title_en.lower() not in group_title:
                continue

            items: list[str] = []
            for li in group.select(".source-list .item"):
                item_text = re.sub(r"\s+", " ", li.get_text(" ", strip=True)).strip()
                percent_match = re.search(r"(\d+(?:\.\d+)?)%$", item_text)
                if not percent_match:
                    continue
                if float(percent_match.group(1)) == 0:
                    continue
                item_text = item_text.replace("P2p", "P2P").replace("Atm", "ATM")
                items.append(item_text)

            return items

        return []

    trusted_sources = parse_source_group("Trusted sources")
    suspicious_sources = parse_source_group("Suspicious sources")
    dangerous_sources = parse_source_group("Dangerous sources")

    preview_link = f"{base_url}/{lang}/report-preview/{amlcheckup}"

    return {
        "asset_name": asset_name,
        "blockchain": blockchain or "",
        "token": token or "",
        "type": type_value or "",
        "hash": hash_value or "",
        "risk_percent": risk_percent or "",
        "risk_label": risk_label or "",
        "counterparty": counterparty or "unknown",
        "report_date": formatted_date,
        "aml_provider": aml_provider or "",
        "preview_link": preview_link,
        "risk_emoji": risk_emoji,
        "risk_label_ru": risk_label_ru,
        "trusted_sources": trusted_sources,
        "suspicious_sources": suspicious_sources,
        "dangerous_sources": dangerous_sources,
    }


def build_report_message(report_data: dict[str, Any]) -> str:
    asset_name = report_data.get("asset_name", "Адрес")
    hash_value = report_data.get("hash", "")
    risk_emoji = report_data.get("risk_emoji", "🟢")
    risk_label_ru = report_data.get("risk_label_ru", "Низкий уровень риска")
    risk_percent = report_data.get("risk_percent", "")
    counterparty = report_data.get("counterparty", "unknown")
    report_date = report_data.get("report_date", "")
    preview_link = report_data.get("preview_link", "")

    trusted_sources = report_data.get("trusted_sources", [])
    suspicious_sources = report_data.get("suspicious_sources", [])
    dangerous_sources = report_data.get("dangerous_sources", [])

    parts = [
        f"{asset_name} Адрес:",
        f"{hash_value}",
        "",
        f"{risk_emoji} {risk_label_ru}: {risk_percent}",
        f"📅 Дата AML проверки: {report_date}",
        f"Контрагент: {counterparty}",
        "",
    ]

    if trusted_sources:
        parts.append("✅ Доверенные источники")
        parts.extend(trusted_sources)
        parts.append("")

    if suspicious_sources:
        parts.append("⚠️ Подозрительные источники")
        parts.extend(suspicious_sources)
        parts.append("")

    if dangerous_sources:
        parts.append("🛑 Опасные источники")
        parts.extend(dangerous_sources)
        parts.append("")

    parts.extend(["Полный отчет:", preview_link])
    return "\n".join(parts)