"""SQLite 引擎与会话。单进程 MVP：WAL 模式 + busy_timeout。"""

from __future__ import annotations

import logging
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.ensure_data_dir()
        _engine = create_engine(
            settings.db_url,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def SessionLocal() -> Session:
    """每次调用返回一个新会话（with SessionLocal() as s: ...）。"""
    return get_session_factory()()


def init_db() -> None:
    """建表（幂等）。MVP 用 create_all；生产化时换 Alembic 迁移。"""
    from app.persistence.models import Base

    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("database ready at %s", get_settings().db_path)


def reset_engine() -> None:
    """测试辅助：切换 DATA_DIR 后重置引擎缓存。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
