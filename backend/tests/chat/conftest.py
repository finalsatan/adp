"""P4-06 回答流程测试的公共夹具。

连真实 PostgreSQL、Redis；模型调用用可控的假实现（monkeypatch `litellm.acompletion`，
内部再用 LiteLLM 的 `mock_response`），不访问网络。
"""

from __future__ import annotations

import asyncio
from time import monotonic
from types import SimpleNamespace
from typing import Any

import litellm
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import (
    AppIntentConfig,
    AppModelConfig,
    AppRewriteConfig,
    KbQaAppConfig,
)
from app.chat.pipeline import INTENT_SYSTEM_PROMPT, REWRITE_SYSTEM_PROMPT
from app.core.schemas import Role
from app.models.tables import Model
from tests.factories import make_user


async def make_chat_model(session: AsyncSession, name: str = "回答模型", **overrides) -> Model:
    user = await make_user(session, role=Role.sys_admin)
    fields: dict[str, Any] = {
        "name": name,
        "status": "enabled",
        "created_by": user.id,
        "model_type": "chat",
        "litellm_model": "openai/gpt-4o",
        "cap_thinking": False,
        "default_params": {},
        "first_token_timeout_s": 30,
        "total_timeout_s": 300,
        "max_retries": 0,
    }
    fields.update(overrides)
    model = Model(**fields)
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def make_embedding_model(session: AsyncSession, name: str = "向量模型", **overrides) -> Model:
    fields = {
        "litellm_model": "openai/text-embedding-3-small",
        "embedding_dim": 4,
    }
    fields.update(overrides)
    return await make_chat_model(session, name=name, model_type="embedding", **fields)


async def make_rerank_model(session: AsyncSession, name: str = "重排模型", **overrides) -> Model:
    fields = {"litellm_model": "cohere/rerank-v3"}
    fields.update(overrides)
    return await make_chat_model(session, name=name, model_type="rerank", **fields)


def build_config(model_id: int, **overrides) -> KbQaAppConfig:
    """默认开启意图识别与改写（都用回答模型），可覆盖各段配置。"""
    defaults: dict[str, Any] = {
        "model": AppModelConfig(chat_model_id=model_id),
        "intent": AppIntentConfig(),
        "rewrite": AppRewriteConfig(),
    }
    defaults.update(overrides)
    return KbQaAppConfig(**defaults)


def messages_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


class FakeLlm:
    """可控的假模型：按系统提示词区分意图 / 改写 / 闲聊，记录调用与进出时间。

    monkeypatch `litellm.acompletion`，返回轻量的响应对象（等价于 LiteLLM 的
    `mock_response`，见 `tests/gateway/test_gateway_chat.py`），不访问网络、不引入
    LiteLLM 的 CPU 开销，便于验证并行耗时。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.entered: dict[str, float] = {}
        self.left: dict[str, float] = {}
        self.delays: dict[str, float] = {}
        self.raise_for: dict[str, BaseException] = {}
        self.barrier: asyncio.Barrier | None = None
        self.intent_reply = '{"intent": "knowledge"}'
        self.rewrite_reply = "改写后的问题"
        self.chat_reply = "闲聊的回应"

    def kind_of(self, messages: list[dict[str, Any]]) -> str:
        text = messages_text(messages)
        if INTENT_SYSTEM_PROMPT in text:
            return "intent"
        if REWRITE_SYSTEM_PROMPT in text:
            return "rewrite"
        return "chat"

    async def __call__(self, **kwargs: Any):
        messages = kwargs.get("messages") or []
        kind = self.kind_of(messages)
        self.calls.append(kind)
        self.entered[kind] = monotonic()
        try:
            if self.barrier is not None and kind in ("intent", "rewrite"):
                await self.barrier.wait()
            delay = self.delays.get(kind, 0.0)
            if delay:
                await asyncio.sleep(delay)
            error = self.raise_for.get(kind)
            if error is not None:
                raise error
            reply = {
                "intent": self.intent_reply,
                "rewrite": self.rewrite_reply,
                "chat": self.chat_reply,
            }[kind]
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=reply, reasoning_content=None))
                ],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
                model="fake-model",
            )
        finally:
            self.left[kind] = monotonic()


class Emitter:
    """收集流程发出的事件，代替 P4-09 的 SSE 写入。"""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def __call__(self, event: Any) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [event.type for event in self.events]


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLlm:
    fake = FakeLlm()
    monkeypatch.setattr(litellm, "acompletion", fake)
    return fake
