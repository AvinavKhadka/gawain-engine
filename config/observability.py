"""
config/observability.py — Structured logging + environment validation.

A single, dependency-light module (stdlib only) that gives the engine two things
it previously lacked:

  1. **Structured logging** — JSON in containers, human-readable colour on a TTY,
     with automatic redaction of secrets so DB_PASSWORD can never reach a log
     sink, a log file, or a CI transcript.

  2. **Environment validation** — a startup preflight that turns silent
     misconfiguration into a loud, specific, actionable report. It encodes the
     footguns that actually bite this project: Windows Auth from inside a Linux
     container, `localhost` instead of `host.docker.internal`, an ODBC driver
     name that isn't installed, ODBC 18's encrypt-by-default certificate error.

Design constraints (deliberate):
  * **Zero imports from `server/` or `config.settings`.** This module is a leaf.
    It reads `os.environ` directly, so importing it can never create a cycle and
    it can be unit-tested with nothing else present.
  * **Never fatal by default.** Gawain's whole architecture is graceful
    degradation — the UI must serve with no database and no LLM. Validation
    therefore *reports*; it only raises when `GAWAIN_STRICT_ENV=1`.
  * **Import-safe.** Nothing here performs I/O, opens sockets, or touches the
    network at import time.

Usage:
    from config.observability import setup_logging, get_logger, preflight

    setup_logging()
    preflight()                      # logs the report, returns it
    log = get_logger(__name__)

Self-test:
    python -m config.observability
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

__all__ = [
    "setup_logging",
    "get_logger",
    "validate_environment",
    "preflight",
    "redact",
    "Severity",
    "Issue",
    "ValidationReport",
    "in_container",
]

# ──────────────────────────────────────────────────────────────────────────────
# Secret redaction
# ──────────────────────────────────────────────────────────────────────────────

#: Environment variable names whose *values* must never be logged verbatim.
SECRET_ENV_KEYS: frozenset[str] = frozenset({
    "DB_PASSWORD",
    "SA_PASSWORD",
    "MSSQL_SA_PASSWORD",
    "SSAS_CONNECTION_STRING",
    "GITHUB_TOKEN",
    "GHCR_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
})

#: Substrings that mark a key as sensitive even if not listed above.
_SECRET_HINTS = ("password", "passwd", "secret", "token", "apikey", "api_key",
                 "credential", "private_key")

_REDACTED = "***REDACTED***"

#: Placeholder DB_SERVER values shipped in .env.example / settings.py defaults.
#: Seeing one of these means the user copied the example and never edited it.
_PLACEHOLDER_SERVERS: tuple[str, ...] = (
    "IMPOSSIBLEISNOT",   # settings.py fallback default
    "YOUR_PC",           # .env.example
    "YOURSERVER",
    "YOUR_SERVER",
    "CHANGEME",
    "CHANGE_ME",
    "<SERVER>",
    "SERVERNAME",
)

# Inline secrets inside connection strings / URLs, e.g. "PWD=hunter2;" or
# "postgres://user:hunter2@host". Matched case-insensitively.
_INLINE_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(pwd|password)\s*=\s*([^;\s]+)"),
    re.compile(r"(?i)\b(uid|user id)\s*=\s*([^;\s]+)"),
    re.compile(r"(?i)(://[^:/\s]+:)([^@/\s]+)(@)"),
    re.compile(r"(?i)\b(authorization|bearer)\s*[:=]\s*(\S+)"),
)


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return key.upper() in SECRET_ENV_KEYS or any(h in k for h in _SECRET_HINTS)


def redact(value: Any) -> Any:
    """Return *value* with any embedded secrets masked.

    Handles plain strings (connection strings, URLs) and dicts (whose keys are
    inspected by name). Non-string scalars pass through untouched. This is
    intentionally conservative: it is far better to over-redact a log line than
    to leak a production password into CI output.

    >>> redact("DRIVER={x};SERVER=s;UID=gawain;PWD=hunter2;")
    'DRIVER={x};SERVER=s;UID=***REDACTED***;PWD=***REDACTED***;'
    >>> redact({"DB_PASSWORD": "hunter2", "DB_USER": "gawain"})
    {'DB_PASSWORD': '***REDACTED***', 'DB_USER': 'gawain'}
    """
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_secret_key(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    if not isinstance(value, str):
        return value

    out = value
    for pat in _INLINE_SECRET_PATTERNS:
        if pat.groups >= 3:
            out = pat.sub(lambda m: f"{m.group(1)}{_REDACTED}{m.group(3)}", out)
        else:
            out = pat.sub(lambda m: f"{m.group(1)}={_REDACTED}", out)
    return out


# LogRecord attributes that are built-in; anything else was passed via `extra=`.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime", "message", "taskName",
}


class _DropColorMessage(logging.Filter):
    """Strip uvicorn's ANSI-laden `color_message` duplicate field."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.__dict__.pop("color_message", None)
        return True


