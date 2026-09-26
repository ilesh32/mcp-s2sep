"""Bearer-token middleware: rejects unauthenticated requests, lets /healthz through."""
import asyncio

from mcp_server.http_auth import BearerTokenMiddleware

TOKEN = "a-sufficiently-long-token"


async def _inner_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"inner"})


def _call(path="/mcp", method="POST", authorization=None):
    headers = [(b"authorization", authorization.encode())] if authorization else []
    scope = {"type": "http", "path": path, "method": method, "headers": headers}
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(BearerTokenMiddleware(_inner_app, TOKEN)(scope, None, send))
    return sent[0]["status"], sent[1]["body"]


def test_missing_token_rejected():
    assert _call()[0] == 401


def test_wrong_token_rejected():
    assert _call(authorization="Bearer nope")[0] == 401


def test_wrong_scheme_rejected():
    assert _call(authorization=f"Basic {TOKEN}")[0] == 401


def test_valid_token_passes_through():
    assert _call(authorization=f"Bearer {TOKEN}") == (200, b"inner")


def test_healthz_is_open_but_only_for_get():
    assert _call(path="/healthz", method="GET")[0] == 200
    assert _call(path="/healthz", method="POST")[0] == 401
