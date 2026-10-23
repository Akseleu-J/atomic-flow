"""Opt-in loopback IDE bridge v1: authenticated offline source inspection only.

The per-instance nonce and project binding prevent accidental cross-project
reuse. They are not substitutes for the process-only bearer credential. Browser
Origin is mandatory and exactly allowlisted; no CORS wildcard or remote edits.
"""
from __future__ import annotations

import hmac
import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from atomic_flow.integrations.mcp import SourceService, _decode, _json

MAX_BODY_BYTES = 16384
MAX_RESPONSE_BYTES = 1024 * 1024


def create_ide_bridge(project: Path, *, token: str | None = None, allowed_origins=(), host="127.0.0.1", port=0):
    """Return an unstarted HTTPServer; host owns thread/start/shutdown lifecycle.

    Supply ATOMIC_FLOW_IDE_TOKEN through the host process environment or token
    argument. It is never written to source, response bodies or access logs.
    """
    token = token if token is not None else os.environ.get("ATOMIC_FLOW_IDE_TOKEN", "")
    origins = frozenset(allowed_origins)
    if host != "127.0.0.1" or type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("IDE bridge requires IPv4 loopback binding")
    if not isinstance(token, str) or not 32 <= len(token) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("IDE bridge requires a strong process-only token of at least 32 characters")
    if not 1 <= len(origins) <= 16:
        raise ValueError("explicit Origin allowlist required")
    for origin in origins:
        parsed = urlsplit(origin)
        if not parsed.scheme or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("allow exact serialized Origins only")
    source = SourceService(project)
    nonce = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "AtomicIDE/1"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, *_):
            pass

        def _reply(self, status, value):
            body = _json(value)
            if len(body) > MAX_RESPONSE_BYTES:
                status, body = 413, b'{"error":"response limit exceeded"}'
            # Closing a Windows socket with unread request bytes can reset it
            # before the rejection reaches the caller. Discard only a bounded
            # raw body; authorization still precedes parsing/source access.
            if not getattr(self, "_body_consumed", False):
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) == 1 and lengths[0].isdigit() and not self.headers.get("Transfer-Encoding"):
                    self.connection.settimeout(0.1)
                    try:
                        self.rfile.read(min(int(lengths[0]), MAX_BODY_BYTES * 4))
                    except OSError:
                        pass
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_POST(self):
            # All authorization gates precede body parsing and source access.
            if (len(self.headers.get_all("Authorization", [])) != 1
                    or not hmac.compare_digest(self.headers.get("Authorization", "").encode("utf-8"), ("Bearer " + token).encode("ascii"))):
                return self._reply(401, {"error": "unauthorized"})
            if len(self.headers.get_all("Origin", [])) != 1 or self.headers.get("Origin") not in origins:
                return self._reply(403, {"error": "Origin denied"})
            if (len(self.headers.get_all("X-Atomic-Project", [])) != 1
                    or self.headers.get("X-Atomic-Project") != source.project_id
                    or len(self.headers.get_all("X-Atomic-Nonce", [])) != 1
                    or not hmac.compare_digest(self.headers.get("X-Atomic-Nonce", "").encode("utf-8"), nonce.encode("ascii"))):
                return self._reply(409, {"error": "project or instance binding mismatch"})
            if self.path not in {"/v1/status", "/v1/read"}:
                return self._reply(404, {"error": "readonly endpoint unavailable"})
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                return self._reply(400, {"error": "bounded JSON body required"})
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_BODY_BYTES:
                return self._reply(413, {"error": "body limit exceeded"})
            try:
                raw = self.rfile.read(int(lengths[0]))
                self._body_consumed = True
                if len(raw) != int(lengths[0]):
                    raise ValueError("short body")
                value = _decode(raw)
                if not isinstance(value, dict):
                    raise ValueError("object required")
                if self.path == "/v1/status" and not value:
                    result = {**source.tree(), "protocol_version": 1, "capabilities": ["source_tree", "source_read"], "writes_enabled": False}
                elif self.path == "/v1/read" and set(value) <= {"path", "offset", "limit"} and isinstance(value.get("path"), str):
                    result = source.read(**value)
                else:
                    raise ValueError("invalid request")
            except (ValueError, TypeError, RuntimeError, OSError):
                return self._reply(400, {"error": "invalid request or unavailable source"})
            return self._reply(200, result)

        def do_GET(self):
            self._reply(405, {"error": "POST required"})

        def do_OPTIONS(self):
            self._reply(405, {"error": "cross-origin preflight unavailable"})

    server = HTTPServer((host, port), Handler)
    server.timeout = 0.25
    server.project_id = source.project_id
    server.nonce = nonce
    return server