class _RedactingFilter(logging.Filter):
    """Scrubs secrets from every record before it reaches a handler.

    Covers the message, positional/dict args, **and every field passed via
    ``extra=``** — the last one matters most, because structured context is
    exactly where a connection string or password is most likely to be handed
    to the logger.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = redact(record.args)
                else:
                    record.args = tuple(redact(a) for a in record.args)
            # Anything not a built-in LogRecord attribute arrived via extra=.
            for key, val in list(record.__dict__.items()):
                if key in _RESERVED or key.startswith("_"):
                    continue
                record.__dict__[key] = (
                    _REDACTED if _is_secret_key(key) else redact(val)
                )
        except Exception:  # never let logging break the app
            pass
        return True


# ──────────────────────────────────────────────────────────────────────────────
# Formatters
# ──────────────────────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """One JSON object per line — for Docker, CI, and log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_type"] = getattr(record.exc_info[0], "__name__", None)
            payload["exc"] = "".join(traceback.format_exception(*record.exc_info)).strip()
        for key, val in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(val)
                    payload[key] = val
                except (TypeError, ValueError):
                    payload[key] = repr(val)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Aligned, optionally colourised output for humans at a terminal."""

    _COLORS = {
        "DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
        "ERROR": "\033[31m", "CRITICAL": "\033[1;41m",
    }
    _DIM, _RESET = "\033[2m", "\033[0m"

    def __init__(self, *, color: bool = True) -> None:
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        ts = _dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        lvl = record.levelname
        if self.color:
            c = self._COLORS.get(lvl, "")
            head = f"{self._DIM}{ts}{self._RESET} {c}{lvl:<8}{self._RESET} {self._DIM}{record.name}{self._RESET}"
        else:
            head = f"{ts} {lvl:<8} {record.name}"

        line = f"{head}  {record.getMessage()}"

        ctx = {
            k: v for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")
        }
        if ctx:
            line += "  " + " ".join(f"{k}={v!r}" for k, v in ctx.items())
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return line


# ──────────────────────────────────────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────────────────────────────────────

_CONFIGURED = False

#: Third-party loggers that are far too chatty at DEBUG.
_NOISY = ("httpx", "httpcore", "urllib3", "asyncio", "matplotlib", "PIL", "numexpr")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def in_container() -> bool:
    """Best-effort detection of running inside a container.

    Checks the Docker sentinel file, the cgroup hierarchy, and the marker the
    Dockerfile can set. Used to decide log format defaults and to catch the
    `localhost`/Windows-Auth misconfigurations that only bite inside Docker.
    """
    if _env_flag("GAWAIN_IN_CONTAINER"):
        return True
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "rt", encoding="utf-8", errors="ignore") as fh:
            blob = fh.read()
        return "docker" in blob or "kubepods" in blob or "containerd" in blob
    except OSError:
        return False


def setup_logging(
    level: str | int | None = None,
    *,
    fmt: str | None = None,
    color: bool | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure root logging. Idempotent — safe to call from several entrypoints.

    Environment:
        LOG_LEVEL   DEBUG|INFO|WARNING|ERROR|CRITICAL   (default INFO)
        LOG_FORMAT  json|console|auto                   (default auto)
        LOG_COLOR   1|0                                 (default: auto by TTY)

    ``auto`` chooses JSON when running in a container or when stdout is not a
    TTY (i.e. CI, piped output), and the human console format otherwise.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return logging.getLogger("gawain")

    lvl_raw = level if level is not None else os.getenv("LOG_LEVEL", "INFO")
    if isinstance(lvl_raw, str):
        lvl = getattr(logging, lvl_raw.strip().upper(), logging.INFO)
    else:
        lvl = int(lvl_raw)

    mode = (fmt or os.getenv("LOG_FORMAT", "auto")).strip().lower()
    is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if mode == "auto":
        mode = "json" if (in_container() or not is_tty) else "console"

    if color is None:
        color = _env_flag("LOG_COLOR", default=is_tty) and os.getenv("NO_COLOR") is None

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if mode == "json" else ConsoleFormatter(color=color))
    handler.addFilter(_RedactingFilter())

    root = logging.getLogger()
    for old in root.handlers[:]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(lvl)

    # Uvicorn installs its own handlers; strip them so we don't double-print,
    # and let records propagate up to our single configured root handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        ulog = logging.getLogger(name)
        ulog.handlers.clear()
        ulog.propagate = True
    # uvicorn attaches a pre-colourised duplicate of every message as
    # `color_message`; it would be echoed as a context field with raw ANSI
    # escapes in it. Drop it at the source.
    logging.getLogger("uvicorn").addFilter(_DropColorMessage())
    logging.getLogger("uvicorn.error").addFilter(_DropColorMessage())
    logging.getLogger("uvicorn.access").addFilter(_DropColorMessage())

    for name in _NOISY:
        logging.getLogger(name).setLevel(max(lvl, logging.WARNING))

    _CONFIGURED = True
    return logging.getLogger("gawain")


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a namespaced logger, configuring logging on first use."""
    if not _CONFIGURED:
        setup_logging()
    if not name or name in {"__main__", "main"}:
        return logging.getLogger("gawain")
    if name.startswith("gawain"):
        return logging.getLogger(name)
    return logging.getLogger(f"gawain.{name}")


