"""Static bearer-token auth for the streamable-http transport.

execute_teardown deletes AWS resources, so a network-reachable server must not
be open. Every request needs `Authorization: Bearer <MCP_AUTH_TOKEN>`; only
GET /healthz (liveness probe, reveals nothing) is exempt. Pure ASGI, so it
doesn't buffer the streamed MCP responses.
"""
import hmac
import json

HEALTH_PATH = "/healthz"


class BearerTokenMiddleware:
    def __init__(self, app, token: str):
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope["path"] == HEALTH_PATH and scope["method"] == "GET"):
            await self.app(scope, receive, send)
            return

        header = dict(scope["headers"]).get(b"authorization", b"")
        scheme, _, presented = header.partition(b" ")
        if scheme.lower() == b"bearer" and hmac.compare_digest(presented, self._token):
            await self.app(scope, receive, send)
            return

        body = json.dumps({"error": "unauthorized"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b'Bearer realm="mcp"'),
            ],
        })
        await send({"type": "http.response.body", "body": body})
