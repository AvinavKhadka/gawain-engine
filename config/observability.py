"""
config/observability.py — Structured logging + startup environment checks.

Stdlib only, and a leaf module: it imports nothing from server/ or
config.settings, so it can never create an import cycle.

Two jobs:

1. **Logging** — JSON in containers, readable console on a TTY, with secret
   redaction so a connection string or password can never reach a log sink,
   a file, or a CI transcript.

2. **Preflight** — a startup check that turns silent misconfiguration into a
   specific, actionable message. Non-fatal by default: this app is built to
   serve the UI with no database and no LLM, so a bad environment should be
   loud, not terminal. `GAWAIN_STRICT_ENV=1` opts into raising.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import sys
import traceback
from typing import Any

__all__ = ["setup_logging", "get_logger", "preflight", "redact", "in_container"]

_REDACTED = "***REDACTED***"

#: Substrings marking an env var or log field as sensitive.
_SECRET_HINTS = ("password", "passwd", "secret", "token", "apikey", "api_key",
                 "credential", "private_key")

#: Secrets embedded inside connection strings and URLs.
_INLINE_SECRETS = (
    re.compile(r"(?i)\b(pwd|password|uid|user id)\s*=\s*([^;\s]+)"),
    re.compile(r"(?i)(://[^:/\s]+:)([^@/\s]+)(@)"),
)

#: DB_SERVER values shipped in .env.example — seeing one means it was never
#: edited, so no connection will ever succeed.
_PLACEHOLDERS = ("IMPOSSIBLEISNOT", "YOUR_PC", "YOURSERVER", "YOUR_SERVER",
                 "CHANGEME", "CHANGE_ME", "<SERVER>", "SERVERNAME")

#: Built-in LogRecord attributes; anything else arrived via extra=.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime", "message", "taskName", "color_message",
}


# ── Redaction ─────────────────────────────────────────────────────────────────

def redact(value: Any) -> Any:
    """Mask secrets in a string, dict or sequence.

    Deliberately over-redacts: leaking a production password into CI output is
    far worse than an unhelpfully masked log line.

    >>> redact("SERVER=s;UID=gawain;PWD=hunter2;")
    'SERVER=s;UID=***REDACTED***;PWD=***REDACTED***;'
    """
    if isinstance(value, dict):
        return {k: (_REDACTED if _is_secret(str(k)) else redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    if not isinstance(value, str):
        return value

    out = value
    for pat in _INLINE_SECRETS:
        out = pat.sub(
            lambda m: (f"{m.group(1)}{_REDACTED}{m.group(3)}" if pat.groups >= 3
                       else f"{m.group(1)}={_REDACTED}"),
            out,
        )
    return out


def _is_secret(key: str) -> bool:
    return any(h in key.lower() for h in _SECRET_HINTS)


class _Scrub(logging.Filter):
    """Redact the message, args and every extra= field before it is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                record.args = (redact(record.args) if isinstance(record.args, dict)
                               else tuple(redact(a) for a in record.args))
            for key, val in list(record.__dict__.items()):
                if key in _RESERVED or key.startswith("_"):
                    continue
                record.__dict__[key] = _REDACTED if _is_secret(key) else redact(val)
            # uvicorn attaches an ANSI-laden duplicate of every message.
            record.__dict__.pop("color_message", None)
        except Exception:
            pass          # logging must never break the caller
        return True


# ── Formatters ────────────────────────────────────────────────────────────────

