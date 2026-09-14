from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseDownload
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Не установлены Google API зависимости. Установите: "
        "pip install google-api-python-client google-auth"
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportSheetError(RuntimeError):
    pass


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")


def _resolve_project_path(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def _extract_spreadsheet_id(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ExportSheetError("Не задана исходная Google таблица.")
    match = SPREADSHEET_ID_RE.search(raw)
    return match.group(1) if match else raw


def _resolve_source_spreadsheet_id(arg_value: str | None) -> str:
    candidate = (
        arg_value
        or os.getenv("GOOGLE_SHEET_URL")
        or os.getenv("GOOGLE_SHEET_ID")
        or os.getenv("SPREADSHEET_URL")
        or os.getenv("SPREADSHEET_ID")
    )
    return _extract_spreadsheet_id(candidate or "")


def _get_credentials() -> Credentials:
    json_inline = (
        os.getenv("GOOGLE_CREDENTIALS_JSON")
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    )
    json_path = (
        os.getenv("GOOGLE_CREDENTIALS_FILE")
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]

    if json_inline:
        return Credentials.from_service_account_info(json.loads(json_inline), scopes=scopes)
    if json_path:
        return Credentials.from_service_account_file(
            _resolve_project_path(json_path),
            scopes=scopes,
        )
    raise ExportSheetError("Не заданы Google credentials.")


def _build_drive_service(credentials: Credentials):
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^\w .()А-Яа-я-]+", "_", value, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "google_sheet"


def _get_source_name(drive_service, file_id: str) -> str:
    metadata = (
        drive_service.files()
        .get(fileId=file_id, fields="id,name", supportsAllDrives=True)
        .execute()
    )
    return str(metadata.get("name") or file_id)


def _export_xlsx(*, drive_service, file_id: str) -> bytes:
    request = drive_service.files().export_media(
        fileId=file_id,
        mimeType=XLSX_MIME,
    )
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export configured Google Sheet to a local .xlsx file.",
    )
    parser.add_argument(
        "--spreadsheet",
        help="Source spreadsheet id or URL. Defaults to GOOGLE_SHEET_URL/GOOGLE_SHEET_ID.",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("GOOGLE_SHEET_XLSX_DIR", "exports/google_sheets"),
        help="Local directory for xlsx backups.",
    )
    parser.add_argument(
        "--output-file",
        help="Exact output .xlsx path. Overrides --output-dir.",
    )
    return parser.parse_args()


def main() -> int:
    _load_env()
    args = parse_args()

    source_id = _resolve_source_spreadsheet_id(args.spreadsheet)
    credentials = _get_credentials()
    drive_service = _build_drive_service(credentials)

    try:
        source_name = _get_source_name(drive_service, source_id)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if args.output_file:
            output_path = _resolve_project_path(args.output_file)
        else:
            output_dir = _resolve_project_path(args.output_dir)
            filename = f"{_safe_filename(source_name)}_{timestamp}.xlsx"
            output_path = output_dir / filename

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_export_xlsx(drive_service=drive_service, file_id=source_id))

        print("Excel копия Google таблицы создана.")
        print(f"Название: {source_name}")
        print(f"FILE={output_path}")
        return 0
    except HttpError as exc:
        raise ExportSheetError(f"Google API error: {exc}") from exc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExportSheetError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
