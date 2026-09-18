"""Per-app structured logging with rotation, redaction and compatibility helpers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from core.time_utils import now_business, utc_iso
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import sys
import threading
import uuid

from googleform_autosubmit.config import Config


_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_job_id: ContextVar[int | str | None] = ContextVar("job_id", default=None)
_job_type: ContextVar[str | None] = ContextVar("job_type", default=None)
_component: ContextVar[str | None] = ContextVar("component", default=None)
_active_manager: ContextVar[LogManager | None] = ContextVar("log_manager", default=None)
_default_manager: LogManager | None = None
_default_lock = threading.Lock()

_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "image_data",
    "request_payload",
}
_PAIR_RE = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|authorization|cookie)\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def _secret_values() -> tuple[str, ...]:
    values = []
    for key, value in os.environ.items():
        folded = key.lower()
        if value and len(value) >= 6 and any(
            marker in folded
            for marker in ("password", "secret", "token", "api_key", "apikey")
        ):
            values.append(value)
    return tuple(sorted(set(values), key=len, reverse=True))


_KNOWN_SECRETS = _secret_values()


def redact_text(value) -> str:
    text = str(value)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _PAIR_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    for secret in _KNOWN_SECRETS:
        text = text.replace(secret, "[REDACTED]")
    return text


def _safe_value(key: str, value):
    folded = key.lower()
    if folded in _SENSITIVE_KEYS or any(marker in folded for marker in _SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _safe_value(str(k), item) for k, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(key, item) for item in value]
    if isinstance(value, str):
        return redact_text(value)[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value)[:2000]


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = getattr(record, "structured", None) or {
            "timestamp": utc_iso(timespec="milliseconds"),
            "level": record.levelname,
            "message": redact_text(record.getMessage()),
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


class LogManager:
    def __init__(
        self,
        *,
        log_dir: str,
        level: str = "INFO",
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 5,
        memory_lines: int = 500,
    ):
        self.log_dir = os.path.abspath(log_dir)
        self.memory_lines = max(100, int(memory_lines))
        self.system_logs: list[str] = []
        self.structured_logs: list[dict] = []
        self._sequence = 0
        self._lock = threading.Lock()
        self._closed = False
        os.makedirs(self.log_dir, exist_ok=True)
        self._logger = logging.getLogger(f"zalo_automation.{uuid.uuid4().hex}")
        self._logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
        self._logger.propagate = False
        handler = RotatingFileHandler(
            os.path.join(self.log_dir, "app.jsonl"),
            maxBytes=max(64 * 1024, int(max_bytes)),
            backupCount=max(1, int(backup_count)),
            encoding="utf-8",
        )
        handler.setFormatter(_JsonFormatter())
        self._logger.addHandler(handler)

    def emit(self, text: str, level: str, event: str, fields: dict) -> dict:
        if self._closed:
            raise RuntimeError("Log manager đã đóng.")
        level = str(level).upper()
        message = redact_text(text)
        timestamp = utc_iso(timespec="milliseconds")
        local_time = now_business().strftime("%H:%M:%S")
        context = {
            "request_id": _request_id.get(),
            "job_id": _job_id.get(),
            "job_type": _job_type.get(),
            "component": _component.get(),
        }
        with self._lock:
            self._sequence += 1
            record = {
                "seq": self._sequence,
                "timestamp": timestamp,
                "level": level,
                "event": event,
                "message": message,
                **{key: value for key, value in context.items() if value is not None},
                **{
                    str(key): _safe_value(str(key), value)
                    for key, value in fields.items()
                },
            }
            formatted = f"[{local_time}] {message}"
            self.system_logs.append(formatted)
            self.structured_logs.append(record)
            if len(self.system_logs) > self.memory_lines:
                del self.system_logs[: len(self.system_logs) - self.memory_lines]
            if len(self.structured_logs) > self.memory_lines:
                del self.structured_logs[: len(self.structured_logs) - self.memory_lines]
        self._logger.log(
            getattr(logging, level, logging.INFO),
            message,
            extra={"structured": record},
        )
        try:
            print(formatted)
        except (UnicodeEncodeError, OSError):
            try:
                sys.stdout.buffer.write((formatted + "\n").encode("utf-8", errors="replace"))
                sys.stdout.buffer.flush()
            except Exception:
                pass
        return record

    def recent(self, *, after=0, limit=200, level=None, job_id=None) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        wanted_level = str(level).upper() if level else None
        with self._lock:
            return [
                dict(item)
                for item in self.structured_logs
                if item["seq"] > max(0, int(after))
                and (wanted_level is None or item["level"] == wanted_level)
                and (job_id is None or item.get("job_id") == job_id)
            ][:limit]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handler in list(self._logger.handlers):
            handler.flush()
            handler.close()
            self._logger.removeHandler(handler)


def get_default_log_manager() -> LogManager:
    global _default_manager
    if _default_manager is None or _default_manager._closed:
        with _default_lock:
            if _default_manager is None or _default_manager._closed:
                _default_manager = LogManager(
                    log_dir=Config.LOG_DIR,
                    level=Config.LOG_LEVEL,
                    max_bytes=Config.LOG_MAX_BYTES,
                    backup_count=Config.LOG_BACKUP_COUNT,
                    memory_lines=Config.LOG_MEMORY_LINES,
                )
    return _default_manager


def current_log_manager() -> LogManager:
    return _active_manager.get() or get_default_log_manager()


@contextmanager
def use_log_manager(manager: LogManager):
    token = _active_manager.set(manager)
    try:
        yield
    finally:
        _active_manager.reset(token)


@contextmanager
def log_context(
    *,
    request_id: str | None = None,
    job_id: int | str | None = None,
    job_type: str | None = None,
    component: str | None = None,
):
    request_token = _request_id.set(request_id) if request_id is not None else None
    job_token = _job_id.set(job_id) if job_id is not None else None
    job_type_token = _job_type.set(job_type) if job_type is not None else None
    component_token = _component.set(component) if component is not None else None
    try:
        yield
    finally:
        if component_token is not None:
            _component.reset(component_token)
        if job_type_token is not None:
            _job_type.reset(job_type_token)
        if job_token is not None:
            _job_id.reset(job_token)
        if request_token is not None:
            _request_id.reset(request_token)


def log_message(text: str, level: str = "INFO", event: str = "message", **fields):
    return current_log_manager().emit(text, level, event, fields)


def get_recent_logs(*, after: int = 0, limit: int = 200, level=None, job_id=None):
    return current_log_manager().recent(
        after=after,
        limit=limit,
        level=level,
        job_id=job_id,
    )


def shutdown_logging(manager: LogManager | None = None):
    (manager or current_log_manager()).close()


class _SystemLogsProxy:
    def __iter__(self):
        return iter(current_log_manager().system_logs)

    def __len__(self):
        return len(current_log_manager().system_logs)

    def __getitem__(self, item):
        return current_log_manager().system_logs[item]


system_logs = _SystemLogsProxy()


class GoogleFormLogger:
    @staticmethod
    def info(msg: str):
        log_message(msg, level="INFO")

    @staticmethod
    def error(msg: str):
        log_message(msg, level="ERROR")

    @staticmethod
    def warning(msg: str):
        log_message(msg, level="WARNING")


logger = GoogleFormLogger()
