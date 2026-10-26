"""回答流程编排测试（P4-06）。

连真实 PostgreSQL、Redis；意图识别 / 改写 / 闲聊的模型调用用可控的假模型
（`tests/chat/conftest.py` 的 `fake_llm`，内部走 LiteLLM 的 `mock_response`）。
检索、提示词组装、生成是占位，本卡对占位行为断言（空结果 → 兜底）。
"""

from __future__ import annotations

import asyncio
from time import monotonic

import litellm
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import (
    AppDocConfig,
    AppIntentConfig,
    AppKbRef,
    AppKnowledgeConfig,
    AppQaConfig,
    AppQaRerankConfig,
    AppRerankConfig,
    AppRewriteConfig,
)
from app.chat import pipeline
from app.chat.pipeline import (
    INTENT_PROMPT_VERSION,
    REWRITE_PROMPT_VERSION,
    AnswerRun,
    HistoryTurn,
    run_flow,
)
from app.chat.schemas import AnswerKind, AnswerStatus, StepName
from app.core.errors import ModelUnavailableError, RetrievalFailedError
from app.gateway.errors import ModelCallError
from app.models.tables import Model
from tests.chat.conftest import (
    Emitter,
    build_config,
    make_chat_model,
    make_rerank_model,
)
from tests.worker import make_kb


def make_run(
    app, config, *, question: str = "什么是考勤制度？", history=None, **kwargs
) -> AnswerRun:
    return AnswerRun(
        app_id=1,
        config=config,
        question=question,
        sessionmaker=app.state.sessionmaker,
        redis=app.state.redis,
        history=history or [],
        **kwargs,
    )


# ---- 意图识别与问题改写并行（6.2）----


async def test_intent_and_rewrite_run_in_parallel(app, session, fake_llm) -> None:
    """两路都到 barrier 才继续：串行会在 barrier 上超时，并行总耗时约等于较慢的一路。"""
    model = await make_chat_model(session)
    config = build_config(model.id)
    fake_llm.barrier = asyncio.Barrier(2)
    fake_llm.delays = {"intent": 0.3, "rewrite": 0.3}
    history = [HistoryTurn(question="之前的问题", answer="之前的回答")]
    run = make_run(app, config, history=history)
    emit = Emitter()

    started = monotonic()
    await asyncio.wait_for(run_flow(run, emit), timeout=5)
    elapsed = monotonic() - started

    assert set(fake_llm.calls) == {"intent", "rewrite"}
    # 两路在时间上重叠（barrier 保证两路都进入后才继续）。
    assert fake_llm.entered["intent"] < fake_llm.left["rewrite"]
    assert fake_llm.entered["rewrite"] < fake_llm.left["intent"]
    assert elapsed < 0.55, f"两路疑似串行，耗时 {elapsed:.2f}s"
    assert run.intent == "knowledge"
    assert run.query_text == fake_llm.rewrite_reply


async def test_intent_disabled_keeps_knowledge(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id, intent=AppIntentConfig(enabled=False))
    run = make_run(app, config, history=[HistoryTurn("旧问题", "旧回答")])
    await run_flow(run, Emitter())
    assert run.intent == "knowledge"
    assert "intent" not in fake_llm.calls
    assert "rewrite" in fake_llm.calls


async def test_rewrite_disabled_uses_original(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id, rewrite=AppRewriteConfig(enabled=False))
    run = make_run(app, config, history=[HistoryTurn("旧问题", "旧回答")])
    await run_flow(run, Emitter())
    assert run.query_text == run.question
    assert run.rewritten_question == run.question
    assert "rewrite" not in fake_llm.calls


async def test_both_disabled_no_model_call(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(
        model.id,
        intent=AppIntentConfig(enabled=False),
        rewrite=AppRewriteConfig(enabled=False),
    )
    run = make_run(app, config, history=[HistoryTurn("旧问题", "旧回答")])
    await run_flow(run, Emitter())
    assert fake_llm.calls == []
    assert run.intent == "knowledge"
    assert run.query_text == run.question
    # 检索占位返回空 → 兜底，不调用回答模型。
    assert run.kind == AnswerKind.fallback


# ---- 意图识别降级 ----


@pytest.mark.parametrize(
    "reply",
    ["这不是 JSON", '{"intent": "other"}', "", '{"foo": 1}', "[1, 2]"],
)
async def test_intent_parse_failure_falls_back_to_knowledge(app, session, fake_llm, reply) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id, rewrite=AppRewriteConfig(enabled=False))
    fake_llm.intent_reply = reply
    run = make_run(app, config)
    await run_flow(run, Emitter())
    assert run.intent == "knowledge"
    step = _step(run, StepName.intent)
    assert step.output["intent"] == "knowledge"


async def test_intent_timeout_falls_back_to_knowledge(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id, rewrite=AppRewriteConfig(enabled=False))
    fake_llm.raise_for = {
        "intent": litellm.Timeout(message="timeout", model="gpt-4o", llm_provider="openai")
    }
    run = make_run(app, config)
    await run_flow(run, Emitter())
    assert run.intent == "knowledge"
    step = _step(run, StepName.intent)
    assert "degraded" in step.output


