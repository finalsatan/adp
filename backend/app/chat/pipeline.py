"""知识问答的固定回答流程编排（应用技术方案 6、9.1、Q1）。

流程自己编排：每一步是一个异步函数，由 `run_flow` 按固定顺序调用；意图识别与问题
改写并行（`asyncio.TaskGroup`），每步开始 / 结束时记处理过程并发 STEP_STARTED /
STEP_FINISHED。本卡只搭骨架：

- 检索、提示词组装、生成三步是占位，分别由 P4-07、P4-08、P4-09 替换；
- 不写接口、不落库、不发 SSE，`emit` 回调把事件交给调用方（P4-09 接 SSE，测试收集）。

降级照应用技术方案 6.5：意图识别 / 改写失败按知识问题、原问题继续；模型不可用
在提问开始时统一预检；检索失败（P4-07 抛）与回答模型报错向上抛给 P4-09 处理。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

import json_repair
from redis.asyncio import Redis
from sqlalchemy import select

from app.apps.config import KbQaAppConfig
from app.chat import prompts
from app.chat.events import (
    ChatSseEvent,
    StepFinishedEvent,
    StepStartedEvent,
)
from app.chat.prompts import HistoryTurn
from app.chat.schemas import AnswerKind, AnswerStatus, CitationOut, StepName, TraceStepOut
from app.chat.traces import TraceRecorder
from app.core.errors import AppError, ModelUnavailableError
from app.gateway import service as gateway
from app.gateway.errors import ModelCallError
from app.gateway.events import TextDeltaEvent
from app.gateway.service import CallMeta, SessionMaker
from app.kb.tables import KnowledgeBase
from app.models.tables import Model

logger = logging.getLogger(__name__)

# 提示词写在代码里并带版本号；处理过程记下版本，本阶段不开放修改（6.2）。
INTENT_PROMPT_VERSION = "intent-v1"
REWRITE_PROMPT_VERSION = "rewrite-v1"

# 意图识别：温度 0、最大输出 32 token、关闭深度思考（6.2）。
INTENT_MAX_TOKENS = 32

INTENT_SYSTEM_PROMPT = (
    "你是意图识别助手。判断用户当前的问题属于“知识问题”还是“闲聊问候”。\n"
    "只输出一个 JSON，不要输出任何其他文字：\n"
    '{"intent": "knowledge"} 或 {"intent": "chitchat"}\n'
    "涉及事实、资料、业务、操作的问题属于 knowledge；打招呼、寒暄等与知识无关的闲聊"
    "属于 chitchat。拿不准时输出 knowledge。"
)

REWRITE_SYSTEM_PROMPT = (
    "你是问题改写助手。结合对话历史，把用户当前的问题改写成一个完整、独立、适合检索"
    "的问题。只输出改写后的问题，不要输出其他文字，不要回答问题，不要解释。"
)

# 闲聊时在搭建者系统提示词后追加的固定要求（6.4）。
CHITCHAT_INSTRUCTION = (
    "当前用户是在闲聊。请简短、友好地回应，并引导用户提出与资料相关的问题；不要回答事实性问题。"
)

# 生成步骤的占位回答；P4-09 替换为 `gateway.chat_stream` 流式输出。
GENERATE_PLACEHOLDER = "（回答流程占位回答，由 P4-09 替换为流式生成）"

EmitFn = Callable[[ChatSseEvent], Awaitable[None]]


@dataclass
class AnswerRun:
    """一次回答的运行上下文（6.1）。

    `sessionmaker` / `redis` 是模型调用的运行时依赖（gateway.chat 需要）；`models`
    由流程开始时的模型可用性预检填入，供处理过程记录模型名。其余字段是各步骤结果，
    P4-09 在流程结束后读取 `kind` / `citations` / `content` / `suggestions` / `error`
    与用量，落库到 `chat_answers` / `answer_traces`。
    """

    app_id: int
    config: KbQaAppConfig
    question: str
    sessionmaker: SessionMaker
    redis: Redis
    # 会话与轮次：P4-09 传入；调试预览（P4-11）没有持久化 ID。
    conversation_id: int | None = None
    turn_id: int | None = None
    answer_id: int = 0
    version_id: int | None = None
    user_id: int | None = None
    org_id: int | None = None
    # 模型调用来源：终端用户 `app`，调试预览 `app_debug`（14）。
    source: str = "app"
    # 最近 N 轮历史，由调用方按 `memory_turns` 截取（P4-08 提供裁剪函数）。
    history: list[HistoryTurn] = field(default_factory=list)
    trace: TraceRecorder | None = None
    models: dict[int, Model] = field(default_factory=dict)

    # ---- 各步骤结果 ----
    intent: str | None = None
    rewritten_question: str | None = None
    rewrite_ok: bool = False
    query_text: str | None = None
    candidates: list[Any] = field(default_factory=list)
    references: list[Any] = field(default_factory=list)
    prompt_messages: list[dict[str, Any]] = field(default_factory=list)
    prompt_version: str | None = None
    prompt_assembly: prompts.AssembledPrompt | None = None
    content: str = ""
    reasoning: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    first_token_ms: int | None = None
    total_ms: int | None = None
    citations: list[CitationOut] = field(default_factory=list)
    cited: list[int] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    kind: AnswerKind = AnswerKind.generated
    status: AnswerStatus = AnswerStatus.generating
    error: Exception | None = None

    def __post_init__(self) -> None:
        if self.trace is None:
            self.trace = TraceRecorder(app_id=self.app_id, answer_id=self.answer_id)
        if self.query_text is None:
            self.query_text = self.question


def _answer_model_id(config: KbQaAppConfig) -> int | None:
    return config.model.chat_model_id if config.model is not None else None


def _meta(run: AnswerRun) -> CallMeta:
    return CallMeta(
        source=run.source,
        source_id=run.app_id,
        user_id=run.user_id,
        org_id=run.org_id,
    )


def _error_code(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.code
    if isinstance(exc, ModelCallError):
        return exc.error_code
    return type(exc).__name__


# ---- 模型可用性预检（6.5）----


async def load_available_models(
    sessionmaker: SessionMaker, config: KbQaAppConfig
) -> dict[int, Model]:
    """预检本次提问会用到的模型，返回 `{model_id: Model}`。

    检查回答、意图识别、改写、重排（文档库与问答库）模型与所引用知识库的向量化模型，
    存在、未删除且 `status='enabled'`；任一不可用直接抛 `MODEL_UNAVAILABLE`（6.5）。
    只读模型，不实际调用。意图识别 / 改写 / 重排关闭时不检查对应模型。
    """
    answer_id = _answer_model_id(config)
    if answer_id is None:
        raise ModelUnavailableError()
    wanted: set[int] = {answer_id}
    if config.intent.enabled:
        wanted.add(config.intent.model_id or answer_id)
    if config.rewrite.enabled:
        wanted.add(config.rewrite.model_id or answer_id)
    if config.knowledge.doc.rerank.enabled and config.knowledge.doc.rerank.model_id:
        wanted.add(config.knowledge.doc.rerank.model_id)
    if config.knowledge.qa.rerank.enabled and config.knowledge.qa.rerank.model_id:
        wanted.add(config.knowledge.qa.rerank.model_id)

    kb_ids = [ref.kb_id for ref in config.knowledge.kbs]
    async with sessionmaker() as session:
        if kb_ids:
            embedding_ids = (
                (
                    await session.execute(
                        select(KnowledgeBase.embedding_model_id).where(KnowledgeBase.id.in_(kb_ids))
                    )
                )
                .scalars()
                .all()
            )
            wanted.update(embedding_ids)
        rows = (await session.execute(select(Model).where(Model.id.in_(wanted)))).scalars().all()

    available = {model.id: model for model in rows if _model_enabled(model)}
    if wanted - available.keys():
        raise ModelUnavailableError()
    return available


def _model_enabled(model: Model) -> bool:
    return model.deleted_at is None and model.status == "enabled"


# ---- 步骤事件与处理过程 ----


async def begin_step(run: AnswerRun, emit: EmitFn, name: StepName) -> None:
    """记录一步开始并发 STEP_STARTED。"""
    run.trace.start_step(name)
    await emit(StepStartedEvent(step=name))


async def end_step(run: AnswerRun, emit: EmitFn, name: StepName, **kwargs: Any) -> TraceStepOut:
    """记录一步结束并发 STEP_FINISHED，返回这一步的结构。"""
    step = run.trace.finish_step(name, **kwargs)
    await emit(StepFinishedEvent(step=name, duration_ms=step.duration_ms))
    return step


# ---- 意图识别与问题改写（6.2）----


def _memory_messages(
    run: AnswerRun, system_prompt: str, history: list[HistoryTurn] | None = None
) -> list[dict[str, Any]]:
    """系统提示词 + 最近 N 轮历史 + 当前问题。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in run.history if history is None else history:
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": turn.answer})
    messages.append({"role": "user", "content": run.question})
    return messages