# ──────────────────────────────────────────────────────────────────────────────
# Environment validation
# ──────────────────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    ERROR = "ERROR"    # will not work — needs fixing
    WARN = "WARN"      # probably wrong, or will degrade
    INFO = "INFO"      # notable, benign

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Issue:
    """A single validation finding."""
    severity: Severity
    code: str            # stable machine-readable id, e.g. "DB_WINDOWS_AUTH_IN_CONTAINER"
    message: str         # what is wrong
    hint: str = ""       # how to fix it

    def format(self) -> str:
        icon = {Severity.ERROR: "✗", Severity.WARN: "!", Severity.INFO: "·"}[self.severity]
        out = f"{icon} [{self.code}] {self.message}"
        return f"{out}\n    → {self.hint}" if self.hint else out


@dataclass
class ValidationReport:
    """Aggregated findings from :func:`validate_environment`."""
    issues: list[Issue] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def add(self, severity: Severity, code: str, message: str, hint: str = "") -> None:
        self.issues.append(Issue(severity, code, message, hint))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARN]

    @property
    def ok(self) -> bool:
        """True when nothing will hard-fail. Warnings do not make this False."""
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "facts": redact(self.facts),
            "issues": [
                {"severity": str(i.severity), "code": i.code,
                 "message": i.message, "hint": i.hint}
                for i in self.issues
            ],
        }

    def log(self, logger: logging.Logger | None = None) -> "ValidationReport":
        """Emit the report through the logging system at appropriate levels."""
        log = logger or get_logger("config")
        log.info(
            "environment preflight: %s (%d error(s), %d warning(s))",
            "OK" if self.ok else "PROBLEMS FOUND",
            len(self.errors), len(self.warnings),
            extra={"facts": redact(self.facts)},
        )
        for issue in self.issues:
            lvl = {
                Severity.ERROR: logging.ERROR,
                Severity.WARN: logging.WARNING,
                Severity.INFO: logging.INFO,
            }[issue.severity]
            log.log(lvl, "%s", issue.message,
                    extra={"code": issue.code, "hint": issue.hint or None})
        return self

    def render(self) -> str:
        """Plain-text report — handy for CLI output and tests."""
        head = f"environment preflight: {'OK' if self.ok else 'PROBLEMS FOUND'}"
        lines = [head, "=" * len(head)]
        for k, v in redact(self.facts).items():
            lines.append(f"  {k:<20} {v}")
        if self.issues:
            lines.append("")
            lines.extend(i.format() for i in self.issues)
        return "\n".join(lines)


def _installed_odbc_drivers() -> list[str] | None:
    """Names of ODBC drivers pyodbc can see, or None if pyodbc is unavailable."""
    try:
        import pyodbc  # noqa: PLC0415 — optional, imported lazily on purpose
        return list(pyodbc.drivers())
    except Exception:
        return None


def _check_numeric_env(report: ValidationReport, names_defaults: Iterable[tuple[str, str]]) -> None:
    """Numeric env vars crash `settings.py` at import with a raw ValueError.

    Detect them here so the failure is named and explained instead of being a
    traceback from a float() call the user never wrote.
    """
    for name, kind in names_defaults:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            continue
        try:
            float(raw) if kind == "float" else int(raw)
        except ValueError:
            report.add(
                Severity.ERROR, "ENV_NOT_NUMERIC",
                f"{name}={raw!r} is not a valid {kind}",
                f"Set {name} to a {kind} value, or remove it to use the default.",
            )


