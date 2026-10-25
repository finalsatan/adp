"""数据库：异步引擎、会话工厂、声明式基类。"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(url: str) -> AsyncEngine:
    """建异步引擎。

    关闭 asyncpg 预编译语句缓存（`prepared_statement_cache_size=0`）：ParadeDB 的类型修饰
    （`pdb.jieba` 等）把切词选项存在 `paradedb._typmod_cache` 表里，而该表的写入随事务
    回滚（`build_query_terms` 的会话不提交，第一次解析插入的行会回滚）；异步驱动缓存的
    预编译语句把类型修饰 id 固化在计划里，下次执行查不到对应行就报
    “stored tokenizer options could not be found”，且缓存一直存在、重启进程才恢复
    （知识库技术方案 11.4；SQLAlchemy asyncpg 的 DDL 缓存告警）。关掉缓存后每条语句重新
    解析，解析时会重新写入类型修饰行，问题消失。代价是每条语句多一次 Parse/Describe；
    平台当前规模可接受，见 P3-13 完成记录“缺陷修复”。
    """
    return create_async_engine(
        url,
        pool_pre_ping=True,
        connect_args={"prepared_statement_cache_size": 0},
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个会话。"""
    async with request.app.state.sessionmaker() as session:
        yield session
