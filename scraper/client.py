import json
import logging
import re
import threading
import typing
from datetime import date as date_type
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

WWW_BASE_URL = "https://www.easistent.com"
MOJ_BASE_URL = "https://moj.easistent.com"
MOJ_SESSION_COOKIE = "ses"
LOGOUT_URL = f"{MOJ_BASE_URL}/logout"
TIMETABLE_ACTION_NAME = "getWeekTimetable"
TIMETABLE_ROUTER_STATE_TREE = (
    "%5B%22%22%2C%7B%22children%22%3A%5B%22(root)%22%2C%7B%22children%22%3A%5B%22"
    "(routes)%22%2C%7B%22children%22%3A%5B%22timetable%22%2C%7B%22children%22%3A%5B%22"
    "__PAGE__%22%2C%7B%7D%2Cnull%2Cnull%2C0%5D%7D%2Cnull%2Cnull%2C4%5D%7D%2Cnull%2Cnull%2C8%5D%7D%2Cnull%2Cnull%2C8%5D%7D%2Cnull%2Cnull%2C28%5D"
)

logger = logging.getLogger(__name__)

SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']")
SERVER_ACTION_RE = re.compile(
    r"createServerReference\)?\(\s*[\"'](?P<id>[0-9a-f]{32,64})[\"'][^)]*?[\"']"
    + re.escape(TIMETABLE_ACTION_NAME)
    + r"[\"']\s*\)"
)

_timetable_action_id: str | None = None
_action_id_lock = threading.Lock()


def reset_timetable_action_id() -> None:
    """Forget the cached server action ID (mainly useful in tests)."""
    global _timetable_action_id
    with _action_id_lock:
        _timetable_action_id = None


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

    def _get_timetable_action_id(
        self, timetable_date: str, *, refresh: bool = False
    ) -> str:
        global _timetable_action_id
        with _action_id_lock:
            if refresh or _timetable_action_id is None:
                _timetable_action_id = self._discover_timetable_action_id(
                    timetable_date
                )
            return _timetable_action_id

    def _discover_timetable_action_id(self, timetable_date: str) -> str:
        page_url = f"{MOJ_BASE_URL}/timetable?date={timetable_date}"
        page = self.client.get(page_url)
        if self._is_logged_out(page):
            self._clear_session()
            self.login()
            page = self.client.get(page_url)
        page.raise_for_status()

        match = SERVER_ACTION_RE.search(page.text)
        if match:
            logger.info("Discovered timetable server action %s", match["id"])
            return match["id"]

        for source in SCRIPT_SRC_RE.findall(page.text):
            script_url = urljoin(str(page.url), source)
            if not script_url.startswith(MOJ_BASE_URL):
                continue
            chunk = self.client.get(script_url)
            chunk.raise_for_status()
            match = SERVER_ACTION_RE.search(chunk.text)
            if match:
                logger.info("Discovered timetable server action %s", match["id"])
                return match["id"]

        raise RuntimeError(
            f"could not find {TIMETABLE_ACTION_NAME} server action on timetable page"
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
        self, timetable_date: str, week_start_date: str, action_id: str
    ) -> httpx.Response:
        return self.client.post(
            f"{MOJ_BASE_URL}/timetable",
            params={"date": timetable_date},
            content=json.dumps([week_start_date]),
            headers={
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
                "next-action": action_id,
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

        action_id = self._get_timetable_action_id(timetable_date)
        response = self._request_timetable(
            timetable_date, week_start_date, action_id
        )
        if self._is_logged_out(response):
            self._clear_session()
            self.login()
            action_id = self._get_timetable_action_id(timetable_date)
            response = self._request_timetable(
                timetable_date, week_start_date, action_id
            )

        if response.status_code == 409:
            logger.info("Timetable server action was rejected; rediscovering it")
            action_id = self._get_timetable_action_id(timetable_date, refresh=True)
            response = self._request_timetable(
                timetable_date, week_start_date, action_id
            )

        response.raise_for_status()
        return self._parse_timetable_response(response)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> typing.Self:
        return self

    def __exit__(self, *args) -> None:
        self.close()