def validate_environment(*, check_paths: bool = True) -> ValidationReport:
    """Inspect the environment and return a :class:`ValidationReport`.

    Pure inspection: reads ``os.environ`` and the filesystem, never connects to
    a database or an LLM. Safe to call at import time or in CI.
    """
    r = ValidationReport()
    containerised = in_container()

    db_server = os.getenv("DB_SERVER", "")
    db_database = os.getenv("DB_DATABASE", "")
    db_driver = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
    db_user = os.getenv("DB_USER", "").strip()
    db_password = os.getenv("DB_PASSWORD", "").strip()
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    r.facts.update({
        "in_container": containerised,
        "python": sys.version.split()[0],
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "log_format": os.getenv("LOG_FORMAT", "auto"),
        "DB_SERVER": db_server or "<unset>",
        "DB_DATABASE": db_database or "<unset>",
        "DB_DRIVER": db_driver,
        "DB_AUTH": "sql" if (db_user and db_password) else "windows/trusted",
        "OLLAMA_BASE_URL": ollama_url,
    })

    # ── .env presence ─────────────────────────────────────────────────────────
    if not os.getenv("DB_SERVER"):
        r.add(
            Severity.WARN, "ENV_NO_DB_SERVER",
            "DB_SERVER is unset — settings.py will fall back to its placeholder default",
            "cp .env.docker.example .env  (Docker)  or  cp .env.example .env  (local), "
            "then set DB_SERVER.",
        )
    elif any(p in db_server.upper() for p in _PLACEHOLDER_SERVERS):
        r.add(
            Severity.WARN, "DB_PLACEHOLDER_SERVER",
            f"DB_SERVER still holds an unedited placeholder ({db_server!r})",
            "Point DB_SERVER at your own SQL Server instance — this value came "
            "straight from .env.example and will not resolve.",
        )

    # ── The two footguns that actually bite in Docker ─────────────────────────
    if containerised:
        if not (db_user and db_password):
            r.add(
                Severity.ERROR, "DB_WINDOWS_AUTH_IN_CONTAINER",
                "Windows/Trusted authentication cannot work from inside a Linux "
                "container, but DB_USER/DB_PASSWORD are not both set",
                "Enable SQL Server Authentication (mixed mode), create a SQL login, "
                "and set DB_USER + DB_PASSWORD. See README → Getting to database: true.",
            )
        host = db_server.split("\\", 1)[0].split(",", 1)[0].strip().lower()
        if host in {"localhost", "127.0.0.1", "::1", "."}:
            r.add(
                Severity.ERROR, "DB_LOCALHOST_IN_CONTAINER",
                f"DB_SERVER={db_server!r} points at the container itself, not your host",
                "Use host.docker.internal\\INSTANCE to reach a SQL Server running on "
                "the host, or the compose service name (e.g. 'mssql').",
            )
        if "localhost" in ollama_url or "127.0.0.1" in ollama_url:
            r.add(
                Severity.WARN, "OLLAMA_LOCALHOST_IN_CONTAINER",
                f"OLLAMA_BASE_URL={ollama_url!r} resolves to the container itself",
                "Inside docker compose use http://ollama:11434.",
            )
    else:
        if db_user and not db_password:
            r.add(
                Severity.WARN, "DB_USER_WITHOUT_PASSWORD",
                "DB_USER is set but DB_PASSWORD is empty — falling back to Trusted_Connection",
                "Set DB_PASSWORD, or clear DB_USER to use Windows Auth deliberately.",
            )

    # ── ODBC driver sanity ────────────────────────────────────────────────────
    drivers = _installed_odbc_drivers()
    if drivers is None:
        r.add(
            Severity.WARN, "ODBC_PYODBC_UNAVAILABLE",
            "pyodbc could not be imported — cannot verify ODBC drivers",
            "Windows: pip install pyodbc (the OS ships the ODBC manager). "
            "Linux/macOS: pyodbc also needs the unixODBC runtime "
            "(libodbc.so.2) present at import time — apt install unixodbc.",
        )
    else:
        r.facts["odbc_drivers"] = drivers or ["<none>"]
        if not drivers:
            r.add(
                Severity.ERROR, "ODBC_NO_DRIVERS",
                "No ODBC drivers are installed — every database connection will fail",
                "Install msodbcsql18 (or 17). The project Dockerfile installs both.",
            )
        elif db_driver not in drivers:
            r.add(
                Severity.ERROR, "ODBC_DRIVER_NOT_FOUND",
                f"DB_DRIVER={db_driver!r} is not installed. Available: {drivers}",
                "Set DB_DRIVER to one of the names listed above, exactly as spelled.",
            )

    # ODBC 18 encrypts by default and rejects self-signed server certificates.
    if "18" in db_driver and "TrustServerCertificate" not in os.getenv("DB_EXTRA_OPTS", ""):
        r.add(
            Severity.INFO, "ODBC18_ENCRYPT_DEFAULT",
            "ODBC Driver 18 encrypts by default and will reject a self-signed "
            "server certificate",
            "If you hit 'SSL Provider: certificate verify failed', use "
            "'ODBC Driver 17 for SQL Server' or add TrustServerCertificate=yes.",
        )

    # ── Numeric env vars that would crash settings.py at import ───────────────
    _check_numeric_env(r, [
        ("LLM_TEMPERATURE", "float"), ("LLM_TEMPERATURE_ANALYSIS", "float"),
        ("LLM_TIMEOUT_SQL", "float"), ("LLM_TIMEOUT_STREAM", "float"),
        ("LLM_NUM_PREDICT", "int"), ("LLM_ANALYSIS_TOKENS", "int"),
        ("MAX_DIM_CARDINALITY", "int"), ("EXTRACT_ROW_LIMIT", "int"),
    ])

    # ── Filesystem ────────────────────────────────────────────────────────────
    if check_paths:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        storage = os.path.join(root, "storage")
        static = os.path.join(root, "static")

        if not os.path.isdir(storage):
            r.add(
                Severity.WARN, "STORAGE_MISSING",
                f"storage directory does not exist: {storage}",
                "It holds history.db and analytics.duckdb; create it or let the app.",
            )
        elif not os.access(storage, os.W_OK):
            r.add(
                Severity.ERROR, "STORAGE_NOT_WRITABLE",
                f"storage directory is not writable: {storage}",
                "Query history and the DuckDB extract will fail. Fix the mount "
                "permissions on ./storage.",
            )

        if not os.path.isdir(static):
            r.add(
                Severity.WARN, "STATIC_MISSING",
                f"static directory does not exist: {static} — the UI will 404",
                "Run: cd frontend && npm ci && npm run build",
            )
        elif not os.path.isfile(os.path.join(static, "index.html")):
            r.add(
                Severity.WARN, "STATIC_NO_INDEX",
                "static/index.html is missing — the SPA shell will not load",
                "Run: cd frontend && npm run build",
            )

    # ── Star-schema config used by the driver engine ──────────────────────────
    measures = [m.strip() for m in os.getenv("STAR_MEASURES", "").split(",") if m.strip()]
    if os.getenv("STAR_MEASURES") is not None and not measures:
        r.add(
            Severity.WARN, "STAR_MEASURES_EMPTY",
            "STAR_MEASURES is set but empty — driver analysis has nothing to attribute",
            "Provide a comma-separated list, e.g. SalesAmount,OrderQuantity.",
        )

    return r


