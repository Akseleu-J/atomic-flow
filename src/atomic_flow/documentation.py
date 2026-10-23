"""Host-allowlisted HTTPS documentation fetch and search over explicit pages.

No ambient proxies/credentials, model APIs or arbitrary Internet search provider.
Every redirect and every resolved address is admitted before opening a socket.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from atomic_flow.activation.redact import redact


class DocumentationError(ValueError):
    pass


_DNS_SLOTS = threading.BoundedSemaphore(4)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "pre", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "pre", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the already admitted numeric IP; TLS still verifies hostname.
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _host(value):
    if not isinstance(value, str) or not value or value.endswith(".") or any(c in value for c in "/:@\\\r\n\0*"):
        raise DocumentationError("use an exact documentation hostname")
    try:
        host = value.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise DocumentationError("invalid documentation hostname") from None
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", host) or ".." in host:
        raise DocumentationError("invalid documentation hostname")
    return host


class DocumentationClient:
    def __init__(self, allowed_hosts, *, approved=False, timeout=10, max_bytes=1_000_000,
                 cancel_check=None, resolver=None, connection_factory=None):
        if approved is not True:
            raise DocumentationError("documentation network scope requires explicit host approval")
        if isinstance(allowed_hosts, (str, bytes)):
            raise DocumentationError("provide an explicit collection of hosts")
        self.allowed_hosts = frozenset(_host(host) for host in allowed_hosts)
        if not 1 <= len(self.allowed_hosts) <= 16 or not 0.1 <= timeout <= 30 or type(max_bytes) is not int or not 1024 <= max_bytes <= 1_000_000:
            raise DocumentationError("documentation scope/limits invalid")
        self.timeout, self.max_bytes = timeout, max_bytes
        self.cancel_check = cancel_check or (lambda: None)
        # Trusted host dependency injection for deterministic tests only.
        self._resolver = resolver or socket.getaddrinfo
        self._connection = connection_factory or _PinnedHTTPS

    def _admit(self, url, *, cancel_check=None, deadline=None):
        if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in url) or "\\" in url:
            raise DocumentationError("invalid documentation URL")
        parsed = urlsplit(url)
        try:
            if parsed.scheme != "https" or parsed.port not in {None, 443} or parsed.username or parsed.password or parsed.fragment:
                raise DocumentationError("documentation URL requires HTTPS without credentials or fragments")
            host = _host(parsed.hostname)
        except (ValueError, TypeError):
            raise DocumentationError("invalid documentation URL authority") from None
        if host not in self.allowed_hosts:
            raise DocumentationError("documentation host is outside approved scope")
        # API keys and authenticated query strings are outside this connector.
        keys = [key.casefold().replace("-", "_") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
        if any(key in {"api_key", "apikey", "access_token", "token", "secret", "password", "signature"} for key in keys):
            raise DocumentationError("credential-bearing documentation query is not permitted")
        self.cancel_check()
        check = cancel_check or self.cancel_check
        check()
        if not _DNS_SLOTS.acquire(blocking=False):
            raise DocumentationError("documentation DNS capacity exhausted")
        result = queue.Queue(maxsize=1)
        def resolve():
            try:
                result.put((True, self._resolver(host, 443, type=socket.SOCK_STREAM)))
            except Exception:
                result.put((False, None))
            finally:
                _DNS_SLOTS.release()
        threading.Thread(target=resolve, daemon=True, name="atomic-docs-dns").start()
        expires = min(deadline, time.monotonic() + self.timeout) if deadline is not None else time.monotonic() + self.timeout
        while True:
            self.cancel_check()
            check()
            if time.monotonic() >= expires:
                raise DocumentationError("documentation DNS deadline exceeded")
            try:
                okay, rows = result.get(timeout=0.025)
                break
            except queue.Empty:
                continue
        if not okay:
            raise DocumentationError("documentation DNS resolution failed")
        addresses = []
        for row in rows:
            if len(addresses) >= 16:
                raise DocumentationError("documentation DNS result oversized")
            try:
                address = ipaddress.ip_address(row[4][0])
            except (ValueError, IndexError, TypeError):
                raise DocumentationError("invalid DNS address") from None
            if not address.is_global or (getattr(address, "ipv4_mapped", None) is not None and not address.ipv4_mapped.is_global):
                raise DocumentationError("private/reserved documentation address denied")
            addresses.append(str(address))
        if not addresses or len(addresses) > 16:
            raise DocumentationError("documentation DNS result is empty or oversized")
        return parsed, host, addresses[0]

    def fetch(self, url: str, *, offset=0, limit=12000, cancel_check=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 256 <= limit <= 12000:
            raise DocumentationError("invalid documentation page range")
        check = cancel_check or self.cancel_check
        requested, current = url, url
        deadline = time.monotonic() + self.timeout
        for hop in range(4):
            self.cancel_check()
            check()
            parsed, host, address = self._admit(current, cancel_check=check, deadline=deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DocumentationError("documentation request deadline exceeded")
            conn = self._connection(host, address, max(0.1, remaining))
            try:
                path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
                conn.request("GET", path, headers={"Accept": "text/html,text/plain,application/json", "Accept-Encoding": "identity",
                    "User-Agent": "atomic-flow-docs/1", "Connection": "close"})
                response = conn.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    if hop == 3 or not response.getheader("Location"):
                        raise DocumentationError("documentation redirect limit or invalid target")
                    current = urljoin(current, response.getheader("Location"))
                    continue
                if response.status != 200:
                    raise DocumentationError("documentation server refused request")
                if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise DocumentationError("compressed documentation is outside the bounded transport contract")
                content_type = response.getheader("Content-Type", "").lower()
                mime = content_type.split(";", 1)[0].strip()
                if mime not in {"text/plain", "text/html", "application/xhtml+xml", "application/json"}:
                    raise DocumentationError("unsupported documentation content type")
                declared = response.getheader("Content-Length")
                if declared and (not declared.isdigit() or int(declared) > self.max_bytes):
                    raise DocumentationError("documentation exceeds byte limit")
                raw = bytearray()
                while True:
                    self.cancel_check()
                    check()
                    if time.monotonic() >= deadline:
                        raise DocumentationError("documentation request deadline exceeded")
                    if getattr(conn, "sock", None) is not None:
                        conn.sock.settimeout(max(0.1, deadline - time.monotonic()))
                    chunk = response.read(min(16384, self.max_bytes + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > self.max_bytes:
                        raise DocumentationError("documentation exceeds byte limit")
                text = bytes(raw).decode("utf-8-sig")
                if "\0" in text:
                    raise DocumentationError("binary documentation is not supported")
                if mime in {"text/html", "application/xhtml+xml"}:
                    parser = _Text()
                    parser.feed(text)
                    text = "".join(parser.parts)
                text = redact(text)
                start = min(offset, len(text))
                end = min(start + limit, len(text))
                check()
                return {"url": current, "requested_url": requested, "source": text[start:end],
                    "content_sha256": hashlib.sha256(raw).hexdigest(), "offset": start,
                    "next_offset": end if end < len(text) else None, "total_chars": len(text),
                    "truncated": end < len(text), "retrieved_at": datetime.now(UTC).isoformat(),
                    "provenance": {"origin": "https_documentation", "trust": "external_data", "host": host,
                                   "permissions_granted": False, "read_evidence": False}, "data_only": True}
            except (OSError, http.client.HTTPException, UnicodeError):
                raise DocumentationError("documentation transport or UTF-8 decoding failed") from None
            finally:
                conn.close()
        raise DocumentationError("documentation redirect limit exceeded")

    def search(self, query: str, urls: list[str], *, limit=5, cancel_check=None):
        """Lexical documentation search over explicitly selected approved pages."""
        if not isinstance(query, str) or not query.strip() or len(query.encode()) > 1000 or not isinstance(urls, list) or not 1 <= len(urls) <= 8:
            raise DocumentationError("search requires a bounded query and 1-8 explicit documentation URLs")
        if type(limit) is not int or not 1 <= limit <= 8:
            raise DocumentationError("invalid documentation search limit")
        # Admit the whole URL set before making the first request.
        for url in urls:
            self._admit(url, cancel_check=cancel_check)
        terms = set(re.findall(r"\w+", query.casefold()))
        if not terms:
            raise DocumentationError("search query has no searchable terms")
        ranked, incomplete = [], []
        for url in urls:
            page = self.fetch(url, limit=12000, cancel_check=cancel_check)
            source = page.pop("source")
            score = sum(source.casefold().count(term) for term in terms)
            if page["truncated"]:
                incomplete.append(page["url"])
            if score:
                position = min((source.casefold().find(term) for term in terms if term in source.casefold()), default=0)
                ranked.append({**page, "score": score, "excerpt": source[max(0, position - 120):position + 1000]})
        ranked.sort(key=lambda item: (-item["score"], item["url"]))
        return {"results": ranked[:limit], "searched_pages": len(urls), "scope": "first 12000 redacted characters of explicit approved pages",
                "incomplete_pages": incomplete, "data_only": True, "query": redact(query)}
