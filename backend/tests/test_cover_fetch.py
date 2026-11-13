"""Fetching a cover by its address: the address can come from a page, so the
sidecar must never be steered into this computer or its network — not by the
address itself, and not by a redirect."""
from __future__ import annotations

import ipaddress
import socket

import httpx
import pytest

from app.scraper import covers

PNG = b"\x89PNG\r\n\x1a\n-cover"

# what each host name resolves to in these tests: nothing touches the network
HOSTS = {
    "cdn.example": "93.184.216.34",
    "other.example": "93.184.216.35",
    "router.example": "192.168.1.1",
    "self.example": "127.0.0.1",
    "metadata.example": "169.254.169.254",
    "mapped.example": "::ffff:10.0.0.1",
}


@pytest.fixture
def served(monkeypatch):
    """Answer requests from `routes` (url → response) and record them."""
    routes: dict[str, httpx.Response] = {}
    asked: list[str] = []

    def resolve(host, port, *args, **kwargs):
        try:
            ip = str(ipaddress.ip_address(host))  # an address written out resolves to itself
        except ValueError:
            if host not in HOSTS:
                raise socket.gaierror("unknown host")
            ip = HOSTS[host]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    monkeypatch.setattr(covers.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(covers, "_client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False))
    return routes, asked


def image(data: bytes = PNG) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "image/png"}, content=data)


def moved(to: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": to})


def test_a_public_address_is_fetched(served):
    routes, _ = served
    routes["https://cdn.example/c.png"] = image()
    assert covers.fetch_cover("https://cdn.example/c.png") == (PNG, "png")


@pytest.mark.parametrize("url", [
    "http://192.168.1.1/admin.png",       # a private address, written out
    "http://127.0.0.1:8787/api/titles",   # this computer — the sidecar itself
    "http://router.example/logo.png",      # a name for the local network
    "http://self.example/x.png",
    "http://metadata.example/latest",      # link-local
    "http://mapped.example/x.png",         # IPv4-mapped IPv6 of a private address
    "http://nowhere.example/x.png",        # a name that does not resolve
    "http://[::1]/x.png",                  # this computer, in IPv6
    "http://0.0.0.0/x.png",
    "http://[::7f00:1]/x.png",             # IPv4-compatible IPv6 of 127.0.0.1
    "http://[64:ff9b::c0a8:101]/x.png",    # NAT64 of 192.168.1.1
    "http://[2002:c0a8:101::1]/x.png",     # 6to4 of 192.168.1.1
    "http://[fec0::1]/x.png",              # site-local
])
def test_an_address_off_the_public_internet_is_never_asked(served, url):
    _, asked = served
    assert covers.fetch_cover(url) is None
    assert asked == []


def test_a_redirect_into_the_local_network_is_not_followed(served):
    routes, asked = served
    routes["https://cdn.example/c.png"] = moved("http://192.168.1.1/admin.png")
    assert covers.fetch_cover("https://cdn.example/c.png") is None
    assert asked == ["https://cdn.example/c.png"]


def test_a_redirect_to_a_file_is_not_followed(served):
    routes, asked = served
    routes["https://cdn.example/c.png"] = moved("file:///C:/Windows/win.ini")
    assert covers.fetch_cover("https://cdn.example/c.png") is None
    assert asked == ["https://cdn.example/c.png"]


def test_a_redirect_to_another_public_host_is_followed(served):
    routes, asked = served
    routes["https://cdn.example/c.png"] = moved("https://other.example/real.png")
    routes["https://other.example/real.png"] = image()
    assert covers.fetch_cover("https://cdn.example/c.png") == (PNG, "png")
    assert asked == ["https://cdn.example/c.png", "https://other.example/real.png"]


def test_a_relative_redirect_stays_on_its_host(served):
    routes, _ = served
    routes["https://cdn.example/c.png"] = moved("/covers/c2.png")
    routes["https://cdn.example/covers/c2.png"] = image()
    assert covers.fetch_cover("https://cdn.example/c.png") == (PNG, "png")


def test_a_redirect_loop_gives_up(served):
    routes, asked = served
    routes["https://cdn.example/a.png"] = moved("https://cdn.example/b.png")
    routes["https://cdn.example/b.png"] = moved("https://cdn.example/a.png")
    assert covers.fetch_cover("https://cdn.example/a.png") is None
    assert len(asked) == covers._MAX_REDIRECTS + 1


def test_what_is_not_an_image_is_refused(served):
    routes, _ = served
    routes["https://cdn.example/page"] = httpx.Response(
        200, headers={"content-type": "text/html"}, content=b"<html>")
    assert covers.fetch_cover("https://cdn.example/page") is None
