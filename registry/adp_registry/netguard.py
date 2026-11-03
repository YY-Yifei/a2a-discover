"""抓取安全边界：URL 规范化、地址校验、DNS 固定（防 DNS rebinding）。

这些不是"最佳实践"，是 ``spec/adp-core.md`` §7 里逐条列出的 MUST。
本模块的每个拒绝分支都对应规范中的一条要求。
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
import socket
import threading
from collections.abc import Iterator, Sequence
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048

_PERCENT = re.compile(r"%[0-9a-fA-F]{2}")

_BLOCKED_V4 = tuple(
    ipaddress.ip_network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
    )
)

_BLOCKED_V6 = tuple(
    ipaddress.ip_network(network)
    for network in (
        "::/128",
        "::1/128",
        "::ffff:0:0/96",
        "2001:db8::/32",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)

_DNS_LOCK = threading.RLock()


class UrlError(ValueError):
    """URL 不满足 ADP 的抓取前提（返回 400 BAD_URL）。"""


class BlockedAddress(Exception):
    """目标地址落在私有 / 保留 / 回环段（返回 422 CARD_UNREACHABLE）。"""


def is_public_ip(ip: str) -> bool:
    """地址是否可用于对外抓取。IPv4-mapped 地址按映射后的 v4 判定。"""
    address = ipaddress.ip_address(ip.split("%")[0])
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if address.version == 4:
        return not any(address in network for network in _BLOCKED_V4)
    return not any(address in network for network in _BLOCKED_V6)


def normalize_url(url: str) -> str:
    """校验并返回**归一化**后的 https URL（规则见 spec §4.1）。

    归一化 MUST 且仅 MUST 做三件事：

    1. scheme 与 host 转小写；
    2. 去掉默认端口 ``:443``；
    3. path 与 query 的百分号编码统一为大写十六进制。

    不做任何语义重写：不补尾斜杠、不合并 ``/./`` 与 ``/../``、不排序或改写 query
    —— ``id`` 由归一化后的字符串派生，多余的改写会让 ``id`` 漂移。

    拒绝：非 https、带 userinfo、带 fragment、非 ASCII host、超过 2048 字符、非绝对 URL。

    非 ASCII host 一律拒绝而不做 IDNA 映射 —— UTS-46 的映射与算法版本相关，
    纳入它会让 ``id`` 从「URL 的确定性函数」退化为「URL + 本版 IDNA」（spec §4.1）。
    """
    if not isinstance(url, str):
        raise UrlError("url 必须是字符串")
    candidate = url.strip()
    if not candidate:
        raise UrlError("url 不能为空")
    if len(candidate) > MAX_URL_LENGTH:
        raise UrlError(f"url 超过 {MAX_URL_LENGTH} 字符")
    parts = urlsplit(candidate)
    if parts.scheme.lower() != "https":
        raise UrlError("url 必须使用 https")
    if parts.username or parts.password:
        raise UrlError("url 不得包含 userinfo")
    if parts.fragment:
        raise UrlError("url 不得包含 fragment")
    host = parts.hostname
    if not host:
        raise UrlError("url 缺少主机名")
    if not host.isascii():
        raise UrlError("url 的 host 必须是纯 ASCII（非 ASCII 域名请先转写为 punycode）")
    try:
        port = parts.port
    except ValueError as exc:
        raise UrlError("url 端口非法") from exc

    host = host.lower()
    if ":" in host:  # IPv6 字面量要放回方括号
        host = f"[{host}]"
    netloc = host if port in (None, 443) else f"{host}:{port}"

    return urlunsplit(
        ("https", netloc, _upper_percent(parts.path), _upper_percent(parts.query), "")
    )


def _upper_percent(text: str) -> str:
    """把百分号编码的三元组统一为大写十六进制（``%2f`` → ``%2F``）。"""
    return _PERCENT.sub(lambda match: match.group(0).upper(), text)


def host_and_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or 443
    return host, port


def resolve_public_ips(
    host: str,
    port: int,
    *,
    resolver=socket.getaddrinfo,
) -> list[str]:
    """解析并校验全部地址。

    混合解析（既有公网又有私网地址）整次拒绝，不做"只取公网那个"的降级 ——
    混合解析本身就是攻击信号。
    """
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedAddress(f"DNS 解析失败：{host}（{exc}）") from exc

    ips: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in ips:
            ips.append(address)
    if not ips:
        raise BlockedAddress(f"DNS 无结果：{host}")

    blocked = [ip for ip in ips if not is_public_ip(ip)]
    if blocked:
        raise BlockedAddress(f"{host} 解析到受限地址 {blocked[0]}")
    return ips


@contextlib.contextmanager
def pinned_dns(host: str, port: int, ips: Sequence[str]) -> Iterator[None]:
    """在块内把 ``socket.getaddrinfo`` 钉死到已校验的 IP 上。

    这是 §7.2 的落地方式：只校验主机名挡不住 DNS rebinding —— 校验时解析到公网、
    连接时再解析到 127.0.0.1。把连接层看到的结果替换成校验过的那一份，
    校验与连接才是同一组地址。
    """
    original = socket.getaddrinfo
    expected = host.lower()

    def fake_getaddrinfo(host_arg, port_arg, family=0, type=0, proto=0, flags=0):
        if not isinstance(host_arg, str) or host_arg.lower() != expected:
            raise socket.gaierror(
                socket.EAI_NONAME, f"ADP: 抓取过程中出现未预期的主机名 {host_arg!r}"
            )
        results = []
        for ip in ips:
            is_v6 = ":" in ip
            if family not in (0, socket.AF_UNSPEC):
                wanted = socket.AF_INET6 if family == socket.AF_INET6 else socket.AF_INET
                if (wanted == socket.AF_INET6) != is_v6:
                    continue
            if is_v6:
                sockaddr = (ip, port_arg, 0, 0)
                results.append((socket.AF_INET6, type, proto, "", sockaddr))
            else:
                sockaddr = (ip, port_arg)
                results.append((socket.AF_INET, type, proto, "", sockaddr))
        if not results:
            raise socket.gaierror(socket.EAI_NONAME, "ADP: 无可用地址")
        return results

    with _DNS_LOCK:
        socket.getaddrinfo = fake_getaddrinfo  # type: ignore[assignment]
        try:
            yield
        finally:
            socket.getaddrinfo = original  # type: ignore[assignment]