def preflight(
    *,
    logger: logging.Logger | None = None,
    strict: bool | None = None,
) -> ValidationReport:
    """Validate the environment and log the result.

    Returns the report. Raises :class:`RuntimeError` only when strict mode is on
    (argument, or ``GAWAIN_STRICT_ENV=1``) *and* hard errors were found —
    otherwise the app is allowed to boot degraded, by design.
    """
    report = validate_environment()
    report.log(logger)

    if strict is None:
        strict = _env_flag("GAWAIN_STRICT_ENV", default=False)
    if strict and report.errors:
        codes = ", ".join(i.code for i in report.errors)
        raise RuntimeError(
            f"environment validation failed with {len(report.errors)} error(s): {codes} "
            "(GAWAIN_STRICT_ENV is enabled)"
        )
    return report


# ──────────────────────────────────────────────────────────────────────────────
# CLI self-test:  python -m config.observability
# ──────────────────────────────────────────────────────────────────────────────

def _selftest() -> int:
    setup_logging(force=True)
    log = get_logger("selftest")

    log.info("structured logging online", extra={"mode": os.getenv("LOG_FORMAT", "auto")})
    log.debug("debug is only visible when LOG_LEVEL=DEBUG")
    log.warning("warnings look like this")

    # Prove redaction works on the shapes we actually log.
    conn = "DRIVER={ODBC Driver 18};SERVER=host;DATABASE=db;UID=gawain;PWD=hunter2;"
    assert "hunter2" not in redact(conn), "connection-string redaction failed"
    assert "hunter2" not in redact({"DB_PASSWORD": "hunter2"})["DB_PASSWORD"]
    log.info("secret redaction verified", extra={"conn": conn})

    try:
        raise ValueError("this is what a logged exception looks like")
    except ValueError:
        log.exception("exception rendering verified")

    print()
    print(validate_environment().render())
    print()

    report = validate_environment()
    log.info("report is JSON-serialisable", extra={"keys": sorted(report.as_dict())})
    json.dumps(report.as_dict())  # must not raise
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
