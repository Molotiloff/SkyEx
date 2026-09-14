from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Не установлены Google API зависимости. Установите: "
        "pip install google-api-python-client google-auth"
    ) from exc


SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
DRIVE_FOLDER_ID_RE = re.compile(r"/drive/folders/([a-zA-Z0-9-_]+)")
DRIVE_FILE_URL = "https://docs.google.com/spreadsheets/d/{file_id}/edit"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COPY_FOLDER_ID = "1PaNnHOfwJCl-T-K3MyJKgFY0X0m5Qg3d"


class CopySheetError(RuntimeError):
    pass


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")


def _resolve_project_path(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return str(p)


def _extract_spreadsheet_id(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise CopySheetError("Не задана исходная Google таблица.")
    match = SPREADSHEET_ID_RE.search(raw)
    return match.group(1) if match else raw


def _extract_drive_folder_id(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    match = DRIVE_FOLDER_ID_RE.search(raw)
    return match.group(1) if match else raw


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
    scopes = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]

    if json_inline:
        return Credentials.from_service_account_info(json.loads(json_inline), scopes=scopes)
    if json_path:
        return Credentials.from_service_account_file(_resolve_project_path(json_path), scopes=scopes)
    raise CopySheetError("Не заданы Google credentials.")


def _resolve_source_spreadsheet_id(arg_value: str | None) -> str:
    candidate = (
        arg_value
        or os.getenv("GOOGLE_SHEET_URL")
        or os.getenv("GOOGLE_SHEET_ID")
        or os.getenv("SPREADSHEET_URL")
        or os.getenv("SPREADSHEET_ID")
    )
    return _extract_spreadsheet_id(candidate or "")


def _build_drive_service(credentials: Credentials):
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _get_source_name(drive_service, file_id: str) -> str:
    metadata = (
        drive_service.files()
        .get(fileId=file_id, fields="id,name", supportsAllDrives=True)
        .execute()
    )
    return str(metadata.get("name") or file_id)


def _get_folder_info(drive_service, folder_id: str | None) -> dict[str, str | None]:
    if not folder_id:
        return {"id": None, "name": None, "drive_id": None}
    metadata = (
        drive_service.files()
        .get(
            fileId=folder_id,
            fields="id,name,driveId",
            supportsAllDrives=True,
        )
        .execute()
    )
    return {
        "id": str(metadata.get("id") or folder_id),
        "name": str(metadata.get("name") or ""),
        "drive_id": str(metadata.get("driveId") or "") or None,
    }


def _copy_spreadsheet(
    *,
    drive_service,
    source_file_id: str,
    copy_name: str,
    folder_id: str | None,
) -> tuple[str, str]:
    body: dict[str, Any] = {
        "name": copy_name,
        "mimeType": "application/vnd.google-apps.spreadsheet",
    }
    if folder_id:
        body["parents"] = [folder_id]

    copied = (
        drive_service.files()
        .copy(
            fileId=source_file_id,
            body=body,
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )
    return str(copied["id"]), str(copied.get("name") or copy_name)


def _share_anyone_reader(*, drive_service, file_id: str) -> None:
    drive_service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
        supportsAllDrives=True,
    ).execute()


def _share_with_emails(*, drive_service, file_id: str, emails: list[str], role: str) -> None:
    for email in emails:
        drive_service.permissions().create(
            fileId=file_id,
            body={"type": "user", "role": role, "emailAddress": email},
            fields="id",
            sendNotificationEmail=False,
            supportsAllDrives=True,
        ).execute()


def _send_telegram_message(*, bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise CopySheetError(f"Telegram API error: {payload}")


def _parse_emails(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _is_storage_quota_error(exc: HttpError) -> bool:
    try:
        payload = json.loads(exc.content.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return "storageQuotaExceeded" in str(exc)
    errors = payload.get("error", {}).get("errors", [])
    return any(err.get("reason") == "storageQuotaExceeded" for err in errors)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy configured Google Sheet and print a public read-only link.",
    )
    parser.add_argument(
        "--spreadsheet",
        help="Source spreadsheet id or URL. Defaults to GOOGLE_SHEET_URL/GOOGLE_SHEET_ID.",
    )
    parser.add_argument(
        "--copy-name",
        help="Name for the created copy. Defaults to '<source> backup YYYY-mm-dd HH-MM'.",
    )
    parser.add_argument(
        "--folder-id",
        default=(
            os.getenv("GOOGLE_SHEET_COPY_FOLDER_ID", "").strip()
            or DEFAULT_COPY_FOLDER_ID
        ),
        help="Optional Google Drive folder id or URL for the copy.",
    )
    parser.add_argument(
        "--share-anyone-reader",
        action="store_true",
        help="Allow anyone with the link to read the copy. Enabled by default unless --private is passed.",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Do not enable public read access by link.",
    )
    parser.add_argument(
        "--share-email",
        action="append",
        default=[],
        help="Email to share the copy with. Can be repeated.",
    )
    parser.add_argument(
        "--share-role",
        choices=("reader", "writer"),
        default=os.getenv("GOOGLE_SHEET_COPY_SHARE_ROLE", "reader").strip() or "reader",
        help="Role for --share-email recipients.",
    )
    parser.add_argument(
        "--telegram-chat-id",
        default=(
            os.getenv("GOOGLE_SHEET_COPY_NOTIFY_CHAT_ID")
            or os.getenv("ADMIN_CHAT_ID")
            or ""
        ).strip(),
        help="Telegram chat id for the link. Defaults to GOOGLE_SHEET_COPY_NOTIFY_CHAT_ID or ADMIN_CHAT_ID.",
    )
    parser.add_argument(
        "--bot-token",
        default=os.getenv("BOT_TOKEN", "").strip(),
        help="Telegram bot token. Defaults to BOT_TOKEN.",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Deprecated: terminal-only mode is the default.",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Also send the link to Telegram.",
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
        timestamp = datetime.now().strftime("%Y-%m-%d %H-%M")
        copy_name = args.copy_name or f"{source_name} backup {timestamp}"
        folder_id = _extract_drive_folder_id(args.folder_id)
        folder_info = _get_folder_info(drive_service, folder_id)
        if folder_info["id"]:
            folder_kind = "Shared Drive" if folder_info["drive_id"] else "My Drive folder"
            print(
                f"Destination folder: {folder_info['name'] or folder_info['id']} "
                f"({folder_kind}, id={folder_info['id']})"
            )
        copy_id, final_name = _copy_spreadsheet(
            drive_service=drive_service,
            source_file_id=source_id,
            copy_name=copy_name,
            folder_id=folder_id,
        )

        env_emails = _parse_emails(os.getenv("GOOGLE_SHEET_COPY_SHARE_EMAILS"))
        share_emails = [*env_emails, *args.share_email]
        share_anyone_reader = args.share_anyone_reader or not args.private
        if share_anyone_reader:
            _share_anyone_reader(drive_service=drive_service, file_id=copy_id)
        if share_emails:
            _share_with_emails(
                drive_service=drive_service,
                file_id=copy_id,
                emails=share_emails,
                role=args.share_role,
            )

        link = DRIVE_FILE_URL.format(file_id=copy_id)
        message = (
            "Копия Google таблицы создана.\n"
            f"Название: {final_name}\n"
            f"Ссылка: {link}"
        )

        print(message)
        print(f"\nLINK={link}")

        if args.telegram and not args.no_telegram:
            if not args.bot_token:
                raise CopySheetError("Не задан BOT_TOKEN для отправки ссылки в Telegram.")
            if not args.telegram_chat_id:
                raise CopySheetError("Не задан chat_id для отправки ссылки в Telegram.")
            _send_telegram_message(
                bot_token=args.bot_token,
                chat_id=args.telegram_chat_id,
                text=message,
            )
            print(f"Telegram message sent to {args.telegram_chat_id}")

        if not share_anyone_reader and not share_emails:
            print(
                "Warning: копия не была расшарена. "
                "Ссылка может открываться только service account или владельцам папки."
            )

        return 0
    except HttpError as exc:
        if _is_storage_quota_error(exc):
            raise CopySheetError(
                "Google Drive quota exceeded для аккаунта, под которым работает API. "
                "Обычная папка в My Drive не решает это: копия всё равно занимает квоту "
                "service account. Нужна папка именно в Shared Drive с доступной квотой "
                "или OAuth-доступ от обычного Google-пользователя."
            ) from exc
        raise CopySheetError(f"Google API error: {exc}") from exc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CopySheetError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
