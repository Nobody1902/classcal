import json

import httpx
import pytest

import scraper.client as client_module
from scraper.client import EasistentClient

OLD_ACTION_ID = "a" * 42
NEW_ACTION_ID = "b" * 42

PAGE = '<html><script src="/_next/static/chunks/timetable.js"></script></html>'


@pytest.fixture(autouse=True)
def fresh_action_id():
    client_module.reset_timetable_action_id()
    yield
    client_module.reset_timetable_action_id()


def make_client(tmp_path, handler):
    session_file = tmp_path / "session.json"
    session_file.write_text(
        json.dumps(
            [
                {
                    "name": "ses",
                    "value": "token",
                    "domain": "moj.easistent.com",
                    "path": "/",
                }
            ]
        )
    )
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    client = EasistentClient(
        "user", "password", session_file=session_file, client=http_client
    )
    return client


def chunk(action_id):
    return (
        'let J=(0,G.createServerReference)("%s",G.callServer,void 0,'
        'G.findSourceMapURL,"getWeekTimetable");' % action_id
    )


def component_response():
    return httpx.Response(
        200,
        headers={"content-type": "text/x-component"},
        text='0:{"a":"$@1"}\n1:{"ok":true,"value":{"schedule":[],"events":[]}}\n',
    )


def test_discovers_action_and_refreshes_it_after_409(tmp_path):
    action_ids = iter([OLD_ACTION_ID, NEW_ACTION_ID])
    used_action_ids = []

    def handler(request):
        url = str(request.url)
        if (
            url.startswith("https://moj.easistent.com/timetable?")
            and request.method == "GET"
        ):
            return httpx.Response(200, text=PAGE)
        if url.endswith("/_next/static/chunks/timetable.js"):
            return httpx.Response(200, text=chunk(next(action_ids)))
        assert url.startswith("https://moj.easistent.com/timetable?")
        used_action_ids.append(request.headers["next-action"])
        if len(used_action_ids) == 1:
            return httpx.Response(409, text="Server Action unavailable.")
        return component_response()

    client = make_client(tmp_path, handler)
    try:
        data = client.timetable("2026-11-23", "2026-11-23")
    finally:
        client.close()

    assert data == {"schedule": [], "events": []}
    assert used_action_ids == [OLD_ACTION_ID, NEW_ACTION_ID]


def test_action_id_is_reused_across_clients(tmp_path):
    page_requests = []
    used_action_ids = []

    def handler(request):
        url = str(request.url)
        if (
            url.startswith("https://moj.easistent.com/timetable?")
            and request.method == "GET"
        ):
            page_requests.append(url)
            return httpx.Response(200, text=PAGE)
        if url.endswith("/_next/static/chunks/timetable.js"):
            return httpx.Response(200, text=chunk(NEW_ACTION_ID))
        used_action_ids.append(request.headers["next-action"])
        return component_response()

    first = make_client(tmp_path, handler)
    try:
        assert first.timetable("2026-11-23") == {"schedule": [], "events": []}
    finally:
        first.close()

    second = make_client(tmp_path, handler)
    try:
        assert second.timetable("2026-11-30") == {"schedule": [], "events": []}
    finally:
        second.close()

    assert len(page_requests) == 1
    assert used_action_ids == [NEW_ACTION_ID, NEW_ACTION_ID]


def test_missing_action_id_raises_without_posting(tmp_path):
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request)
        return httpx.Response(200, text="<html></html>")

    client = make_client(tmp_path, handler)
    try:
        with pytest.raises(RuntimeError, match="getWeekTimetable"):
            client.timetable("2026-11-23")
    finally:
        client.close()

    assert posts == []
