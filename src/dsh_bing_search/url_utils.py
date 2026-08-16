from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import re
import socket
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
}
_MARKET_RE = re.compile(r"^[A-Za-z]{2,3}-[A-Za-z]{2}$")


def normalize_market(value: str) -> str:
    value = value.strip()
    if not _MARKET_RE.fullmatch(value):
        raise ValueError("market must look like en-US or zh-CN")
    language, country = value.split("-", 1)
    return f"{language.lower()}-{country.upper()}"


def _safe_b64decode(value: str) -> str | None:
    try:
        raw = value.encode("ascii")
        raw += b"=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8", errors="strict")
    except (ValueError, UnicodeError):
        return None
    return decoded if decoded.startswith(("http://", "https://")) else None


def unwrap_bing_url(href: str) -> str:
    """Decode Bing's common /ck/a?u=a1<base64> tracking links."""
    try:
        parts = urlsplit(href)
    except ValueError:
        return href

    host = (parts.hostname or "").lower().rstrip(".")
    if not (host == "bing.com" or host.endswith(".bing.com")):
        return href
    if parts.path.rstrip("/") != "/ck/a":
        return href

    query = parse_qs(parts.query)
    value = query.get("u", [None])[0] or query.get("url", [None])[0]
    if not value:
        return href

    value = unquote(value)
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("a1"):
        decoded = _safe_b64decode(value[2:])
        if decoded:
            return decoded
    return href


def canonicalize_url(url: str) -> str:
    """Normalize an HTTP URL for deduplication without changing path semantics."""
    value = url.strip()
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return value

    if scheme not in {"http", "https"} or not host:
        return value

    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return value

    host_for_netloc = f"[{host}]" if ":" in host else host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host_for_netloc}:{port}"
    else:
        netloc = host_for_netloc

    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_KEYS
    ]
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(query, doseq=True), ""))


def source_id_for(url: str) -> str:
    digest = hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()[:12]
    return f"src_{digest}"


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


async def validate_public_http_url(url: str) -> None:
    """Reject non-HTTP URLs and hosts resolving to non-public addresses."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid URL") from exc

    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("only http:// and https:// URLs are allowed")
    if not parts.hostname:
        raise ValueError("URL must include a hostname")
    if parts.username or parts.password:
        raise ValueError("userinfo in URLs is not allowed")

    host = parts.hostname.rstrip(".")
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise ValueError("localhost is not allowed")

    try:
        ipaddress.ip_address(host)
    except ValueError:
        lookup_port = port or (443 if parts.scheme.lower() == "https" else 80)
        try:
            infos = await asyncio.to_thread(
                socket.getaddrinfo,
                host,
                lookup_port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise ValueError(f"hostname resolution failed: {exc}") from exc
        addresses = {info[4][0].split("%", 1)[0] for info in infos}
        if not addresses or any(not _is_public_ip(address) for address in addresses):
            raise ValueError("URL resolves to a non-public IP address")
    else:
        if not _is_public_ip(host):
            raise ValueError("non-public IP addresses are not allowed")
