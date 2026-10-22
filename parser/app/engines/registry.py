"""引擎注册表：按名字取引擎，并给 /v1/engines 提供列表。"""

from __future__ import annotations

from .base import Engine

_ENGINES: dict[str, Engine] = {}


def register(engine: Engine) -> None:
    _ENGINES[engine.name] = engine


def unregister(name: str) -> None:
    _ENGINES.pop(name, None)


def clear() -> None:
    _ENGINES.clear()


def get_engine(name: str) -> Engine | None:
    return _ENGINES.get(name)


def list_engines() -> list[Engine]:
    return list(_ENGINES.values())


def bootstrap(enable_fake_engine: bool = False) -> None:
    """初始化注册表。主进程和每个子进程都要调用一次。"""
    clear()
    if enable_fake_engine:
        from .fake import FakeEngine

        register(FakeEngine())
