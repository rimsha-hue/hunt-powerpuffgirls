"""ASGI middleware: request body size limits.

Starlette accepts bodies of any size by default, so one oversized request could exhaust memory.
Multipart uploads get the upload limit; everything else gets the much smaller JSON limit. Both the
declared Content-Length and the bytes actually streamed are checked (a client can lie about one).
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MULTIPART_OVERHEAD = 64 * 1024  # boundaries and part headers around the file itself


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, json_limit_bytes: int, upload_limit_bytes: int) -> None:
        self.app = app
        self.json_limit = json_limit_bytes
        self.upload_limit = upload_limit_bytes + MULTIPART_OVERHEAD

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_type = headers.get(b"content-type", b"").decode("latin-1").lower()
        limit = (
            self.upload_limit if content_type.startswith("multipart/form-data") else self.json_limit
        )
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await self._reject(send, limit)
            return

        received = 0
        response_started = False
        rejected = False

        async def limited_receive() -> Message:
            # Over the limit, answer 413 here and tell the app the client went away. Frameworks
            # turn errors raised while reading a body into their own responses, so the decision
            # must not depend on an exception travelling back through the app.
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    rejected = True
                    if not response_started:
                        await self._reject(send, limit)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if rejected:
                return  # the 413 has been sent; drop whatever the app answers
            response_started = response_started or message["type"] == "http.response.start"
            await send(message)

        await self.app(scope, limited_receive, guarded_send)

    @staticmethod
    async def _reject(send: Send, limit: int) -> None:
        body = json.dumps(
            {"detail": f"The request is too large (limit {limit // 1024} KB)."}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
