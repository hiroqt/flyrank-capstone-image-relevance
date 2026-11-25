"""Reject oversized request bodies with a 413 before they reach a handler."""
import json

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import error_body


class PayloadTooLarge(HTTPException):
    """A FastAPI HTTPException, so FastAPI's body parsing re-raises it instead of turning it into a 400."""

    def __init__(self, max_bytes: int):
        super().__init__(status_code=413, detail=f"Request body exceeds {max_bytes} bytes")


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        # Chunked / undeclared bodies: count bytes as they stream in.
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise PayloadTooLarge(self.max_bytes)
            return message

        try:
            await self.app(scope, limited_receive, send)
        except PayloadTooLarge:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps(error_body("payload_too_large", f"Request body exceeds {self.max_bytes} bytes")).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
