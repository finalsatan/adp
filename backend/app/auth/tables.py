"""认证相关表：刷新令牌。"""

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("users.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    token_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    expires_at: Mapped[sa.DateTime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    ip: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.Index("uq_refresh_tokens_token_hash", "token_hash", unique=True),
        sa.Index("ix_refresh_tokens_session_id", "session_id"),
        sa.Index("ix_refresh_tokens_user_id", "user_id"),
    )
