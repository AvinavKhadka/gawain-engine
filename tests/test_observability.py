"""
test_observability.py — Tests for config/observability.py

Stdlib unittest only (no pytest dependency), so it runs anywhere the app runs:

    python -m unittest test_observability -v
    python test_observability.py
"""

from __future__ import annotations

import io
import json
import logging
import os
import unittest
from contextlib import contextmanager
from unittest import mock

from config import observability as obs


@contextmanager
def env(**kwargs):
    """Temporarily set/clear environment variables."""
    missing = object()
    saved = {k: os.environ.get(k, missing) for k in kwargs}
    try:
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is missing:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


@contextmanager
def capture_logs(level=logging.DEBUG, fmt="json"):
    """Attach a temporary handler and yield the captured stream."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(obs.JsonFormatter() if fmt == "json"
                         else obs.ConsoleFormatter(color=False))
    handler.addFilter(obs._RedactingFilter())
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    root.handlers = [handler]
    root.setLevel(level)
    try:
        yield stream
    finally:
        root.handlers, root.level = old_handlers, old_level


# ──────────────────────────────────────────────────────────────────────────────
# Redaction — the security-critical part
# ──────────────────────────────────────────────────────────────────────────────

class TestRedaction(unittest.TestCase):

    def test_connection_string_password(self):
        s = "DRIVER={ODBC Driver 18};SERVER=h;DATABASE=d;UID=gawain;PWD=hunter2;"
        out = obs.redact(s)
        self.assertNotIn("hunter2", out)
        self.assertNotIn("gawain", out)
        self.assertIn("SERVER=h", out)  # non-secrets survive

    def test_password_equals_form(self):
        self.assertNotIn("s3cret", obs.redact("Password=s3cret;Encrypt=yes"))

    def test_url_embedded_credentials(self):
        out = obs.redact("postgres://admin:topsecret@db.example.com:5432/x")
        self.assertNotIn("topsecret", out)
        self.assertIn("db.example.com", out)

    def test_dict_by_key_name(self):
        out = obs.redact({"DB_PASSWORD": "hunter2", "DB_USER": "gawain",
                          "DB_SERVER": "host.docker.internal"})
        self.assertEqual(out["DB_PASSWORD"], obs._REDACTED)
        self.assertEqual(out["DB_SERVER"], "host.docker.internal")

    def test_secret_hint_substring(self):
        out = obs.redact({"my_api_token": "abc123", "harmless": "value"})
        self.assertEqual(out["my_api_token"], obs._REDACTED)
        self.assertEqual(out["harmless"], "value")

    def test_nested_structures(self):
        out = obs.redact({"outer": {"DB_PASSWORD": "x"}, "lst": ["PWD=y;"]})
        self.assertEqual(out["outer"]["DB_PASSWORD"], obs._REDACTED)
        self.assertNotIn("y", out["lst"][0])

    def test_non_strings_pass_through(self):
        self.assertEqual(obs.redact(42), 42)
        self.assertIsNone(obs.redact(None))
        self.assertEqual(obs.redact(True), True)

    def test_filter_redacts_extra_fields(self):
        """Regression: secrets passed via extra= must not reach the handler."""
        with capture_logs() as stream:
            logging.getLogger("t").info(
                "connecting",
                extra={"conn": "UID=u;PWD=hunter2;", "DB_PASSWORD": "hunter2"},
            )
        out = stream.getvalue()
        self.assertNotIn("hunter2", out)
        self.assertIn("connecting", out)

    def test_filter_redacts_message_and_args(self):
        with capture_logs() as stream:
            logging.getLogger("t").warning("failed for %s", "PWD=hunter2;")
        self.assertNotIn("hunter2", stream.getvalue())


# ──────────────────────────────────────────────────────────────────────────────
# Formatters
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatters(unittest.TestCase):

    def test_json_is_one_object_per_line(self):
        with capture_logs(fmt="json") as stream:
            log = logging.getLogger("t")
            log.info("first", extra={"n": 1})
            log.info("second", extra={"n": 2})
        lines = [ln for ln in stream.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        for ln in lines:
            json.loads(ln)  # must not raise

    def test_json_fields(self):
        with capture_logs(fmt="json") as stream:
            logging.getLogger("t").info("hello", extra={"custom": "v"})
        rec = json.loads(stream.getvalue().strip())
        for key in ("ts", "level", "logger", "msg"):
            self.assertIn(key, rec)
        self.assertEqual(rec["msg"], "hello")
        self.assertEqual(rec["custom"], "v")

    def test_json_exception_capture(self):
        with capture_logs(fmt="json") as stream:
            try:
                raise ValueError("boom")
            except ValueError:
                logging.getLogger("t").exception("failed")
        rec = json.loads(stream.getvalue().strip())
        self.assertEqual(rec["exc_type"], "ValueError")
        self.assertIn("boom", rec["exc"])

    def test_json_survives_unserialisable_extra(self):
        with capture_logs(fmt="json") as stream:
            logging.getLogger("t").info("x", extra={"obj": object()})
        json.loads(stream.getvalue().strip())  # must not raise

    def test_console_has_no_ansi_when_disabled(self):
        with capture_logs(fmt="console") as stream:
            logging.getLogger("t").info("plain")
        self.assertNotIn("\033[", stream.getvalue())


# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────

class TestValidation(unittest.TestCase):

    def _codes(self, report):
        return {i.code for i in report.issues}

    def test_windows_auth_in_container_is_error(self):
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER=r"host.docker.internal\SQLEXPRESS",
                 DB_USER="", DB_PASSWORD=""):
            r = obs.validate_environment(check_paths=False)
        self.assertIn("DB_WINDOWS_AUTH_IN_CONTAINER", self._codes(r))
        self.assertFalse(r.ok)

    def test_localhost_in_container_is_error(self):
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER=r"localhost\SQLEXPRESS",
                 DB_USER="u", DB_PASSWORD="p"):
            r = obs.validate_environment(check_paths=False)
        self.assertIn("DB_LOCALHOST_IN_CONTAINER", self._codes(r))

    def test_correct_docker_config_is_clean(self):
        with env(GAWAIN_IN_CONTAINER="1",
                 DB_SERVER=r"host.docker.internal\SQLEXPRESS",
                 DB_DATABASE="AdventureWorksDW2019",
                 DB_USER="gawain", DB_PASSWORD="Secret123!",
                 OLLAMA_BASE_URL="http://ollama:11434"):
            with mock.patch.object(obs, "_installed_odbc_drivers",
                                   return_value=["ODBC Driver 17 for SQL Server"]):
                r = obs.validate_environment(check_paths=False)
        self.assertTrue(r.ok, f"unexpected errors: {[i.code for i in r.errors]}")

    def test_ollama_localhost_in_container_warns(self):
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER="h", DB_USER="u",
                 DB_PASSWORD="p", OLLAMA_BASE_URL="http://localhost:11434"):
            r = obs.validate_environment(check_paths=False)
        self.assertIn("OLLAMA_LOCALHOST_IN_CONTAINER", self._codes(r))

    def test_windows_auth_outside_container_is_fine(self):
        with env(GAWAIN_IN_CONTAINER=None, DB_SERVER=r".\SQLEXPRESS",
                 DB_USER="", DB_PASSWORD=""):
            with mock.patch.object(obs, "in_container", return_value=False):
                r = obs.validate_environment(check_paths=False)
        self.assertNotIn("DB_WINDOWS_AUTH_IN_CONTAINER", self._codes(r))

    def test_driver_not_installed_is_error(self):
        with env(DB_DRIVER="ODBC Driver 99 for SQL Server"):
            with mock.patch.object(obs, "_installed_odbc_drivers",
                                   return_value=["ODBC Driver 18 for SQL Server"]):
                r = obs.validate_environment(check_paths=False)
        self.assertIn("ODBC_DRIVER_NOT_FOUND", self._codes(r))
        self.assertFalse(r.ok)

    def test_no_drivers_at_all_is_error(self):
        with mock.patch.object(obs, "_installed_odbc_drivers", return_value=[]):
            r = obs.validate_environment(check_paths=False)
        self.assertIn("ODBC_NO_DRIVERS", self._codes(r))

    def test_pyodbc_missing_only_warns(self):
        with mock.patch.object(obs, "_installed_odbc_drivers", return_value=None):
            r = obs.validate_environment(check_paths=False)
        self.assertIn("ODBC_PYODBC_UNAVAILABLE", self._codes(r))
        self.assertNotIn("ODBC_PYODBC_UNAVAILABLE",
                         {i.code for i in r.errors})  # warn, not error

    def test_odbc18_note_is_info_only(self):
        with env(DB_DRIVER="ODBC Driver 18 for SQL Server"):
            with mock.patch.object(obs, "_installed_odbc_drivers",
                                   return_value=["ODBC Driver 18 for SQL Server"]):
                r = obs.validate_environment(check_paths=False)
        self.assertIn("ODBC18_ENCRYPT_DEFAULT", self._codes(r))
        self.assertTrue(r.ok)  # informational, must not block boot

    def test_bad_numeric_env_is_error(self):
        with env(LLM_TEMPERATURE="not-a-float"):
            r = obs.validate_environment(check_paths=False)
        self.assertIn("ENV_NOT_NUMERIC", self._codes(r))

    def test_good_numeric_env_passes(self):
        with env(LLM_TEMPERATURE="0.2", LLM_NUM_PREDICT="512"):
            r = obs.validate_environment(check_paths=False)
        self.assertNotIn("ENV_NOT_NUMERIC", self._codes(r))

    def test_placeholder_server_warns(self):
        with env(DB_SERVER=r"IMPOSSIBLEISNOT\MSSQLSERVER2019"):
            r = obs.validate_environment(check_paths=False)
        self.assertIn("DB_PLACEHOLDER_SERVER", self._codes(r))

    def test_report_is_json_serialisable(self):
        r = obs.validate_environment(check_paths=False)
        json.dumps(r.as_dict())  # must not raise

    def test_report_never_contains_password(self):
        with env(DB_PASSWORD="SuperSecret123", DB_USER="u", DB_SERVER="h"):
            r = obs.validate_environment(check_paths=False)
        blob = r.render() + json.dumps(r.as_dict())
        self.assertNotIn("SuperSecret123", blob)


# ──────────────────────────────────────────────────────────────────────────────
# preflight() contract
# ──────────────────────────────────────────────────────────────────────────────

class TestPreflight(unittest.TestCase):

    def test_does_not_raise_by_default(self):
        """Graceful degradation: bad config must not stop the app booting."""
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER="localhost",
                 DB_USER="", DB_PASSWORD="", GAWAIN_STRICT_ENV=None):
            with capture_logs():
                r = obs.preflight()
        self.assertFalse(r.ok)  # problems found...
        # ...but no exception raised.

    def test_strict_mode_raises_on_errors(self):
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER="localhost",
                 DB_USER="", DB_PASSWORD="", GAWAIN_STRICT_ENV="1"):
            with capture_logs():
                with self.assertRaises(RuntimeError) as ctx:
                    obs.preflight()
        self.assertIn("DB_WINDOWS_AUTH_IN_CONTAINER", str(ctx.exception))

    def test_strict_mode_silent_when_clean(self):
        with env(GAWAIN_STRICT_ENV="1", DB_SERVER=r".\SQLEXPRESS"):
            with mock.patch.object(obs, "in_container", return_value=False), \
                 mock.patch.object(obs, "_installed_odbc_drivers",
                                   return_value=["ODBC Driver 17 for SQL Server"]):
                with capture_logs():
                    r = obs.preflight()  # must not raise
        self.assertTrue(r.ok)


class TestLoggingSetup(unittest.TestCase):

    def test_setup_is_idempotent(self):
        obs.setup_logging(force=True)
        n = len(logging.getLogger().handlers)
        obs.setup_logging()
        obs.setup_logging()
        self.assertEqual(len(logging.getLogger().handlers), n)

    def test_get_logger_namespaces(self):
        self.assertEqual(obs.get_logger("server.llm").name, "gawain.server.llm")
        self.assertEqual(obs.get_logger("__main__").name, "gawain")
        self.assertEqual(obs.get_logger("gawain.x").name, "gawain.x")

    def test_level_from_env(self):
        with env(LOG_LEVEL="WARNING"):
            obs.setup_logging(force=True)
            self.assertEqual(logging.getLogger().level, logging.WARNING)
        obs.setup_logging(level="INFO", force=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
