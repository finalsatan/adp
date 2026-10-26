"""本地假模型服务（P2-12）：OpenAI 兼容的对话与向量化接口，仅用于测试和本地联调。

不进生产镜像，也不要被任何生产代码 import。

启动（默认 9999，可用环境变量覆盖）：

    make fake-llm
    FAKE_LLM_PORT=19999 make fake-llm
    cd backend && FAKE_LLM_PORT=19999 uv run python -m tests.fake_llm.server

实现的接口：

- ``POST /v1/chat/completions``：支持 ``stream: true``，按固定文本逐字输出，
  最后一个片段带 usage；请求内容里带标记时可模拟慢速首字或上游报错。
  按系统提示词返回固定内容：意图识别返回 ``{"intent": "knowledge"}``、问题改写
  返回固定改写问题、带 ``<references>`` 的回答在末尾附上 ``[1]`` 等引用角标
  （P4-18 端到端用；其余情况返回 ``FAKE_LLM_REPLY``）。
- ``POST /v1/embeddings``：返回固定维度的向量。
- ``POST /v1/rerank``：按查询与文档的字面重合度给分，返回 ``results``（P4-18）。
- ``GET /healthz``：存活检查（给 Playwright webServer 用）。

模拟标记（写在消息文本里即可）：

- ``__SLOW_FIRST_TOKEN__``：延迟 ``FAKE_LLM_SLOW_FIRST_TOKEN`` 秒再发第一个片段。
- ``__ERROR__``：返回 500，模拟上游报错。

环境变量：

- ``FAKE_LLM_PORT``：端口，默认 9999（只在直接运行时用）。
- ``FAKE_LLM_REPLY``：固定回复文本。
- ``FAKE_LLM_CHAR_DELAY``：逐字输出的间隔秒数，默认 0.1。
- ``FAKE_LLM_SLOW_FIRST_TOKEN``：慢速首字的延迟秒数，默认 2。
- ``FAKE_LLM_EMBEDDING_DIM``：向量维度，默认 1024。
"""

import asyncio
import hashlib
import json
import os
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

SLOW_FIRST_TOKEN_MARKER = "__SLOW_FIRST_TOKEN__"
ERROR_MARKER = "__ERROR__"

# 按系统提示词识别调用类型（P4-18）：意图识别、问题改写、带参考内容的生成。
INTENT_MARKER = "意图识别助手"
REWRITE_MARKER = "问题改写助手"
REFERENCES_MARKER = "<references>"

DEFAULT_REPLY = "这是本地假模型的回答：一二三四五六七八九十，用于端到端测试的流式输出。"
DEFAULT_INTENT = '{"intent": "knowledge"}'
DEFAULT_REWRITE = "改写后的问题"
DEFAULT_CHAR_DELAY = 0.1
DEFAULT_SLOW_FIRST_TOKEN = 2.0
DEFAULT_EMBEDDING_DIM = 1024

_REFERENCE_ID_RE = re.compile(r'<reference\s+id="(\d+)"')

app = FastAPI(title="ADP 本地假模型服务")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def reply_text() -> str:
    return os.environ.get("FAKE_LLM_REPLY") or DEFAULT_REPLY


def reply_for(prompt: str, messages: list[dict[str, Any]] | None = None) -> str:
    """按系统提示词返回固定回复（P4-18 端到端需要可预测的意图 / 改写 / 角标）。"""
    if INTENT_MARKER in prompt:
        return DEFAULT_INTENT
    if REWRITE_MARKER in prompt:
        # 改写结果用当前问题本身（等同未改写），保证后续检索命中真实内容。
        return _last_user_text(messages or []) or DEFAULT_REWRITE
    if REFERENCES_MARKER in prompt:
        # 参考内容编号从 1 开始，回答里带上全部角标，供引用预览使用。
        ids = _REFERENCE_ID_RE.findall(prompt)
        cited = "".join(f"[{value}]" for value in ids)
        return f"{reply_text()} {cited}".strip()
    return reply_text()


def char_delay() -> float:
    return _env_float("FAKE_LLM_CHAR_DELAY", DEFAULT_CHAR_DELAY)


def slow_first_token() -> float:
    return _env_float("FAKE_LLM_SLOW_FIRST_TOKEN", DEFAULT_SLOW_FIRST_TOKEN)


