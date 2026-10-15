"""Small HTTP transport: explicit timeouts and cooperative transient-error retries."""

import errno
import json
import socket
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .retry import pause


class ServiceError(RuntimeError):
    pass


class RejectedSubmission(ServiceError):
    pass


class HTTPClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        headers: dict | None = None,
        get_retries: int = 0,
        retry_delay: float = 1,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = headers or {}
        self.get_retries = get_retries
        self.retry_delay = retry_delay

    def request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        content_type: str | None = None,
        deadline: float | None = None,
    ) -> bytes:
        headers = {**self.headers, "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        attempt = 0
        while True:
            timeout = self.timeout
            request = Request(self.base_url + path, data=data, headers=headers, method=method)
            try:
                with urlopen(request, timeout=timeout) as response:
                    return response.read()
            except HTTPError as error:
                details = error.read(2000).decode("utf-8", errors="replace")
                if method == "POST" and path == "/prompt" and error.code == 429:
                    attempt += 1
                    try:
                        pause(attempt, "ComfyUI rate limit", error)
                    except BaseException as interrupted:
                        interrupted.submission_not_sent = True
                        raise
                    continue
                if (
                    method == "POST"
                    and path == "/prompt"
                    and error.code < 500
                    and error.code != 408
                ):
                    raise RejectedSubmission(
                        f"POST /prompt rejected: HTTP {error.code}: {details}"
                    ) from error
                retryable = error.code in (408, 429, 500, 502, 503, 504)
                reason = f"HTTP {error.code}: {details}"
            except (URLError, OSError, HTTPException) as error:
                reason_error = getattr(error, "reason", error)
                if path == "/prompt" and (
                    isinstance(reason_error, (ConnectionRefusedError, socket.gaierror))
                    or getattr(reason_error, "errno", None)
                    in {errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN}
                ):
                    attempt += 1
                    try:
                        pause(attempt, "Connecting to ComfyUI", error)
                    except BaseException as interrupted:
                        interrupted.submission_not_sent = True
                        raise
                    continue
                retryable = True
                reason = str(error)
            if method != "GET" and path not in ("/chat/completions", "/upload/image"):
                raise ServiceError(
                    f"{method} {path} failed: {reason}. Request was not retried; "
                    "the server may already have accepted it. Inspect its queue/history."
                )
            if not retryable:
                raise ServiceError(f"{method} {path} failed: {reason}")
            attempt += 1
            pause(attempt, f"{method} {path}", reason)
        raise AssertionError("unreachable")

    def json(
        self, method: str, path: str, payload: dict | None = None, deadline: float | None = None
    ) -> dict:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        raw = self.request(
            method, path, body, "application/json" if body is not None else None, deadline
        )
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise ServiceError(f"{method} {path} returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ServiceError(f"{method} {path} must return a JSON object")
        return value
