import json
import typing
from datetime import date as date_type
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

WWW_BASE_URL = "https://www.easistent.com"
MOJ_BASE_URL = "https://moj.easistent.com"
MOJ_SESSION_COOKIE = "ses"
LOGOUT_URL = f"{MOJ_BASE_URL}/logout"
TIMETABLE_NEXT_ACTION = "4033775cfb606ce7190b24caf2e77ca89213132feb"
TIMETABLE_ROUTER_STATE_TREE = (
    "%5B%22%22%2C%7B%22children%22%3A%5B%22(root)%22%2C%7B%22children%22%3A%5B%22"
    "(routes)%22%2C%7B%22children%22%3A%5B%22timetable%22%2C%7B%22children%22%3A%5B%22"
    "__PAGE__%22%2C%7B%7D%2Cnull%2Cnull%2C0%5D%7D%2Cnull%2Cnull%2C4%5D%7D%2Cnull%2Cnull%2C8%5D%7D%2Cnull%2Cnull%2C8%5D%7D%2Cnull%2Cnull%2C28%5D"
)


class EasistentClient:
    def __init__(
        self,
        username: str,
        password: str,
        timeout: float = 20.0,
        session_file: str | Path = ".easistent_session",
    ):
        self.username = username
        self.password = password
        self.session_file = Path(session_file)
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        self._load_session()

    def login(self) -> typing.Self:
        self._authenticate_with_credentials()
        one_time_login_url = self._get_one_time_login_url()
        self._exchange_one_time_login_for_session(one_time_login_url)
        self._save_session()
        return self

    def _authenticate_with_credentials(self) -> None:
        login_form = {
            "uporabnik": (None, self.username),
            "geslo": (None, self.password),
            "pin": (None, ""),
            "koda": (None, ""),
            "force_sms": (None, "0"),
        }
        response = self.client.post(f"{WWW_BASE_URL}/p/ajax_prijava", files=login_form)
        response.raise_for_status()

        login_result = response.json()
        login_failed = login_result.get("status") != "ok"
        captcha_required = login_result.get("data", {}).get("require_captcha")
        if login_failed or captcha_required:
            raise RuntimeError(f"login failed: {login_result}")

    def _get_one_time_login_url(self) -> str:
        webapp_response = self.client.get(
            f"{WWW_BASE_URL}/webapp", follow_redirects=False
        )
        if not webapp_response.is_redirect:
            raise RuntimeError(
                f"expected redirect from /webapp, got {webapp_response.status_code}"
            )

        redirect_location = webapp_response.headers.get("Location")
        if not redirect_location:
            raise RuntimeError("webapp redirect missing Location header")

        token_values = parse_qs(urlparse(redirect_location).query).get("token")
        if not token_values or not token_values[0]:
            raise RuntimeError(
                f"one_time_login redirect missing token: {redirect_location}"
            )

        return redirect_location

    def _exchange_one_time_login_for_session(self, one_time_login_url: str) -> None:
        response = self.client.get(one_time_login_url)
        response.raise_for_status()

        session_cookie = self.client.cookies.get(MOJ_SESSION_COOKIE)
        if not session_cookie:
            raise RuntimeError(
                "missing moj.easistent.com session cookie after one_time_login"
            )

    def _load_session(self) -> None:
        if not self.session_file.exists():
            return

        cookies = json.loads(self.session_file.read_text())
        for cookie in cookies:
            domain = cookie.get("domain") or ""
            self.client.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=domain.lstrip(".") or "",
                path=cookie.get("path", "/"),
            )

    def _save_session(self) -> None:
        cookies = [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
            }
            for cookie in self.client.cookies.jar
        ]
        self.session_file.write_text(json.dumps(cookies))

    def _clear_session(self) -> None:
        self.client.cookies.clear()
        if self.session_file.exists():
            self.session_file.unlink()

    def has_session(self) -> bool:
        return self.client.cookies.get(MOJ_SESSION_COOKIE) is not None

    def _is_logged_out(self, response: httpx.Response) -> bool:
        if str(response.url).startswith(LOGOUT_URL):
            return True
        return any(
            str(redirect.url).startswith(LOGOUT_URL) for redirect in response.history
        )

    def _parse_timetable_response(self, response: httpx.Response):
        content_type = response.headers.get("content-type", "")
        if "text/x-component" in content_type:
            for line in response.text.splitlines():
                if ":" not in line:
                    continue
                _, payload = line.split(":", 1)
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(data, dict)
                    and data.get("ok") is True
                    and "value" in data
                ):
                    return data["value"]
            raise RuntimeError(
                "timetable x-component response missing ok/value payload"
            )
        return response.json()

    def _request_timetable(
        self, timetable_date: str, week_start_date: str
    ) -> httpx.Response:
        return self.client.post(
            f"{MOJ_BASE_URL}/timetable",
            params={"date": timetable_date},
            content=json.dumps([week_start_date]),
            headers={
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
                "next-action": TIMETABLE_NEXT_ACTION,
                "next-router-state-tree": TIMETABLE_ROUTER_STATE_TREE,
                "Referer": f"{MOJ_BASE_URL}/timetable?date={timetable_date}",
                "Origin": MOJ_BASE_URL,
            },
        )

    def timetable(
        self, timetable_date: str | None = None, week_start_date: str | None = None
    ):
        timetable_date = timetable_date or date_type.today().isoformat()
        week_start_date = week_start_date or timetable_date

        if not self.has_session():
            self.login()

        response = self._request_timetable(timetable_date, week_start_date)
        if self._is_logged_out(response):
            self._clear_session()
            self.login()
            response = self._request_timetable(timetable_date, week_start_date)

        response.raise_for_status()
        return self._parse_timetable_response(response)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> typing.Self:
        return self

    def __exit__(self, *args) -> None:
        self.close()
