"""Tests for config/observability.py — logging, redaction, preflight."""

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
    """Temporarily set or clear environment variables."""
    missing = object()
    saved = {k: os.environ.get(k, missing) for k in kwargs}
    try:
        for k, v in kwargs.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        yield
    finally:
        for k, old in saved.items():
            os.environ.pop(k, None) if old is missing else os.environ.__setitem__(k, old)


@contextmanager
def capture(fmt="json"):
    """Attach a temporary handler and yield the captured stream."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(obs._Json() if fmt == "json" else obs._Console(color=False))
    handler.addFilter(obs._Scrub())
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    root.handlers = [handler]
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.handlers, root.level = old_handlers, old_level


class TestRedaction(unittest.TestCase):
    """Security-critical: a password must never reach a log sink."""

    def test_connection_string(self):
        out = obs.redact("DRIVER={x};SERVER=h;UID=gawain;PWD=hunter2;")
        self.assertNotIn("hunter2", out)
        self.assertNotIn("gawain", out)
        self.assertIn("SERVER=h", out)

    def test_url_credentials(self):
        out = obs.redact("postgres://admin:topsecret@db:5432/x")
        self.assertNotIn("topsecret", out)
        self.assertIn("db:5432", out)

    def test_dict_by_key_name(self):
        out = obs.redact({"DB_PASSWORD": "hunter2", "DB_SERVER": "host"})
        self.assertEqual(out["DB_PASSWORD"], obs._REDACTED)
        self.assertEqual(out["DB_SERVER"], "host")

    def test_secret_hint_substring(self):
        out = obs.redact({"my_api_token": "abc", "harmless": "v"})
        self.assertEqual(out["my_api_token"], obs._REDACTED)
        self.assertEqual(out["harmless"], "v")

    def test_nested(self):
        out = obs.redact({"o": {"DB_PASSWORD": "x"}, "l": ["PWD=y;"]})
        self.assertEqual(out["o"]["DB_PASSWORD"], obs._REDACTED)
        self.assertNotIn("y", out["l"][0])

    def test_non_strings_pass_through(self):
        self.assertEqual(obs.redact(42), 42)
        self.assertIsNone(obs.redact(None))

    def test_filter_scrubs_extra_fields(self):
        """Regression: extra= is where a connection string is most likely."""
        with capture() as s:
            logging.getLogger("t").info(
                "connecting", extra={"conn": "UID=u;PWD=hunter2;",
                                     "DB_PASSWORD": "hunter2"})
        self.assertNotIn("hunter2", s.getvalue())

    def test_filter_scrubs_message_and_args(self):
        with capture() as s:
            logging.getLogger("t").warning("failed for %s", "PWD=hunter2;")
        self.assertNotIn("hunter2", s.getvalue())


class TestFormatters(unittest.TestCase):

    def test_json_one_object_per_line(self):
        with capture("json") as s:
            log = logging.getLogger("t")
            log.info("a")
            log.info("b")
        lines = [ln for ln in s.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        for ln in lines:
            json.loads(ln)

    def test_json_fields_and_context(self):
        with capture("json") as s:
            logging.getLogger("t").info("hello", extra={"custom": "v"})
        rec = json.loads(s.getvalue().strip())
        self.assertEqual(rec["msg"], "hello")
        self.assertEqual(rec["custom"], "v")
        for key in ("ts", "level", "logger"):
            self.assertIn(key, rec)

    def test_json_captures_exception(self):
        with capture("json") as s:
            try:
                raise ValueError("boom")
            except ValueError:
                logging.getLogger("t").exception("failed")
        self.assertIn("boom", json.loads(s.getvalue().strip())["exc"])

    def test_json_survives_unserialisable_extra(self):
        with capture("json") as s:
            logging.getLogger("t").info("x", extra={"obj": object()})
        json.loads(s.getvalue().strip())

    def test_console_no_ansi_when_disabled(self):
        with capture("console") as s:
            logging.getLogger("t").info("plain")
        self.assertNotIn("\033[", s.getvalue())

    def test_uvicorn_color_message_dropped(self):
        """uvicorn attaches an ANSI duplicate of every message."""
        with capture("console") as s:
            logging.getLogger("t").info("Started", extra={"color_message": "\033[36mStarted"})
        self.assertNotIn("color_message", s.getvalue())


class TestSetup(unittest.TestCase):

    def test_idempotent(self):
        obs.setup_logging(force=True)
        n = len(logging.getLogger().handlers)
        obs.setup_logging()
        obs.setup_logging()
        self.assertEqual(len(logging.getLogger().handlers), n)

    def test_namespacing(self):
        self.assertEqual(obs.get_logger("server.llm").name, "gawain.server.llm")
        self.assertEqual(obs.get_logger("__main__").name, "gawain")
        self.assertEqual(obs.get_logger("gawain.x").name, "gawain.x")

    def test_level_from_env(self):
        with env(LOG_LEVEL="WARNING"):
            obs.setup_logging(force=True)
            self.assertEqual(logging.getLogger().level, logging.WARNING)
        obs.setup_logging(level="INFO", force=True)


class TestPreflight(unittest.TestCase):

    def _messages(self, issues):
        return " ".join(m for _s, m, _h in issues)

    def test_windows_auth_in_container_is_error(self):
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER=r"host.docker.internal\X",
                 DB_USER="", DB_PASSWORD=""):
            issues = obs._checks()
        self.assertIn("Windows/Trusted auth", self._messages(issues))
        self.assertTrue(any(s == "ERROR" for s, _, _ in issues))

    def test_localhost_in_container_is_error(self):
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER="localhost",
                 DB_USER="u", DB_PASSWORD="p"):
            self.assertIn("points at the container itself",
                          self._messages(obs._checks()))

    def test_correct_docker_config_is_clean(self):
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER=r"host.docker.internal\X",
                 DB_USER="g", DB_PASSWORD="p",
                 OLLAMA_BASE_URL="http://ollama:11434",
                 DB_DRIVER="ODBC Driver 17 for SQL Server"):
            with mock.patch.object(obs, "_odbc_drivers",
                                   return_value=["ODBC Driver 17 for SQL Server"]):
                issues = obs._checks()
        errors = [m for s, m, _ in issues if s == "ERROR"]
        self.assertEqual(errors, [], f"unexpected: {errors}")

    def test_placeholder_server_warns(self):
        for placeholder in (r"YOUR_PC\X", r"IMPOSSIBLEISNOT\X", "CHANGEME"):
            with self.subTest(p=placeholder), env(DB_SERVER=placeholder):
                self.assertIn("placeholder", self._messages(obs._checks()))

    def test_real_server_not_flagged(self):
        with env(DB_SERVER=r"host.docker.internal\SQLEXPRESS"):
            self.assertNotIn("placeholder", self._messages(obs._checks()))

    def test_no_drivers_is_error(self):
        with mock.patch.object(obs, "_odbc_drivers", return_value=[]):
            self.assertIn("No ODBC drivers", self._messages(obs._checks()))

    def test_driver_not_installed_is_error(self):
        with env(DB_DRIVER="ODBC Driver 99 for SQL Server"):
            with mock.patch.object(obs, "_odbc_drivers",
                                   return_value=["ODBC Driver 18 for SQL Server"]):
                self.assertIn("is not installed", self._messages(obs._checks()))

    def test_pyodbc_missing_only_warns(self):
        with mock.patch.object(obs, "_odbc_drivers", return_value=None):
            issues = obs._checks()
        pyodbc_issues = [s for s, m, _ in issues if "pyodbc" in m]
        self.assertEqual(pyodbc_issues, ["WARN"])

    def test_bad_numeric_env_is_error(self):
        with env(LLM_TEMPERATURE="abc"):
            self.assertIn("not a valid float", self._messages(obs._checks()))

    def test_good_numeric_env_passes(self):
        with env(LLM_TEMPERATURE="0.2", LLM_NUM_PREDICT="512"):
            self.assertNotIn("not a valid", self._messages(obs._checks()))

    def test_does_not_raise_by_default(self):
        """Graceful degradation: bad config must not stop the app booting."""
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER="localhost",
                 DB_USER="", DB_PASSWORD="", GAWAIN_STRICT_ENV=None):
            with capture():
                issues = obs.preflight()
        self.assertTrue(any(s == "ERROR" for s, _, _ in issues))

    def test_strict_mode_raises(self):
        with env(GAWAIN_IN_CONTAINER="1", DB_SERVER="localhost",
                 DB_USER="", DB_PASSWORD="", GAWAIN_STRICT_ENV="1"):
            with capture(), self.assertRaises(RuntimeError):
                obs.preflight()

    def test_preflight_never_logs_password(self):
        with env(DB_PASSWORD="SuperSecret123", DB_USER="u", DB_SERVER="h"):
            with capture() as s:
                obs.preflight()
        self.assertNotIn("SuperSecret123", s.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
