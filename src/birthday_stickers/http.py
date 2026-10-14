"""Small HTTP transport: bounded reads, explicit timeouts, GET-only retries."""

import json
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ServiceError(RuntimeError):
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
        attempts = 1 + (self.get_retries if method == "GET" else 0)
        for attempt in range(attempts):
            timeout = self.timeout
            if deadline is not None:
                timeout = min(timeout, deadline - time.monotonic())
                if timeout <= 0:
                    raise TimeoutError("Request deadline elapsed")
            request = Request(self.base_url + path, data=data, headers=headers, method=method)
            try:
                with urlopen(request, timeout=timeout) as response:
                    return response.read()
            except HTTPError as error:
                details = error.read(2000).decode("utf-8", errors="replace")
                retryable = error.code in (408, 429, 500, 502, 503, 504)
                reason = f"HTTP {error.code}: {details}"
            except (URLError, OSError, HTTPException) as error:
                retryable = True
                reason = str(error)
            if method != "GET":
                raise ServiceError(
                    f"{method} {path} failed: {reason}. Request was not retried; "
                    "the server may already have accepted it. Inspect its queue/history."
                )
            if not retryable or attempt == attempts - 1:
                raise ServiceError(f"GET {path} failed: {reason}")
            delay = self.retry_delay * 2**attempt
            if deadline is not None:
                delay = min(delay, max(0, deadline - time.monotonic()))
            time.sleep(delay)
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