def embedding_dim() -> int:
    return _env_int("FAKE_LLM_EMBEDDING_DIM", DEFAULT_EMBEDDING_DIM)


def _messages_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """最后一条 user 消息的内容（问题改写的当前问题）。"""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                item["text"]
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
    return ""


def _count_tokens(text: str) -> int:
    """粗略估算 token 数，够测试用即可。"""
    return max(1, len(text))


def _chat_id() -> str:
    return f"chatcmpl-fake-{int(time.time() * 1000)}"


def _chunk(
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": _chat_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _usage_chunk(model: str, prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
    return {
        "id": _chat_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_chat(model: str, prompt: str, reply: str) -> AsyncIterator[str]:
    if SLOW_FIRST_TOKEN_MARKER in prompt:
        await asyncio.sleep(slow_first_token())
    delay = char_delay()
    yield _sse(_chunk(model, {"role": "assistant", "content": ""}))
    for index, char in enumerate(reply):
        if index > 0 and delay > 0:
            await asyncio.sleep(delay)
        yield _sse(_chunk(model, {"content": char}))
    yield _sse(_chunk(model, {}, finish_reason="stop"))
    yield _sse(_usage_chunk(model, _count_tokens(prompt), _count_tokens(reply)))
    yield "data: [DONE]\n\n"


def _embedding_vector(text: str, dim: int) -> list[float]:
    """按文本生成稳定的假向量，同一文本每次结果一致。"""
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    vector: list[float] = []
    for index in range(dim):
        byte = seed[index % len(seed)]
        vector.append(round(((byte + index) % 1000) / 1000, 6))
    return vector


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = await request.json()
    model = str(body.get("model") or "fake")
    messages = body.get("messages") or []
    prompt = _messages_text(messages)
    reply = reply_for(prompt, messages)

    if ERROR_MARKER in prompt:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": "假模型模拟的上游错误",
                    "type": "server_error",
                    "code": "fake_upstream_error",
                }
            },
        )

    if body.get("stream"):
        return StreamingResponse(
            _stream_chat(model, prompt, reply),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return {
        "id": _chat_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": _count_tokens(prompt),
            "completion_tokens": _count_tokens(reply),
            "total_tokens": _count_tokens(prompt) + _count_tokens(reply),
        },
    }


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> dict[str, Any]:
    body = await request.json()
    model = str(body.get("model") or "fake")
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        texts = [raw_input]
    elif isinstance(raw_input, list):
        texts = [str(item) for item in raw_input]
    else:
        texts = []
    dim = embedding_dim()
    data = [
        {"object": "embedding", "index": index, "embedding": _embedding_vector(text, dim)}
        for index, text in enumerate(texts)
    ]
    prompt_tokens = sum(_count_tokens(text) for text in texts) or 1
    return {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }


def _rerank_score(query: str, document: str) -> float:
    """字面重合度：查询里出现在文档中的字符占比，再加一点位置权重（够端到端用）。"""
    if not query:
        return 0.0
    overlap = sum(1 for char in set(query) if char in document)
    return round(overlap / max(1, len(set(query))), 6)


@app.post("/v1/rerank")
async def rerank(request: Request) -> dict[str, Any]:
    """LiteLLM ``hosted_vllm/rerank`` 的本地实现（P4-18）：按字面重合度排序。"""
    body = await request.json()
    query = str(body.get("query") or "")
    documents = [str(item) for item in (body.get("documents") or [])]
    top_n = body.get("top_n")
    scored = sorted(
        (
            {"index": index, "relevance_score": _rerank_score(query, document)}
            for index, document in enumerate(documents)
        ),
        key=lambda item: item["relevance_score"],
        reverse=True,
    )
    if isinstance(top_n, int) and top_n > 0:
        scored = scored[:top_n]
    total_tokens = _count_tokens(query) + sum(_count_tokens(doc) for doc in documents)
    return {
        "id": f"rerank-fake-{int(time.time() * 1000)}",
        "results": scored,
        "usage": {"total_tokens": total_tokens},
    }


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=_env_int("FAKE_LLM_PORT", 9999),
    )


if __name__ == "__main__":
    main()