# ---- 问题改写降级 ----


@pytest.mark.parametrize("reply", ["", "   ", "很长的问题" * 500])
async def test_rewrite_failure_uses_original(app, session, fake_llm, reply) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id)
    fake_llm.rewrite_reply = reply
    run = make_run(app, config, history=[HistoryTurn("旧问题", "旧回答")])
    await run_flow(run, Emitter())
    assert run.rewrite_ok is False
    assert run.rewritten_question == run.question
    assert run.query_text == run.question


async def test_rewrite_error_uses_original(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id)
    fake_llm.raise_for = {"rewrite": ModelCallError("UPSTREAM_ERROR", "boom")}
    run = make_run(app, config, history=[HistoryTurn("旧问题", "旧回答")])
    await run_flow(run, Emitter())
    assert run.query_text == run.question
    assert "degraded" in _step(run, StepName.rewrite).output


async def test_first_question_skips_rewrite_model(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id)
    run = make_run(app, config, history=[])
    await run_flow(run, Emitter())
    assert "rewrite" not in fake_llm.calls
    assert "intent" in fake_llm.calls
    assert run.rewritten_question == run.question
    assert _step(run, StepName.rewrite).output["skipped"] == "first_question"


# ---- 闲聊与兜底（6.4）----


async def test_chitchat_skips_retrieval_and_discards_rewrite(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id)
    fake_llm.intent_reply = '{"intent": "chitchat"}'
    run = make_run(app, config, history=[HistoryTurn("旧问题", "旧回答")])
    emit = Emitter()
    await run_flow(run, emit)

    assert run.kind == AnswerKind.chitchat
    assert run.content == fake_llm.chat_reply
    # 改写结果被丢弃。
    assert run.rewritten_question is None
    # 不检索。
    assert StepName.retrieve not in [step.name for step in run.trace.build().steps]
    started = [event.step for event in emit.events if event.type == "STEP_STARTED"]
    assert StepName.retrieve not in started
    # 用回答模型（chat）回闲聊。
    assert "chat" in fake_llm.calls


async def test_retrieval_empty_falls_back(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id, intent=AppIntentConfig(enabled=False))
    run = make_run(app, config)
    emit = Emitter()
    await run_flow(run, emit)

    assert run.kind == AnswerKind.fallback
    assert run.content == config.reply.fallback_text
    assert "chat" not in fake_llm.calls
    deltas = [event.delta for event in emit.events if event.type == "TEXT_DELTA"]
    assert deltas == [config.reply.fallback_text]


async def test_generated_placeholder(app, session, fake_llm, monkeypatch) -> None:
    model = await make_chat_model(session)
    config = build_config(
        model.id,
        intent=AppIntentConfig(enabled=False),
        rewrite=AppRewriteConfig(enabled=False),
    )

    async def fake_retrieve(run, emit):
        await pipeline.begin_step(run, emit, StepName.retrieve)
        await pipeline.end_step(
            run, emit, StepName.retrieve, input=run.query_text, output={"count": 1}
        )
        return [object()]

    monkeypatch.setattr(pipeline, "_retrieve", fake_retrieve)
    run = make_run(app, config)
    emit = Emitter()
    await run_flow(run, emit)

    assert run.kind == AnswerKind.generated
    assert run.content == pipeline.GENERATE_PLACEHOLDER
    # P4-08 起组装完整提示词：system + 最后一条 user（含问题原文）。
    assert run.prompt_messages[0]["role"] == "system"
    assert run.prompt_messages[-1]["role"] == "user"
    assert run.prompt_messages[-1]["content"].endswith(f"问题：{run.question}")
    assert run.prompt_version is not None
    deltas = [event.delta for event in emit.events if event.type == "TEXT_DELTA"]
    assert deltas == [pipeline.GENERATE_PLACEHOLDER]


# ---- 回答模型 / 检索出错交给 P4-09（6.5）----


async def test_chitchat_answer_model_error_propagates(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id)
    fake_llm.intent_reply = '{"intent": "chitchat"}'
    fake_llm.raise_for = {"chat": ModelCallError("UPSTREAM_ERROR", "boom")}
    run = make_run(app, config)
    with pytest.raises(ModelCallError):
        await run_flow(run, Emitter())
    assert run.status == AnswerStatus.failed
    assert run.error is not None
    # 失败的 generate 步骤也记进处理过程。
    assert "degraded" in _step(run, StepName.generate).output


async def test_retrieval_failure_propagates(app, session, fake_llm, monkeypatch) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id, intent=AppIntentConfig(enabled=False))

    async def fake_retrieve(run, emit):
        raise RetrievalFailedError()

    monkeypatch.setattr(pipeline, "_retrieve", fake_retrieve)
    run = make_run(app, config)
    with pytest.raises(RetrievalFailedError):
        await run_flow(run, Emitter())
    assert run.status == AnswerStatus.failed
    assert run.error is not None


