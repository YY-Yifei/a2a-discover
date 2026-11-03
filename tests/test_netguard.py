"""SSRF 边界：地址校验、URL 规范化、DNS 固定。"""

from __future__ import annotations

import socket

import pytest

from adp_registry import netguard

PRIVATE = [
    "127.0.0.1",
    "10.1.2.3",
    "172.20.0.1",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "224.0.0.1",
    "::1",
    "fe80::1",
    "fd00::1",
    "::ffff:127.0.0.1",
    "2001:db8::1",
]

PUBLIC = ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "93.184.216.34"]


@pytest.mark.parametrize("ip", PRIVATE)
def test_private_addresses_are_blocked(ip):
    assert netguard.is_public_ip(ip) is False


@pytest.mark.parametrize("ip", PUBLIC)
def test_public_addresses_pass(ip):
    assert netguard.is_public_ip(ip) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://geo.example.com/.well-known/agent-card.json",
        "https://user:pass@geo.example.com/card.json",
        "https://geo.example.com/card.json#fragment",
        "https:///card.json",
        "not-a-url",
        "",
    ],
)
def test_normalize_url_rejects(url):
    with pytest.raises(netguard.UrlError):
        netguard.normalize_url(url)


def test_normalize_url_canonicalizes_host_and_default_port():
    """spec §4.1：scheme/host 小写 + 去默认端口 :443。"""
    assert netguard.normalize_url("https://Geo.Example.com:443/Card.json") == (
        "https://geo.example.com/Card.json"
    )
    assert netguard.normalize_url("HTTPS://GEO.EXAMPLE.COM/Card.json") == (
        "https://geo.example.com/Card.json"
    )


def test_normalize_url_uppercases_percent_escapes():
    """spec §4.1：百分号编码统一为大写十六进制（path 与 query 各做一次）。"""
    assert netguard.normalize_url("https://geo.example.com/a%2fb?x=%3a") == (
        "https://geo.example.com/a%2Fb?x=%3A"
    )


def test_normalize_url_does_not_rewrite_semantics():
    """不补尾斜杠、不合并 ../、不排序 query、保留非默认端口。"""
    url = "https://geo.example.com/.well-known/agent-card.json?x=1"
    assert netguard.normalize_url(url) == url
    assert netguard.normalize_url("https://geo.example.com/a/../b") == (
        "https://geo.example.com/a/../b"
    )
    assert netguard.normalize_url("https://geo.example.com:8443/card.json") == (
        "https://geo.example.com:8443/card.json"
    )


def test_normalize_url_rejects_long_url():
    with pytest.raises(netguard.UrlError):
        netguard.normalize_url("https://geo.example.com/" + "a" * 2048)


def test_normalize_url_rejects_non_ascii_host():
    """spec §4.1：非 ASCII host 直接拒绝，不做 IDNA 映射。"""
    for url in (
        "https://例子.测试/card.json",
        "https://exämple.com/card.json",
    ):
        with pytest.raises(netguard.UrlError):
            netguard.normalize_url(url)


def test_punycode_host_is_accepted_and_is_a_different_entry():
    """punycode 是 ASCII，合法；且与 Unicode 写法不是同一条目（不做映射）。"""
    assert netguard.normalize_url("https://xn--fsqu00a.xn--0zwm56d/card.json") == (
        "https://xn--fsqu00a.xn--0zwm56d/card.json"
    )


def _resolver(mapping):
    def resolve(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))
            for ip in mapping[host]
        ]

    return resolve


def test_resolve_public_ips_accepts_public_only():
    resolver = _resolver({"geo.example.com": ["93.184.216.34"]})
    assert netguard.resolve_public_ips(
        "geo.example.com", 443, resolver=resolver
    ) == ["93.184.216.34"]


def test_resolve_public_ips_rejects_mixed_answers():
    """混合解析（公网 + 私网）整次拒绝 —— 不取「公网那个」。"""
    resolver = _resolver({"geo.example.com": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(netguard.BlockedAddress):
        netguard.resolve_public_ips("geo.example.com", 443, resolver=resolver)


def test_resolve_public_ips_reports_dns_failure():
    def broken(host, port, **kwargs):
        raise socket.gaierror("nope")

    with pytest.raises(netguard.BlockedAddress):
        netguard.resolve_public_ips("nope.example", 443, resolver=broken)


def test_pinned_dns_serves_validated_addresses():
    with netguard.pinned_dns("geo.example.com", 443, ["93.184.216.34"]):
        infos = socket.getaddrinfo("geo.example.com", 443, type=socket.SOCK_STREAM)
        assert {info[4][0] for info in infos} == {"93.184.216.34"}
        # 大小写不敏感
        assert socket.getaddrinfo("GEO.example.com", 443)


def test_pinned_dns_refuses_other_hosts():
    with netguard.pinned_dns("geo.example.com", 443, ["93.184.216.34"]):
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("evil.example.com", 443)


def test_pinned_dns_restores_original_resolver():
    original = socket.getaddrinfo
    with netguard.pinned_dns("geo.example.com", 443, ["93.184.216.34"]):
        assert socket.getaddrinfo is not original
    assert socket.getaddrinfo is original


def test_host_and_port():
    assert netguard.host_and_port("https://geo.example.com/x") == ("geo.example.com", 443)
    assert netguard.host_and_port("https://geo.example.com:8443/x") == (
        "geo.example.com",
        8443,
    )
