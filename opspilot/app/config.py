"""统一配置中心：所有配置项从环境变量 / 仓库根目录 .env 读取。

设计原则（见架构文档 §14）：
- 所有模块经 Settings 读取配置，不在代码中散落字符串；
- 令牌留空时自动生成随机值写入数据目录，保证零配置开箱即用且安全；
- AI 模型三项（URL/Key/Model）留空时系统降级为内置规则诊断模式。
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录：app/config.py 的上一级
REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPSPILOT_",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 服务 ----------
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = REPO_ROOT / "data"

    # ---------- 令牌（留空则自动生成） ----------
    webhook_token: str = ""
    admin_token: str = ""

    # ---------- AI 模型（OpenAI 兼容；留空 = 规则诊断模式） ----------
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_s: int = 120

    # ---------- 监控数据源 ----------
    monitoring_source: str = "mock"  # mock | zabbix
    zabbix_url: str = ""
    zabbix_token: str = ""
    queue_backlog_threshold: int = 100

    # ---------- 运行环境 ----------
    run_environment: str = "lab"  # lab 才允许白名单写动作

    # ---------- 通知 ----------
    notification_mode: str = "file"  # file | webhook | dingtalk
    notification_webhook_url: str = ""
    dingtalk_webhook_url: str = ""

    # ---------- 后台工作器 ----------
    worker_enabled: bool = True
    worker_interval_s: float = 2.0

    # ---------- 显示时区 ----------
    display_tz: str = "Asia/Shanghai"

    # ---------- 派生属性 ----------

    @property
    def db_path(self) -> Path:
        return self.data_dir / "opspilot.db"

    @property
    def db_url(self) -> str:
        # SQLite URL 统一用正斜杠，避免 Windows 反斜杠被当作转义符
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def llm_configured(self) -> bool:
        """AI 模型三项都填写才算已配置。"""
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    def resolve_token(self, inline: str, filename: str) -> str:
        """令牌解析：环境变量优先；留空则读取数据目录中的令牌文件，
        不存在则生成随机令牌并保存。保证零配置时也是安全的随机令牌。"""
        if inline:
            return inline
        self.ensure_data_dir()
        token_file = self.data_dir / filename
        if token_file.exists():
            value = token_file.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = secrets.token_urlsafe(32)
        token_file.write_text(value, encoding="utf-8")
        return value

    def webhook_token_resolved(self) -> str:
        return self.resolve_token(self.webhook_token, "webhook_token")

    def admin_token_resolved(self) -> str:
        return self.resolve_token(self.admin_token, "admin_token")


@lru_cache
def get_settings() -> Settings:
    return Settings()
