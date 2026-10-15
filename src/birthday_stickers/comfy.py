"""ComfyUI upload, preflight, submission, history polling, and image retrieval."""

import json
import logging
import mimetypes
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlencode

from .config import ComfyConfig, auth_headers
from .http import HTTPClient, RejectedSubmission, ServiceError
from .retry import pause, read_bytes, wait
from .workflows import Graph

log = logging.getLogger(__name__)


class ComfyClient:
    def __init__(self, config: ComfyConfig, worker="images"):
        self.worker = worker
        self.config = config
        self.client_id = str(uuid.uuid4())
        self.http = HTTPClient(
            config.base_url,
            config.request_timeout,
            auth_headers(config.api_key_env),
            config.get_retries,
            config.retry_delay,
        )

    def preflight(self, *graphs: Graph) -> None:
        info = self.http.json("GET", "/object_info")
        missing = sorted(
            {
                node["class_type"]
                for graph in graphs
                for node in graph.values()
                if node["class_type"] not in info
            }
        )
        if missing:
            raise ServiceError("ComfyUI is missing required node classes: " + ", ".join(missing))
        errors = []
        for graph in graphs:
            for node_id, node in graph.items():
                schema = info[node["class_type"]].get("input", {})
                inputs = {**schema.get("required", {}), **schema.get("optional", {})}
                for name, value in node["inputs"].items():
                    descriptor = inputs.get(name)
                    if (
                        name.rstrip("0123456789").endswith("_name")
                        and isinstance(value, str)
                        and isinstance(descriptor, list)
                        and descriptor
                        and isinstance(descriptor[0], list)
                        and value not in descriptor[0]
                    ):
                        errors.append(f"{node_id}.{name}: model/option {value!r} is unavailable")
        if errors:
            raise ServiceError("ComfyUI preflight failed:\n" + "\n".join(errors))

    def upload(self, path: Path, role: str) -> str:
        boundary = "birthday-" + uuid.uuid4().hex
        filename = f"{uuid.uuid4().hex}-{role}{path.suffix.lower()}"
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts = []
        for name, value in (
            ("type", "input"),
            ("overwrite", "false"),
            ("subfolder", "birthday-stickers"),
        ):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                f"\r\n\r\n{value}\r\n".encode()
            )
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'
            ).encode()
        )
        parts.extend((read_bytes(path), f"\r\n--{boundary}--\r\n".encode()))
        raw = self.http.request(
            "POST", "/upload/image", b"".join(parts), f"multipart/form-data; boundary={boundary}"
        )
        try:
            response = json.loads(raw)
            name = response["name"]
            subfolder = response.get("subfolder", "")
            if not isinstance(name, str) or not name or not isinstance(subfolder, str):
                raise ValueError("missing image name")
            if response.get("type", "input") != "input":
                raise ValueError("unexpected image storage type")
        except (ValueError, KeyError, TypeError) as error:
            raise ServiceError("ComfyUI upload returned an invalid image reference") from error
        return f"{subfolder}/{name}" if subfolder else name

    def recover_submission(self, request_id):
        attempt = 0
        while True:
            queue = self.http.json("GET", "/queue")
            for entry in queue.get("queue_running", []) + queue.get("queue_pending", []):
                if len(entry) > 3 and entry[3].get("birthday_request_id") == request_id:
                    return entry[1]
            history = self.http.json("GET", "/history")
            for prompt_id, entry in history.items():
                prompt = entry.get("prompt", [])
                if len(prompt) > 3 and prompt[3].get("birthday_request_id") == request_id:
                    return prompt_id
            attempt += 1
            pause(attempt, "Recovering submitted ComfyUI job", request_id)

    def submit(self, graph: Graph, request_id=None, *, recover=False) -> str:
        request_id = request_id or uuid.uuid4().hex
        if recover:
            return self.recover_submission(request_id)
        try:
            response = self.http.json(
                "POST",
                "/prompt",
                {
                    "prompt": graph,
                    "client_id": self.client_id,
                    "extra_data": {"birthday_request_id": request_id},
                },
            )
        except RejectedSubmission:
            raise
        except ServiceError:
            # A dropped or malformed response cannot prove the job was rejected.
            return self.recover_submission(request_id)
        if response.get("node_errors"):
            raise RejectedSubmission(f"ComfyUI rejected workflow nodes: {response['node_errors']}")
        prompt_id = response.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            return self.recover_submission(request_id)
        log.info("ComfyUI queued %s", prompt_id, extra={"worker": self.worker})
        return prompt_id

    def wait_terminal(self, prompt_id: str, checkpoint: Callable[[], None] = lambda: None) -> None:
        """Wait until previously submitted work stops, even if it failed or was interrupted."""
        while True:
            checkpoint()
            history = self.http.json("GET", "/history/" + quote(prompt_id, safe=""))
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                failed = status.get("status_str") == "error" or any(
                    kind in ("execution_error", "execution_interrupted")
                    for kind, _ in status.get("messages", [])
                )
                if status.get("completed") or failed:
                    log.info(
                        "Previous ComfyUI job %s is terminal%s",
                        prompt_id,
                        " (failed/interrupted)" if failed else "",
                        extra={"worker": self.worker},
                    )
                    return
            wait(self.config.poll_interval, checkpoint)

    def wait_image(
        self, prompt_id: str, output_id: str, checkpoint: Callable[[], None] = lambda: None
    ) -> bytes:
        next_notice = time.monotonic() + 30
        while True:
            checkpoint()
            history = self.http.json("GET", "/history/" + quote(prompt_id, safe=""))
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                failures = [
                    data
                    for kind, data in status.get("messages", [])
                    if kind in ("execution_error", "execution_interrupted")
                ]
                if status.get("status_str") == "error" or failures:
                    raise ServiceError(f"ComfyUI job {prompt_id} failed: {failures or status}")
                if status.get("completed"):
                    images = entry.get("outputs", {}).get(str(output_id), {}).get("images", [])
                    if len(images) != 1:
                        raise ServiceError(
                            f"ComfyUI job {prompt_id}: expected one image at output node "
                            f"{output_id}, got {len(images)}"
                        )
                    item = images[0]
                    if not isinstance(item.get("filename"), str) or not item["filename"]:
                        raise ServiceError(f"ComfyUI job {prompt_id}: missing output filename")
                    query = urlencode(
                        {
                            "filename": item["filename"],
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                        }
                    )
                    return self.http.request("GET", "/view?" + query)
            if time.monotonic() >= next_notice:
                log.info(
                    "Waiting for ComfyUI job %s (queued or executing)",
                    prompt_id,
                    extra={"worker": self.worker},
                )
                next_notice = time.monotonic() + 30
            wait(self.config.poll_interval, checkpoint)
