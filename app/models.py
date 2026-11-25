"""Модели инфраструктурных таблиц (этап 0).

Игровые таблицы добавляются отдельными миграциями по этапам.
"""
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyKey(Base, TimestampMixin):
    """Идемпотентность команд: повтор с тем же ключом возвращает сохранённый ответ."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    character_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    scope: Mapped[str] = mapped_column(String(32), default="command")
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Outbox(Base, TimestampMixin):
    """Исходящие сообщения. Пишутся атомарно с эффектом команды (ТЗ: потеря/дубли недопустимы)."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigInteger, sa.Identity(), primary_key=True)
    character_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True)  # None = все привязки
    text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_outbox_pending", "status", "next_attempt_at"),)


class ConfigParam(Base, TimestampMixin):
    """Скаляры баланса/настроек, правятся из админ-UI без рестарта (ТЗ)."""

    __tablename__ = "config_params"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    hot_reloadable: Mapped[bool] = mapped_column(default=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Formula(Base, TimestampMixin):
    """Строковые формулы для безопасного AST-вычислителя."""

    __tablename__ = "formulas"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # допустимые переменные
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Timer(Base, TimestampMixin):
    """Единый планировщик: возвращение, крафт, лоты, передатки. Работают офлайн (ТЗ)."""

    __tablename__ = "timers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    character_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    fires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|fired|cancelled

    __table_args__ = (Index("ix_timers_due", "status", "fires_at"),)


class AuditLog(Base):
    """Append-only аудит действий администраторов/модераторов (ТЗ)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, sa.Identity(), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_audit_target", "target_type", "target_id"), Index("ix_audit_at", "at"))


class MaintenanceState(Base):
    """Флаг режима обслуживания (single row)."""

    __tablename__ = "maintenance_state"

    id: Mapped[int] = mapped_column(sa.SmallInteger, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(default=False)
    message: Mapped[str] = mapped_column(Text, default="Технические работы. Попробуйте позже.")

    __table_args__ = (sa.CheckConstraint("id = 1", name="ck_maintenance_single_row"),)


__all__ = [
    "AuditLog",
    "Base",
    "ConfigParam",
    "Formula",
    "IdempotencyKey",
    "MaintenanceState",
    "Outbox",
    "Timer",
]
