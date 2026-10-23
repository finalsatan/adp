"""模型调用层：用量的独立会话写入（P2-05）。"""

import litellm
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway import service
from app.gateway.tables import ModelUsage
from tests.gateway.test_gateway_chat import MESSAGES, META, make_model, text_chunk, usage_chunk


async def test_usage_survives_caller_transaction_rollback(
    app, session: AsyncSession, monkeypatch
) -> None:
    """调用方的数据库事务回滚后，用量记录仍在（独立会话）。"""
    model = await make_model(session)

    async def fake_acompletion(**kwargs):
        return _FakeStream([text_chunk("hi"), usage_chunk(2, 1)])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    caller = app.state.sessionmaker()
    try:
        # 在调用方事务里做一个待回滚的写操作。
        await caller.execute(sa.text("SELECT 1"))
        chunks = [
            c
            async for c in service.chat_stream(
                app.state.sessionmaker, app.state.redis, META, model.id, MESSAGES, {}
            )
        ]
        assert any(c.text for c in chunks)
        await caller.rollback()
    finally:
        await caller.close()

    rows = list((await session.execute(sa.select(ModelUsage))).scalars().all())
    assert len(rows) == 1
    assert rows[0].ok is True


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        raise StopAsyncIteration

    async def aclose(self):
        return None