def _context(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")}


class _Json(logging.Formatter):
    """One JSON object per line — for containers, CI and log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(record.created, tz=_dt.timezone.utc)
                     .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = "".join(traceback.format_exception(*record.exc_info)).strip()
        for k, v in _context(record).items():
            try:
                json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                out[k] = repr(v)
        return json.dumps(out, ensure_ascii=False, default=str)


class _Console(logging.Formatter):
    """Aligned, optionally colourised output for a human at a terminal."""

    COLORS = {"DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
              "ERROR": "\033[31m", "CRITICAL": "\033[1;41m"}

    def __init__(self, *, color: bool = True) -> None:
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        ts = _dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        if self.color:
            c = self.COLORS.get(record.levelname, "")
            head = (f"\033[2m{ts}\033[0m {c}{record.levelname:<8}\033[0m "
                    f"\033[2m{record.name}\033[0m")
        else:
            head = f"{ts} {record.levelname:<8} {record.name}"

        line = f"{head}  {record.getMessage()}"
        ctx = _context(record)
        if ctx:
            line += "  " + " ".join(f"{k}={v!r}" for k, v in ctx.items())
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return line


# ── Setup ─────────────────────────────────────────────────────────────────────

_CONFIGURED = False
_NOISY = ("httpx", "httpcore", "urllib3", "asyncio", "matplotlib", "PIL")


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def in_container() -> bool:
    """Best-effort container detection — decides log format and env checks."""
    if _flag("GAWAIN_IN_CONTAINER") or os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8", errors="ignore") as fh:
            blob = fh.read()
        return any(x in blob for x in ("docker", "kubepods", "containerd"))
    except OSError:
        return False


def setup_logging(level: str | int | None = None, *, force: bool = False):
    """Configure root logging. Idempotent.

    Env: LOG_LEVEL (INFO), LOG_FORMAT (json|console|auto), LOG_COLOR.
    ``auto`` picks JSON in a container or when stdout is not a TTY.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return logging.getLogger("gawain")

    raw = level if level is not None else os.getenv("LOG_LEVEL", "INFO")
    lvl = getattr(logging, raw.strip().upper(), logging.INFO) if isinstance(raw, str) \
        else int(raw)

    mode = os.getenv("LOG_FORMAT", "auto").strip().lower()
    is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if mode == "auto":
        mode = "json" if (in_container() or not is_tty) else "console"
    color = _flag("LOG_COLOR", default=is_tty) and os.getenv("NO_COLOR") is None

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Json() if mode == "json" else _Console(color=color))
    handler.addFilter(_Scrub())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(lvl)

    # Let uvicorn's records flow through our single handler instead of its own.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        ulog = logging.getLogger(name)
        ulog.handlers.clear()
        ulog.propagate = True
    for name in _NOISY:
        logging.getLogger(name).setLevel(max(lvl, logging.WARNING))

    _CONFIGURED = True
    return logging.getLogger("gawain")


def get_logger(name: str | None = None) -> logging.Logger:
    """Namespaced logger, configuring logging on first use."""
    if not _CONFIGURED:
        setup_logging()
    if not name or name in {"__main__", "main"}:
        return logging.getLogger("gawain")
    return logging.getLogger(name if name.startswith("gawain") else f"gawain.{name}")


# ── Preflight ─────────────────────────────────────────────────────────────────

def _odbc_drivers() -> list[str] | None:
    """Installed ODBC driver names, or None if pyodbc cannot be imported."""
    try:
        import pyodbc
        return list(pyodbc.drivers())
    except Exception:
        return None