def parse_intent(text: str) -> str:
    """解析意图识别的返回；解析失败或值不合法一律按知识问题（6.2）。"""
    try:
        data = json_repair.loads(text)
    except Exception:  # noqa: BLE001 - 模型返回什么都要能兜住
        return "knowledge"
    if isinstance(data, dict) and data.get("intent") in ("knowledge", "chitchat"):
        return str(data["intent"])
    return "knowledge"


def clean_rewrite(text: str, question: str, history: list[HistoryTurn]) -> tuple[str, bool]:
    """校验改写结果；为空或明显过长时用原问题（6.2），返回（问题, 是否采用改写）。"""
    rewritten = (text or "").strip()
    if not rewritten:
        return question, False
    limit = len(question) + sum(len(turn.question) + len(turn.answer) for turn in history)
    if len(rewritten) > limit:
        return question, False
    return rewritten, True


async def _run_intent(run: AnswerRun, emit: EmitFn) -> None:
    model_id = run.config.intent.model_id or _answer_model_id(run.config)
    model = run.models.get(model_id)
    await begin_step(run, emit, StepName.intent)
    history = prompts.trim_history_for_model(
        run.history,
        model=model,
        system_prompt=INTENT_SYSTEM_PROMPT,
        question=run.question,
        max_output=INTENT_MAX_TOKENS,
    )
    messages = _memory_messages(run, INTENT_SYSTEM_PROMPT, history)
    try:
        result = await gateway.chat(
            run.sessionmaker,
            run.redis,
            _meta(run),
            model_id,
            messages,
            {"temperature": 0, "max_tokens": INTENT_MAX_TOKENS, "thinking": False},
        )
    except Exception as exc:  # noqa: BLE001 - 失败 / 限流按知识问题继续
        logger.info("意图识别降级: %s", _error_code(exc))
        run.intent = "knowledge"
        await end_step(
            run,
            emit,
            StepName.intent,
            input=messages,
            output={"intent": "knowledge", "degraded": _error_code(exc)},
            model=model,
            prompt_version=INTENT_PROMPT_VERSION,
        )
        return
    run.intent = parse_intent(result.text)
    await end_step(
        run,
        emit,
        StepName.intent,
        input=messages,
        output={"intent": run.intent, "raw": result.text},
        model=model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        prompt_version=INTENT_PROMPT_VERSION,
    )