# ---- 模型可用性预检（6.5）----


@pytest.mark.parametrize(
    "category", ["answer", "intent", "rewrite", "doc_rerank", "qa_rerank", "embedding"]
)
async def test_disabled_referenced_model_unavailable(app, session, fake_llm, category) -> None:
    config = await _config_with_disabled_model(session, category)
    run = make_run(app, config)
    emit = Emitter()

    with pytest.raises(ModelUnavailableError):
        await run_flow(run, emit)

    # 提问开始时就失败：还没发任何步骤事件、没调用模型。
    assert emit.events == []
    assert fake_llm.calls == []


async def _config_with_disabled_model(session: AsyncSession, category: str):
    answer = await make_chat_model(session, name=f"回答-{category}")
    if category == "answer":
        answer.status = "disabled"
        await session.commit()
        return build_config(answer.id)
    if category == "intent":
        model = await make_chat_model(session, name="停用意图", status="disabled")
        return build_config(answer.id, intent=AppIntentConfig(model_id=model.id))
    if category == "rewrite":
        model = await make_chat_model(session, name="停用改写", status="disabled")
        return build_config(answer.id, rewrite=AppRewriteConfig(model_id=model.id))
    if category == "doc_rerank":
        model = await make_rerank_model(session, name="停用文档重排", status="disabled")
        return build_config(
            answer.id,
            knowledge=AppKnowledgeConfig(
                doc=AppDocConfig(rerank=AppRerankConfig(enabled=True, model_id=model.id))
            ),
        )
    if category == "qa_rerank":
        model = await make_rerank_model(session, name="停用问答重排", status="disabled")
        return build_config(
            answer.id,
            knowledge=AppKnowledgeConfig(
                qa=AppQaConfig(rerank=AppQaRerankConfig(enabled=True, model_id=model.id))
            ),
        )
    if category == "embedding":
        kb, _ = await make_kb(session, "停用向量库")
        await session.execute(
            sa.update(Model).where(Model.id == kb.embedding_model_id).values(status="disabled")
        )
        await session.commit()
        return build_config(answer.id, knowledge=AppKnowledgeConfig(kbs=[AppKbRef(kb_id=kb.id)]))
    raise AssertionError(category)


async def test_deleted_answer_model_unavailable(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    model.deleted_at = sa.func.now()
    await session.commit()
    run = make_run(app, build_config(model.id))
    with pytest.raises(ModelUnavailableError):
        await run_flow(run, Emitter())


async def test_missing_answer_model_unavailable(app, session) -> None:
    run = make_run(app, build_config(0))
    with pytest.raises(ModelUnavailableError):
        await run_flow(run, Emitter())


# ---- 处理过程 ----


async def test_trace_records_steps(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id)
    run = make_run(app, config, history=[HistoryTurn("旧问题", "旧回答")], answer_id=7)
    emit = Emitter()
    await run_flow(run, emit)

    trace = run.trace.build()
    assert trace.app_id == 1
    assert trace.answer_id == 7
    assert trace.created_at is not None
    steps = {step.name: step for step in trace.steps}
    assert set(steps) == {StepName.intent, StepName.rewrite, StepName.retrieve}

    intent = steps[StepName.intent]
    assert intent.prompt_version == INTENT_PROMPT_VERSION
    assert intent.model_id == model.id
    assert intent.model_name == model.name
    assert intent.input_tokens is not None and intent.input_tokens > 0
    assert intent.output_tokens is not None and intent.output_tokens > 0
    assert intent.duration_ms >= 0
    assert intent.input is not None and intent.output is not None

    rewrite = steps[StepName.rewrite]
    assert rewrite.prompt_version == REWRITE_PROMPT_VERSION
    assert rewrite.model_id == model.id
    assert rewrite.input_tokens is not None and rewrite.input_tokens > 0
    assert rewrite.output_tokens is not None and rewrite.output_tokens > 0

    retrieve = steps[StepName.retrieve]
    assert retrieve.model_id is None
    assert retrieve.prompt_version is None
    # 没有引用知识库：检索返回空候选，记录各阶段为空。
    assert retrieve.output["count"] == 0
    assert retrieve.output["kbs"] == []
    assert retrieve.output["hits"] == []

    # 步骤事件按 开始 / 结束 成对发出。
    assert emit.types().count("STEP_STARTED") == 3
    assert emit.types().count("STEP_FINISHED") == 3


async def test_trace_parallel_steps_sorted_by_started_at(app, session, fake_llm) -> None:
    model = await make_chat_model(session)
    config = build_config(model.id)
    run = make_run(app, config, history=[HistoryTurn("旧问题", "旧回答")])
    await run_flow(run, Emitter())
    steps = run.trace.build().steps
    assert [step.started_at for step in steps] == sorted(step.started_at for step in steps)


def _step(run: AnswerRun, name: StepName):
    steps = [step for step in run.trace.build().steps if step.name == name]
    assert steps, f"没有步骤 {name}"
    return steps[0]