def _checks() -> list[tuple[str, str, str]]:
    """Return [(severity, message, hint)] for everything wrong with the env.

    Encodes the failures this project actually hits, each with the fix.
    """
    out: list[tuple[str, str, str]] = []
    boxed = in_container()

    server = os.getenv("DB_SERVER", "")
    user = os.getenv("DB_USER", "").strip()
    password = os.getenv("DB_PASSWORD", "").strip()
    driver = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
    ollama = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    if not server:
        out.append(("WARN", "DB_SERVER is unset — the placeholder default will be used",
                    "cp .env.example .env, then set DB_SERVER."))
    elif any(p in server.upper() for p in _PLACEHOLDERS):
        out.append(("WARN", f"DB_SERVER is still a placeholder ({server!r})",
                    "Point it at your own SQL Server instance."))

    if boxed:
        if not (user and password):
            out.append(("ERROR",
                        "Windows/Trusted auth cannot work from inside a Linux "
                        "container, and DB_USER/DB_PASSWORD are not both set",
                        "Enable SQL Server Authentication and set both."))
        host = server.split("\\", 1)[0].split(",", 1)[0].strip().lower()
        if host in {"localhost", "127.0.0.1", "::1", "."}:
            out.append(("ERROR",
                        f"DB_SERVER={server!r} points at the container itself",
                        "Use host.docker.internal\\INSTANCE, or the compose "
                        "service name (e.g. 'mssql')."))
        if "localhost" in ollama or "127.0.0.1" in ollama:
            out.append(("WARN", f"OLLAMA_BASE_URL={ollama!r} resolves to the container",
                        "Inside docker compose use http://ollama:11434."))
    elif user and not password:
        out.append(("WARN", "DB_USER is set but DB_PASSWORD is empty",
                    "Set DB_PASSWORD, or clear DB_USER to use Windows Auth."))

    drivers = _odbc_drivers()
    if drivers is None:
        out.append(("WARN", "pyodbc could not be imported — ODBC unverified",
                    "Windows: pip install pyodbc. Linux: also apt install unixodbc."))
    elif not drivers:
        out.append(("ERROR", "No ODBC drivers installed — every connection will fail",
                    "Install msodbcsql18 (or 17)."))
    elif driver not in drivers:
        out.append(("ERROR", f"DB_DRIVER={driver!r} is not installed. Have: {drivers}",
                    "Set DB_DRIVER to one of those, spelled exactly."))

    for name, kind in (("LLM_TEMPERATURE", float), ("LLM_TIMEOUT_SQL", float),
                       ("LLM_TIMEOUT_STREAM", float), ("LLM_NUM_PREDICT", int),
                       ("LLM_ANALYSIS_TOKENS", int), ("MAX_DIM_CARDINALITY", int),
                       ("EXTRACT_ROW_LIMIT", int)):
        raw = os.getenv(name)
        if raw and raw.strip():
            try:
                kind(raw)
            except ValueError:
                out.append(("ERROR", f"{name}={raw!r} is not a valid {kind.__name__}",
                            f"Set a {kind.__name__}, or remove it for the default."))

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    storage = os.path.join(root, "storage")
    if os.path.isdir(storage) and not os.access(storage, os.W_OK):
        out.append(("ERROR", f"storage/ is not writable: {storage}",
                    "History and the analytics extract will fail."))
    if not os.path.isfile(os.path.join(root, "static", "index.html")):
        out.append(("WARN", "static/index.html is missing — the UI will 404",
                    "Run: cd frontend && npm ci && npm run build"))

    return out


def preflight(*, strict: bool | None = None) -> list[tuple[str, str, str]]:
    """Log environment problems. Raises only in strict mode."""
    log = get_logger("config")
    issues = _checks()
    errors = [i for i in issues if i[0] == "ERROR"]

    log.info("environment preflight: %s (%d error(s), %d warning(s))",
             "OK" if not errors else "PROBLEMS FOUND",
             len(errors), len(issues) - len(errors),
             extra={"in_container": in_container(),
                    "db_server": os.getenv("DB_SERVER", "<unset>"),
                    "db_driver": os.getenv("DB_DRIVER", "<default>")})

    for severity, message, hint in issues:
        log.log(logging.ERROR if severity == "ERROR" else logging.WARNING,
                "%s", message, extra={"hint": hint})

    if strict is None:
        strict = _flag("GAWAIN_STRICT_ENV")
    if strict and errors:
        raise RuntimeError(
            f"environment validation failed with {len(errors)} error(s) "
            "(GAWAIN_STRICT_ENV is enabled)")
    return issues


if __name__ == "__main__":
    setup_logging(force=True)
    preflight()
