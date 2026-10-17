"""SSRF-safe fetcher for every URL that comes from untrusted input (portfolios, apply pages).

Rules:
- Only http and https, default ports only, no credentials in the URL.
- The hostname is resolved here and every address must be public: private, loopback,
  link-local (cloud metadata), CGNAT, multicast and reserved ranges are refused.
- The connection goes to the exact IP that was checked (TLS still verifies the real hostname
  through SNI), so a DNS answer that changes between check and use (rebinding) cannot slip through.
- Redirects are followed by hand and every hop is checked again. Hops, size and time are capped.
"""

import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import anyio
import httpx
import structlog

logger = structlog.get_logger()

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 5
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown")
DEFAULT_USER_AGENT = "PowerHuntBot (evidence verification)"

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    """The page could not be fetched. The message is safe to show the user."""


class FetchHTTPError(FetchError):
    """The site answered with an error status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"The site answered with HTTP {status}.")
        self.status = status


class FetchBlockedError(FetchError):
    """The URL points somewhere we refuse to go."""


@dataclass(frozen=True)
class FetchResult:
    url: str  # final URL after redirects
    status: int
    content_type: str
    text: str


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def check_public_ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise FetchBlockedError("That address is not on the public internet.")
    return ip


def _validate(url: str) -> tuple[str, str, int]:
    """(scheme, hostname, port) for an acceptable URL, else FetchBlockedError."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchBlockedError("That is not a valid URL.") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise FetchBlockedError("Only http and https links can be fetched.")
    if parts.username or parts.password:
        raise FetchBlockedError("Links with embedded credentials are not allowed.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchBlockedError("The link has no host name.")
    default_port = 443 if scheme == "https" else 80
    if port not in (None, default_port):
        raise FetchBlockedError("Only standard web ports are allowed.")
    return scheme, host, default_port


def _pinned(url: str, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    parts = urlsplit(url)
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    return urlunsplit((parts.scheme, host, parts.path or "/", parts.query, ""))


class SafeFetcher:
    def __init__(
        self,
        http: httpx.AsyncClient,
        resolver: Resolver = system_resolver,
        *,
        max_bytes: int = MAX_BYTES,
        timeout: float = TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.http = http
        self.resolver = resolver
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.user_agent = user_agent

    async def _resolve_public(
        self, host: str, port: int
    ) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            return check_public_ip(str(literal))
        try:
            addresses = await self.resolver(host, port)
        except OSError as exc:
            raise FetchError(f"Could not find the site {host}.") from exc
        if not addresses:
            raise FetchError(f"Could not find the site {host}.")
        # Every address must be public, or a mixed answer could steer us inside.
        checked = [check_public_ip(a) for a in addresses]
        return checked[0]

    async def fetch(self, url: str, accept: tuple[str, ...] = TEXT_TYPES) -> FetchResult:
        current = url.strip()
        with anyio.fail_after(self.timeout * (self.max_redirects + 1)):
            for _ in range(self.max_redirects + 1):
                scheme, host, port = _validate(current)
                ip = await self._resolve_public(host, port)
                request = self.http.build_request(
                    "GET",
                    _pinned(current, ip),
                    headers={
                        "Host": host,
                        "User-Agent": self.user_agent,
                        "Accept": ", ".join(accept),
                    },
                    extensions={"sni_hostname": host} if scheme == "https" else {},
                    timeout=self.timeout,
                )
                try:
                    response = await self.http.send(request, stream=True, follow_redirects=False)
                except httpx.TimeoutException as exc:
                    raise FetchError("The site took too long to respond.") from exc
                except httpx.HTTPError as exc:
                    raise FetchError("Could not connect to the site.") from exc
                try:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("The site sent a redirect without a destination.")
                        current = urljoin(current, location)
                        continue
                    return await self._read(current, response, accept)
                finally:
                    await response.aclose()
        raise FetchError("The link redirected too many times.")

    async def _read(
        self, url: str, response: httpx.Response, accept: tuple[str, ...]
    ) -> FetchResult:
        if response.status_code >= 400:
            raise FetchHTTPError(response.status_code)
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith(accept):
            raise FetchError("That link is not a web page.")
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            raise FetchError("The page is too large to read.")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > self.max_bytes:
                raise FetchError("The page is too large to read.")
        text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
        logger.info("fetched", status=response.status_code, bytes=len(body))
        return FetchResult(
            url=url, status=response.status_code, content_type=content_type, text=text
        )
