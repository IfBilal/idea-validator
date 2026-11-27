"""Pure-ASGI middleware: security headers on every response and a hard request-body limit."""

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS = {
    b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"no-referrer",
    b"cache-control": b"no-store",
    b"x-frame-options": b"DENY",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = list(message.get("headers", []))
                present = {name.lower() for name, _ in existing}
                existing += [(n, v) for n, v in SECURITY_HEADERS.items() if n not in present]
                message["headers"] = existing
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _too_large(limit: int) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps(
        {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": f"request body is larger than {limit} bytes",
                "details": {},
                "request_id": None,
            }
        }
    ).encode()
    return body, [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]


class BodyLimitMiddleware:
    """Reject bodies over ``max_bytes`` with 413, whether or not ``Content-Length`` is honest."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return
        messages: list[Message] = []
        total = 0
        more = True
        while more:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                await self._reject(send)
                return
            more = bool(message.get("more_body", False))
        replay = iter(messages)

        async def replay_receive() -> Message:
            try:
                return next(replay)
            except StopIteration:
                return await receive()

        await self.app(scope, replay_receive, send)

    async def _reject(self, send: Send) -> None:
        body, headers = _too_large(self.max_bytes)
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def middleware_stack(max_bytes: int) -> list[tuple[Any, dict[str, Any]]]:
    return [(BodyLimitMiddleware, {"max_bytes": max_bytes}), (SecurityHeadersMiddleware, {})]
