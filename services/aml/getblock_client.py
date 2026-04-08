from __future__ import annotations

import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from services.aml.getblock_parser import (
    extract_amlcheckup,
    extract_amlcheckup_from_redirect_header,
    extract_csrf_from_html,
    find_hidden_csrf_field,
)


class GetBlockAMLClient:
    BASE = "https://getblock.net"

    def __init__(self, *, identity: str, password: str, lang: str = "en"):
        self.identity = identity
        self.password = password
        self.lang = lang
        self.session = requests.Session()
        self.session.headers.update({
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
            "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        })

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.BASE}{path}"

    def _get(self, path: str, **kwargs) -> requests.Response:
        resp = self.session.get(self._url(path), timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def _post(self, path: str, **kwargs) -> requests.Response:
        return self.session.post(self._url(path), timeout=30, **kwargs)

    def _csrf_cookie_value(self) -> str | None:
        for name, value in self.session.cookies.items():
            if name == "_csrf":
                return urllib.parse.unquote(value)
        return None

    def _extract_csrf_from_cookie(self) -> str | None:
        cookie_val = self._csrf_cookie_value()
        if not cookie_val:
            return None
        m = re.search(r's:\d+:"([^"]+)"', cookie_val)
        return m.group(1) if m else None

    def _best_csrf_token(self, html_text: str | None = None) -> str | None:
        if html_text:
            token = extract_csrf_from_html(html_text)
            if token:
                return token
        return self._extract_csrf_from_cookie() or self._csrf_cookie_value()

    def _get_with_retry_500(
        self,
        path: str,
        *,
        max_attempts: int = 10,
        delay_seconds: float = 3.0,
        **kwargs,
    ) -> requests.Response:
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                return self._get(path, **kwargs)
            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status == 500 and attempt < max_attempts:
                    time.sleep(delay_seconds)
                    continue
                raise
            except Exception as exc:
                last_error = exc
                if attempt < max_attempts:
                    time.sleep(delay_seconds)
                    continue
                raise

        raise RuntimeError(f"Не удалось получить {path}: {last_error}")

    def login(self) -> None:
        login_url = f"/{self.lang}/user/sign-in/login"

        page = self._get(login_url)
        hidden_csrf = find_hidden_csrf_field(page.text)
        header_csrf = self._best_csrf_token(page.text)

        if not hidden_csrf:
            raise RuntimeError("Не удалось найти hidden _csrf на странице логина")
        if not header_csrf:
            raise RuntimeError("Не удалось найти x-csrf-token для логина")

        payload = {
            "_csrf": hidden_csrf,
            "LoginForm[identity]": self.identity,
            "LoginForm[password]": self.password,
            "LoginForm[rememberMe]": "1",
        }

        headers = {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": self.BASE,
            "referer": self._url(login_url),
            "x-requested-with": "XMLHttpRequest",
            "x-csrf-token": header_csrf,
        }

        resp = self._post(login_url, data=payload, headers=headers, allow_redirects=True)
        if resp.status_code >= 400:
            raise RuntimeError(f"Ошибка логина: HTTP {resp.status_code}")

        check = self._get(f"/{self.lang}/riskscore", allow_redirects=True)
        if "/user/sign-in/login" in check.url.lower():
            raise RuntimeError("Логин не удался")

    def get_riskscore_page(self) -> requests.Response:
        resp = self._get(f"/{self.lang}/riskscore", allow_redirects=True)
        if "/user/sign-in/login" in resp.url.lower():
            raise RuntimeError("Сессия не авторизована: riskscore редиректит на логин")
        return resp

    def create_check(
        self,
        *,
        wallet: str,
        currency_code: str,
        token_id: str,
        user_id: str,
        aml_provider: str,
        direction: str,
        source: str,
        type_: str,
    ) -> dict[str, Any]:
        page = self.get_riskscore_page()
        ajax_csrf = self._best_csrf_token(page.text)
        hidden_csrf = find_hidden_csrf_field(page.text) or ""

        if not ajax_csrf:
            raise RuntimeError("Не найден x-csrf-token для order-запроса")
        if not hidden_csrf:
            raise RuntimeError("Не найден hidden _csrf для order-запроса")
        if not user_id:
            raise RuntimeError("Не задан GETBLOCK_USER_ID")

        data = {
            "_csrf": hidden_csrf,
            "CheckingForm[checking_hash]": wallet,
            "CheckingForm[token_id]": token_id,
            "CheckingForm[type]": type_,
            "CheckingForm[user_id]": user_id,
            "CheckingForm[currency_code]": currency_code,
            "CheckingForm[direction]": direction,
            "CheckingForm[source]": source,
            "CheckingForm[checking_address]": wallet,
            "CheckingForm[checking_tx_id]": "",
            "CheckingForm[aml_provider]": aml_provider,
        }

        headers = {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": self.BASE,
            "referer": self._url(f"/{self.lang}/riskscore"),
            "x-requested-with": "XMLHttpRequest",
            "x-csrf-token": ajax_csrf,
        }

        resp = self._post(
            f"/{self.lang}/explorer/order",
            data=data,
            headers=headers,
            allow_redirects=False,
        )

        result: dict[str, Any] = {
            "status_code": resp.status_code,
            "url": resp.url,
            "headers": dict(resp.headers),
            "raw_text": resp.text[:3000],
        }

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                result["json"] = resp.json()
            except ValueError:
                pass

        amlcheckup = extract_amlcheckup_from_redirect_header(result["headers"])
        if amlcheckup:
            result["amlcheckup"] = amlcheckup

        return result

    def refresh_ajax_csrf(self) -> str:
        resp = self.get_riskscore_page()
        token = self._best_csrf_token(resp.text)
        if not token:
            raise RuntimeError("Не удалось получить CSRF token для AJAX")
        return token

    def get_address_page(self, *, wallet: str, amlcheckup: str, currency_code: str) -> dict[str, Any]:
        ajax_csrf = self.refresh_ajax_csrf()

        data = {
            "method": "getAddressPage",
            "hash": wallet,
            "code": currency_code,
            "page": "",
            "urlParams[amlcheckup]": amlcheckup,
        }

        headers = {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": self.BASE,
            "referer": self._url(f"/{self.lang}/{currency_code}/address/{wallet}?amlcheckup={amlcheckup}"),
            "x-requested-with": "XMLHttpRequest",
            "x-csrf-token": ajax_csrf,
        }

        resp = self._post(f"/{self.lang}/site/ajax", data=data, headers=headers, allow_redirects=True)

        result: dict[str, Any] = {
            "status_code": resp.status_code,
            "url": resp.url,
            "headers": dict(resp.headers),
            "raw_text": resp.text[:5000],
        }

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                result["json"] = resp.json()
            except ValueError:
                pass

        return result

    def get_report_preview_html(
        self,
        *,
        amlcheckup: str,
        max_attempts: int = 10,
        delay_seconds: float = 3.0,
    ) -> str:
        resp = self._get_with_retry_500(
            f"/{self.lang}/report-preview/{amlcheckup}",
            max_attempts=max_attempts,
            delay_seconds=delay_seconds,
            allow_redirects=True,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "referer": self._url(f"/{self.lang}/riskscore"),
                "upgrade-insecure-requests": "1",
            },
        )
        return resp.text

    def download_report(
        self,
        *,
        amlcheckup: str,
        output_path: str,
        max_attempts: int = 10,
        delay_seconds: float = 3.0,
    ) -> str:
        preview_url = f"/{self.lang}/report-preview/{amlcheckup}"
        download_url = f"/{self.lang}/report-download/{amlcheckup}"

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                self._get_with_retry_500(
                    preview_url,
                    max_attempts=max_attempts,
                    delay_seconds=delay_seconds,
                    allow_redirects=True,
                    headers={
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "referer": self._url(f"/{self.lang}/riskscore"),
                        "upgrade-insecure-requests": "1",
                    },
                )

                resp = self._get(
                    download_url,
                    allow_redirects=True,
                    stream=True,
                    headers={
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "referer": self._url(preview_url),
                        "upgrade-insecure-requests": "1",
                    },
                )

                with open(output_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)

                return output_path

            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status == 500 and attempt < max_attempts:
                    time.sleep(delay_seconds)
                    continue
                raise
            except Exception as exc:
                last_error = exc
                if attempt < max_attempts:
                    time.sleep(delay_seconds)
                    continue
                raise RuntimeError(f"Не удалось скачать отчет: {exc}") from exc

        raise RuntimeError(f"Не удалось скачать отчет после {max_attempts} попыток: {last_error}")