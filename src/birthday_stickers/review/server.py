"""Small local HTTP review server. Name links identify reviewers without passwords."""

import argparse
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .store import ReviewStore

STATIC = Path(__file__).parent / "static"


def make_server(gallery, host="0.0.0.0", port=8080):
    store = ReviewStore(gallery)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, value, status=200):
            self.send_bytes(json.dumps(value).encode(), "application/json", status)

        def send_bytes(self, data, kind, status=200, cache="no-store"):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self'; style-src 'self'; "
                "script-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def route(self):
            url = urlsplit(self.path)
            return unquote(url.path), parse_qs(url.query)

        def do_GET(self):
            try:
                path, query = self.route()
                pipeline = query.get("pipeline", ["image_generation"])[0]
                if path.startswith("/api/user/"):
                    self.reply(
                        store.collection(
                            path.removeprefix("/api/user/"),
                            pipeline,
                            query.get("category", ["pending"])[0],
                        )
                    )
                elif path == "/api/sessions":
                    self.reply(store.sessions(pipeline))
                elif path.startswith("/images/"):
                    match = re.fullmatch(
                        r"/images/(image_generation|outpainting)/([a-f0-9]{64}\.(jpg|png|webp|bmp|tiff))",
                        path,
                    )
                    if not match:
                        self.reply({"error": "Image not found"}, 404)
                        return
                    source = store.root / match[1] / match[2]
                    if not source.is_file() or source.is_symlink():
                        self.reply({"error": "Image not found"}, 404)
                        return
                    self.send_bytes(
                        source.read_bytes(),
                        mimetypes.guess_type(source.name)[0] or "image/jpeg",
                        cache="private, max-age=31536000, immutable",
                    )
                elif path in ("/view.png", "/pointing-hand.png"):
                    self.send_bytes((STATIC / path[1:]).read_bytes(), "image/png")
                elif path in ("/app.js", "/zoom.js", "/style.css"):
                    source = STATIC / path[1:]
                    self.send_bytes(
                        source.read_bytes(),
                        "text/javascript" if path.endswith(".js") else "text/css",
                    )
                elif path in ("/", "/sessions") or path.startswith("/user/"):
                    self.send_bytes(
                        (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8"
                    )
                else:
                    self.reply({"error": "Not found"}, 404)
            except (ValueError, OSError) as error:
                self.reply({"error": str(error)}, 400)

        def do_POST(self):
            try:
                path, query = self.route()
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                    self.reply({"error": "Cross-origin requests are not allowed"}, 403)
                    return
                if (
                    not path.startswith("/api/user/")
                    or self.headers.get("Content-Type") != "application/json"
                ):
                    self.reply({"error": "Expected a JSON review request"}, 400)
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object")
                store.decide(
                    path.removeprefix("/api/user/"),
                    query.get("pipeline", ["image_generation"])[0],
                    data.get("hash"),
                    data.get("action"),
                    data.get("token"),
                )
                self.reply({"ok": True})
            except (ValueError, OSError) as error:
                self.reply({"error": str(error)}, 400)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the image review gallery")
    parser.add_argument("--gallery", type=Path, default=Path("gallery"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    server = make_server(args.gallery, args.host, args.port)
    print(f"Review links: http://<your-ip>:{server.server_port}/user/maria", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