async def _run_rewrite(run: AnswerRun, emit: EmitFn) -> None:
    model_id = run.config.rewrite.model_id or _answer_model_id(run.config)
    model = run.models.get(model_id)
    await begin_step(run, emit, StepName.rewrite)
    # 会话第一问不调用模型，直接用原问题（6.2）。
    if not run.history:
        run.rewritten_question = run.question
        run.rewrite_ok = False
        await end_step(
            run,
            emit,
            StepName.rewrite,
            input=run.question,
            output={"rewritten": run.question, "skipped": "first_question"},
            prompt_version=REWRITE_PROMPT_VERSION,
        )
        return
    messages = _memory_messages(
        run,
        REWRITE_SYSTEM_PROMPT,
        prompts.trim_history_for_model(
            run.history,
            model=model,
            system_prompt=REWRITE_SYSTEM_PROMPT,
            question=run.question,
        ),
    )
    try:
        result = await gateway.chat(
            run.sessionmaker,
            run.redis,
            _meta(run),
            model_id,
            messages,
            {"temperature": 0, "thinking": False},
        )
    except Exception as exc:  # noqa: BLE001 - 失败 / 限流用原问题继续
        logger.info("问题改写降级: %s", _error_code(exc))
        run.rewritten_question = run.question
        run.rewrite_ok = False
        await end_step(
            run,
            emit,
            StepName.rewrite,
            input=messages,
            output={"rewritten": run.question, "degraded": _error_code(exc)},
            model=model,
            prompt_version=REWRITE_PROMPT_VERSION,
        )
        return
    rewritten, ok = clean_rewrite(result.text, run.question, run.history)
    run.rewritten_question = rewritten
    run.rewrite_ok = ok
    await end_step(
        run,
        emit,
        StepName.rewrite,
        input=messages,
        output={"rewritten": rewritten, "raw": result.text, "adopted": ok},
        model=model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        prompt_version=REWRITE_PROMPT_VERSION,
    )


