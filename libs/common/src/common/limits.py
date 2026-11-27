"""Pure-ASGI request-body limit shared by every service (413 for oversized bodies).

Counts the bytes actually streamed, so a missing, lying or non-numeric ``Content-Length`` and
chunked uploads are all bounded.
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


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
