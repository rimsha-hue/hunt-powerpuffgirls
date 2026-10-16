"""SSRF guard: schemes, private addresses, rebinding-proof pinning, redirects, size and type caps."""

import httpx
import pytest
import respx

from app.integrations.fetch import FetchBlockedError, FetchError, SafeFetcher, check_public_ip

PUBLIC_IP = "93.184.216.34"


def resolver_for(mapping: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        return mapping[host]

    return resolve


@pytest.fixture
async def http():  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient() as client:
        yield client


class TestAddressRules:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.5",
            "172.16.3.4",
            "192.168.1.1",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "::ffff:127.0.0.1",
        ],
    )
    def test_non_public_addresses_are_blocked(self, address: str) -> None:
        with pytest.raises(FetchBlockedError):
            check_public_ip(address)

    def test_public_address_passes(self) -> None:
        assert str(check_public_ip(PUBLIC_IP)) == PUBLIC_IP


class TestUrlRules:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://example.com/",
            "ftp://example.com/x",
            "http://user:pass@example.com/",
            "http://example.com:6379/",
            "http://127.0.0.1/admin",
            "http://[::1]/",
            "http://169.254.169.254/latest/meta-data/",
        ],
    )
    async def test_refused_before_any_request(self, http, url: str) -> None:  # type: ignore[no-untyped-def]
        fetcher = SafeFetcher(http, resolver_for({"example.com": [PUBLIC_IP]}))
        with respx.mock(assert_all_called=False) as router:
            with pytest.raises(FetchBlockedError):
                await fetcher.fetch(url)
            assert router.calls.call_count == 0

    async def test_hostname_resolving_to_private_ip_is_blocked(self, http) -> None:  # type: ignore[no-untyped-def]
        fetcher = SafeFetcher(http, resolver_for({"internal.example": ["10.1.2.3"]}))
        with pytest.raises(FetchBlockedError):
            await fetcher.fetch("https://internal.example/")

    async def test_mixed_dns_answers_are_blocked(self, http) -> None:  # type: ignore[no-untyped-def]
        fetcher = SafeFetcher(http, resolver_for({"sneaky.example": [PUBLIC_IP, "127.0.0.1"]}))
        with pytest.raises(FetchBlockedError):
            await fetcher.fetch("https://sneaky.example/")


class TestFetching:
    @respx.mock
    async def test_connects_to_the_checked_ip_with_the_real_host(self, http) -> None:  # type: ignore[no-untyped-def]
        route = respx.get(f"https://{PUBLIC_IP}/about").mock(
            return_value=httpx.Response(200, html="<p>Hello</p>")
        )
        fetcher = SafeFetcher(http, resolver_for({"example.com": [PUBLIC_IP]}))
        result = await fetcher.fetch("https://example.com/about")
        assert result.text == "<p>Hello</p>"
        request = route.calls.last.request
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"

    @respx.mock
    async def test_redirect_to_a_private_address_is_blocked(self, http) -> None:  # type: ignore[no-untyped-def]
        respx.get(f"https://{PUBLIC_IP}/").mock(
            return_value=httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        )
        fetcher = SafeFetcher(http, resolver_for({"example.com": [PUBLIC_IP]}))
        with pytest.raises(FetchBlockedError):
            await fetcher.fetch("https://example.com/")

    @respx.mock
    async def test_redirect_to_a_host_that_resolves_privately_is_blocked(self, http) -> None:  # type: ignore[no-untyped-def]
        respx.get(f"https://{PUBLIC_IP}/").mock(
            return_value=httpx.Response(301, headers={"location": "https://evil.example/"})
        )
        fetcher = SafeFetcher(
            http, resolver_for({"example.com": [PUBLIC_IP], "evil.example": ["192.168.0.10"]})
        )
        with pytest.raises(FetchBlockedError):
            await fetcher.fetch("https://example.com/")

    @respx.mock
    async def test_redirect_loops_are_capped(self, http) -> None:  # type: ignore[no-untyped-def]
        respx.get(f"https://{PUBLIC_IP}/loop").mock(
            return_value=httpx.Response(302, headers={"location": "/loop"})
        )
        fetcher = SafeFetcher(http, resolver_for({"example.com": [PUBLIC_IP]}), max_redirects=3)
        with pytest.raises(FetchError, match="redirected too many times"):
            await fetcher.fetch("https://example.com/loop")

    @respx.mock
    async def test_oversized_pages_are_cut_off(self, http) -> None:  # type: ignore[no-untyped-def]
        respx.get(f"https://{PUBLIC_IP}/").mock(
            return_value=httpx.Response(
                200, content=b"x" * 5000, headers={"content-type": "text/html"}
            )
        )
        fetcher = SafeFetcher(http, resolver_for({"example.com": [PUBLIC_IP]}), max_bytes=1000)
        with pytest.raises(FetchError, match="too large"):
            await fetcher.fetch("https://example.com/")

    @respx.mock
    async def test_non_text_content_is_refused(self, http) -> None:  # type: ignore[no-untyped-def]
        respx.get(f"https://{PUBLIC_IP}/cv.zip").mock(
            return_value=httpx.Response(
                200, content=b"PK", headers={"content-type": "application/zip"}
            )
        )
        fetcher = SafeFetcher(http, resolver_for({"example.com": [PUBLIC_IP]}))
        with pytest.raises(FetchError, match="not a web page"):
            await fetcher.fetch("https://example.com/cv.zip")