async def _run_intent_and_rewrite(run: AnswerRun, emit: EmitFn) -> None:
    """两步都开启时并行执行；关闭时不调用模型，直接取默认值。"""
    intent_enabled = run.config.intent.enabled
    rewrite_enabled = run.config.rewrite.enabled
    if not intent_enabled:
        run.intent = "knowledge"
    if not rewrite_enabled:
        run.rewritten_question = run.question
        run.rewrite_ok = False
    async with asyncio.TaskGroup() as group:
        if intent_enabled:
            group.create_task(_run_intent(run, emit))
        if rewrite_enabled:
            group.create_task(_run_rewrite(run, emit))


def _query_text(run: AnswerRun) -> str:
    """检索用改写后的问题（意图识别、改写都开启且改写成功时），否则用原问题（6.3）。"""
    if run.config.intent.enabled and run.config.rewrite.enabled and run.rewrite_ok:
        return run.rewritten_question or run.question
    return run.question


# ---- 检索、提示词组装、生成 ----


async def _retrieve(run: AnswerRun, emit: EmitFn) -> list[Any]:
    """真正的多知识库检索（P4-07）：文档库召回并融合、合并重排，返回候选列表。"""
    from app.chat import retrieval

    outcome = await retrieval.retrieve_documents(run, emit)
    return outcome.candidates


async def _assemble_prompt(run: AnswerRun) -> list[dict[str, Any]]:
    """提示词组装、防注入与上下文预算（P4-08）：参考内容编号、历史裁剪、token 估算。"""
    model = run.models.get(_answer_model_id(run.config))
    max_tokens = run.config.model.max_tokens if run.config.model is not None else None
    assembly = prompts.assemble(
        system_prompt=run.config.system_prompt,
        question=run.question,
        history=run.history,
        candidates=run.candidates,
        model=model,
        max_tokens=max_tokens,
    )
    run.prompt_messages = assembly.messages
    run.prompt_version = assembly.prompt_version
    run.prompt_assembly = assembly
    run.references = list(assembly.references)
    return assembly.messages


