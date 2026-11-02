"""Local HTTPS signed-URL source for Docker integration checks only."""

from __future__ import annotations

import hashlib
import hmac
import os
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path("/data/objects/inputs")
SECRET = os.environ["HEATPIPE_API_TOKEN"].encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        pass  # Avoid writing signed query strings to container logs.

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if parsed.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/health")
            self.end_headers()
            return
        name = parsed.path.removeprefix("/files/")
        if not parsed.path.startswith("/files/") or "/" in name or name not in {
            "docker-smoke.att", "docker-smoke.atg",
            "20260813_132634.att", "20260813_132634.atg",
        }:
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        try:
            expires = int(query["expires"][0])
            signature = query["signature"][0]
        except (KeyError, IndexError, ValueError):
            self.send_error(403)
            return
        expected = hmac.new(SECRET, f"{parsed.path}\n{expires}".encode(), hashlib.sha256).hexdigest()
        if expires < time.time() or not hmac.compare_digest(signature, expected):
            self.send_error(403)
            return
        source = ROOT / name
        if not source.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(source.stat().st_size))
        self.end_headers()
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                self.wfile.write(chunk)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/certs/cert.pem", "/certs/key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
