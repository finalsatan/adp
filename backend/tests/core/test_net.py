"""模型接口地址校验（技术方案 9.1）。

解析用 monkeypatch 模拟：_resolve_ips 里把 IP 字面量直接返回，域名走 mapping，
不依赖真实 DNS。
"""

import ipaddress
import socket

import pytest

from app.core import net
from app.core.errors import ModelHostNotAllowedError
from app.core.net import check_model_host

# 这些地址本身就不是公网地址，直接作为主机名给进来。
NON_PUBLIC_HOSTS = [
    "127.0.0.1",
    "10.0.0.1",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.1.1",
    "100.64.0.1",
    "[::1]",
    "[fc00::1]",
    "[::ffff:10.0.0.1]",
    "0.0.0.0",
]


def _patch_resolve(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, object]) -> None:
    async def fake_resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        if host in mapping:
            result = mapping[host]
            if isinstance(result, Exception):
                raise result
            return [ipaddress.ip_address(item) for item in result]  # type: ignore[union-attr]
        try:
            return [ipaddress.ip_address(host)]
        except ValueError as exc:
            raise socket.gaierror(f"cannot resolve {host}") from exc

    monkeypatch.setattr(net, "_resolve_ips", fake_resolve)


@pytest.mark.parametrize("host", NON_PUBLIC_HOSTS)
async def test_non_public_host_rejected(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    _patch_resolve(monkeypatch, {})
    with pytest.raises(ModelHostNotAllowedError):
        await check_model_host(f"https://{host}/v1", [])


async def test_localhost_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, {"localhost": ["127.0.0.1"]})
    with pytest.raises(ModelHostNotAllowedError):
        await check_model_host("http://localhost:8000/v1", [])


async def test_domain_with_public_and_private_results_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 任一解析结果不是公网地址就拒绝。
    _patch_resolve(monkeypatch, {"mixed.example.com": ["93.184.216.34", "10.0.0.1"]})
    with pytest.raises(ModelHostNotAllowedError):
        await check_model_host("https://mixed.example.com/v1", [])


async def test_public_address_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, {"api.example.com": ["93.184.216.34"]})
    await check_model_host("https://api.example.com/v1", [])


async def test_empty_address_passes() -> None:
    await check_model_host(None, [])
    await check_model_host("", [])


async def test_non_http_scheme_rejected() -> None:
    with pytest.raises(ModelHostNotAllowedError):
        await check_model_host("ftp://api.example.com/v1", [])


async def test_allowlisted_hostname_passes_without_resolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 主机名命中白名单时通过，即使它解析到内网。
    _patch_resolve(monkeypatch, {"models.internal": ["10.0.0.9"]})
    await check_model_host("https://MODELS.INTERNAL/v1", ["models.internal"])


async def test_allowlisted_ip_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, {"models.internal": ["10.0.0.5"]})
    await check_model_host("https://models.internal/v1", ["10.0.0.5"])


async def test_allowlisted_cidr_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, {"models.internal": ["10.1.2.3"]})
    await check_model_host("https://models.internal/v1", ["10.0.0.0/8"])


async def test_resolve_failure_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, {"missing.example.com": socket.gaierror("not found")})
    with pytest.raises(ModelHostNotAllowedError) as excinfo:
        await check_model_host("https://missing.example.com/v1", [])
    assert "无法解析接口地址" in excinfo.value.message