async def _generate(run: AnswerRun, emit: EmitFn) -> None:
    """占位：非流式返回一段占位文字；由 P4-09 替换为 `gateway.chat_stream` 流式输出。"""
    await begin_step(run, emit, StepName.generate)
    run.kind = AnswerKind.generated
    run.content = GENERATE_PLACEHOLDER
    await emit(TextDeltaEvent(delta=run.content))
    assembly = run.prompt_assembly
    await end_step(
        run,
        emit,
        StepName.generate,
        input=run.prompt_messages,
        output={
            "content": run.content,
            "history_turns": assembly.history_turns if assembly else 0,
            "dropped_references": [ref.n for ref in assembly.dropped_references]
            if assembly
            else [],
        },
        prompt_version=run.prompt_version,
    )


# ---- 闲聊与兜底（6.4）----


async def _chitchat(run: AnswerRun, emit: EmitFn) -> None:
    """闲聊：回答模型 + 搭建者系统提示词 + 固定闲聊要求，带历史、不带参考内容。"""
    model_id = _answer_model_id(run.config)
    model = run.models.get(model_id)
    system_prompt = run.config.system_prompt or ""
    system_prompt = (
        f"{system_prompt}\n{CHITCHAT_INSTRUCTION}" if system_prompt else CHITCHAT_INSTRUCTION
    )
    messages = _memory_messages(run, system_prompt)
    params: dict[str, Any] = {"temperature": run.config.model.temperature, "thinking": False}
    if run.config.model.max_tokens is not None:
        params["max_tokens"] = run.config.model.max_tokens
    await begin_step(run, emit, StepName.generate)
    try:
        result = await gateway.chat(
            run.sessionmaker, run.redis, _meta(run), model_id, messages, params
        )
    except Exception as exc:  # noqa: BLE001 - 记下失败步骤后交给 P4-09 发 RUN_ERROR
        await end_step(
            run,
            emit,
            StepName.generate,
            input=messages,
            output={"degraded": _error_code(exc)},
            model=model,
        )
        raise
    run.kind = AnswerKind.chitchat
    run.content = result.text
    run.input_tokens = result.input_tokens
    run.output_tokens = result.output_tokens
    run.first_token_ms = result.first_token_ms
    await emit(TextDeltaEvent(delta=result.text))
    await end_step(
        run,
        emit,
        StepName.generate,
        input=messages,
        output=result.text,
        model=model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


async def _fallback(run: AnswerRun, emit: EmitFn) -> None:
    """兜底：检索结果为空时用一个 TEXT_DELTA 发出兜底话术，不调用模型。"""
    run.kind = AnswerKind.fallback
    run.content = run.config.reply.fallback_text
    await emit(TextDeltaEvent(delta=run.content))


# ---- 流程入口 ----


async def run_flow(run: AnswerRun, emit: EmitFn) -> None:
    """按固定顺序执行一次回答流程（6.1）。

    模型不可用、检索失败、回答模型报错等向上抛给调用方（P4-09）；抛之前把异常写进
    `run.error` 并把状态置为 `failed`，供落库使用。
    """
    started = monotonic()
    try:
        run.models = await load_available_models(run.sessionmaker, run.config)
        await _run_intent_and_rewrite(run, emit)
        run.query_text = _query_text(run)
        if run.intent == "chitchat":
            # 判为闲聊时丢弃改写结果（6.2）。
            run.rewritten_question = None
            run.rewrite_ok = False
            await _chitchat(run, emit)
        else:
            run.candidates = await _retrieve(run, emit)
            run.references = run.candidates
            if not run.references:
                await _fallback(run, emit)
            else:
                run.prompt_messages = await _assemble_prompt(run)
                await _generate(run, emit)
        run.status = AnswerStatus.done
    except (AppError, ModelCallError) as exc:
        run.error = exc
        run.status = AnswerStatus.failed
        raise
    finally:
        run.total_ms = max(0, int(round((monotonic() - started) * 1000)))
