"""模型接口地址校验，防 SSRF（技术方案 9.1）。

规则：只允许 http/https；主机名或解析出的 IP 命中白名单（主机名、IP、CIDR）时通过；
否则解析域名，任一结果是私有、本机、链路本地、保留、组播、未指定地址、100.64/10
共享地址或 IPv4 映射的 IPv6 地址，就拒绝。

已知限制：校验时和 LiteLLM 实际连接时各解析一次域名，中间解析结果可能被换成内网
地址（DNS 重绑定），这一步校验防不住。因为只有系统管理员能填写接口地址，本版接受
这个风险；需要更严时，让 api 容器只能经出网代理访问外部。
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.errors import ModelHostNotAllowedError

# 运营商级 NAT 用的共享地址段，ipaddress 的 is_private 不认为它私有。
SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


async def _resolve_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """用事件循环的 getaddrinfo 解析主机名。单独成函数便于测试模拟。"""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def _is_non_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # IPv4 映射的 IPv6 地址（如 ::ffff:10.0.0.1）按内层 IPv4 判断。
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip in SHARED_ADDRESS_SPACE
    )


def _host_matches(host: str, allowlist: list[str]) -> bool:
    host_lower = host.lower()
    return any(entry.strip().lower() == host_lower for entry in allowlist if entry.strip())


def _ip_matches(
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address], allowlist: list[str]
) -> bool:
    for entry in allowlist:
        entry = entry.strip()
        if not entry:
            continue
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            # 主机名白名单已在 _host_matches 里处理。
            continue
        if any(ip.version == network.version and ip in network for ip in ips):
            return True
    return False


async def check_model_host(api_base: str | None, allowlist: list[str]) -> None:
    """校验模型接口地址；不通过时抛 MODEL_HOST_NOT_ALLOWED。"""
    if not api_base:
        # 为空时由 LiteLLM 使用供应商默认地址。
        return

    parts = urlsplit(api_base)
    if parts.scheme.lower() not in ("http", "https"):
        raise ModelHostNotAllowedError()
    host = parts.hostname
    if not host:
        raise ModelHostNotAllowedError()

    # 主机名直接命中白名单时不再解析。
    if _host_matches(host, allowlist):
        return

    try:
        ips = await _resolve_ips(host)
    except (OSError, ValueError) as exc:
        raise ModelHostNotAllowedError("无法解析接口地址") from exc

    if _ip_matches(ips, allowlist):
        return

    # 任一解析结果不是公网地址就拒绝。
    if any(_is_non_public(ip) for ip in ips):
        raise ModelHostNotAllowedError()
