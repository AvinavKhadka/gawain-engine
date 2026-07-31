"""Tests for failure capture and prompt-level reinforcement."""

from __future__ import annotations

import os
import tempfile
import unittest

import server.failures as F


class TestErrorSignature(unittest.TestCase):
    """Signatures must group repeats while keeping the useful identifier."""

    def test_invalid_column(self):
        e = ("('42S22', \"[42S22] [Microsoft][ODBC Driver 17 for SQL Server]"
             "Invalid column name 'OrderNumber'. (207) (SQLExecDirectW)\")")
        self.assertEqual(F.error_signature(e), "invalid_column:OrderNumber")

    def test_same_column_same_signature(self):
        a = "Invalid column name 'Foo'. (207)"
        b = "[ODBC Driver 17] Invalid column name 'Foo'. (207) (SQLExecDirectW)"
        self.assertEqual(F.error_signature(a), F.error_signature(b))

    def test_different_columns_differ(self):
        self.assertNotEqual(
            F.error_signature("Invalid column name 'A'."),
            F.error_signature("Invalid column name 'B'."),
        )

    def test_syntax_error(self):
        self.assertEqual(
            F.error_signature("Incorrect syntax near 'LIMIT'. (102)"),
            "syntax_near:LIMIT",
        )

    def test_invalid_object(self):
        self.assertEqual(
            F.error_signature("Invalid object name 'dbo.Nope'."),
            "invalid_object:dbo.Nope",
        )

    def test_connection_errors(self):
        self.assertEqual(F.error_signature("Login timeout expired"),
                         "connection_timeout")
        self.assertEqual(F.error_signature("Login failed for user 'sa'."),
                         "login_failed")

    def test_empty(self):
        self.assertEqual(F.error_signature(""), "unknown")


class TestRecordAndLessons(unittest.TestCase):

    def setUp(self):
        self._saved = F.FAILURES_PATH
        F.FAILURES_PATH = os.path.join(tempfile.mkdtemp(), "failures.jsonl")
        F.clear_lessons_cache()

    def tearDown(self):
        F.FAILURES_PATH = self._saved
        F.clear_lessons_cache()

    def _fail(self, col="OrderNumber", n=1):
        e = f"Invalid column name '{col}'. (207)"
        for _ in range(n):
            F.record_failure("q", f"SELECT fis.{col}", e,
                             stage="validation", attempts=2)

    def test_record_writes(self):
        self.assertTrue(self._fail() or True)
        self._fail()
        self.assertEqual(len(F.load_failures()), 2)

    def test_single_failure_is_not_yet_a_lesson(self):
        """One-off failures are noise; only repeats become guidance."""
        self._fail(n=1)
        self.assertEqual(F.build_lessons(), [])

    def test_repeated_failure_becomes_a_lesson(self):
        self._fail(n=2)
        lessons = F.build_lessons()
        self.assertEqual(len(lessons), 1)
        self.assertIn("OrderNumber", lessons[0])

    def test_lessons_block_empty_when_nothing_learned(self):
        self.assertEqual(F.lessons_block(), "")

    def test_lessons_block_has_content(self):
        self._fail(n=2)
        block = F.lessons_block()
        self.assertIn("do not repeat", block)
        self.assertIn("OrderNumber", block)

    def test_lesson_cap_respected(self):
        for i in range(12):
            self._fail(col=f"Col{i}", n=2)
        self.assertLessEqual(len(F.build_lessons()), F.MAX_LESSONS)

    def test_stats_group_by_signature(self):
        self._fail(col="A", n=3)
        self._fail(col="B", n=1)
        stats = F.failure_stats()
        self.assertEqual(stats["total"], 4)
        self.assertEqual(dict(stats["by_signature"])["invalid_column:A"], 3)

    def test_record_never_raises_on_bad_path(self):
        """An error-path logger must not create a second error."""
        F.FAILURES_PATH = "/nonexistent\x00/dir/failures.jsonl"
        self.assertFalse(F.record_failure("q", "sql", "err", stage="validation"))

    def test_malformed_lines_skipped(self):
        os.makedirs(os.path.dirname(F.FAILURES_PATH), exist_ok=True)
        with open(F.FAILURES_PATH, "w", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write('{"signature":"invalid_column:X","stage":"validation"}\n')
        self.assertEqual(len(F.load_failures()), 1)

    def test_trim_caps_file(self):
        F.MAX_RECORDS, saved = 10, F.MAX_RECORDS
        try:
            self._fail(n=25)
            self.assertLessEqual(len(F.load_failures()), 10)
        finally:
            F.MAX_RECORDS = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestLearnedRepairs(unittest.TestCase):
    """Mappings discovered from repairs that worked — not hand-written rules."""

    def setUp(self):
        import tempfile
        self._f, self._r = F.FAILURES_PATH, F.REPAIRS_PATH
        d = tempfile.mkdtemp()
        F.FAILURES_PATH = os.path.join(d, "failures.jsonl")
        F.REPAIRS_PATH = os.path.join(d, "repairs.jsonl")
        F.clear_lessons_cache()

    def tearDown(self):
        F.FAILURES_PATH, F.REPAIRS_PATH = self._f, self._r
        F.clear_lessons_cache()

    def _repair(self, wrong="GrossProfit", right="SalesAmount", n=1):
        bad = f"SELECT fis.{wrong} FROM dbo.FactInternetSales fis"
        good = f"SELECT fis.{right} FROM dbo.FactInternetSales fis"
        err = f"Invalid column name '{wrong}'. (207)"
        for _ in range(n):
            F.record_repair("q", bad, good, err, stage="validation")

    def test_infers_single_column_rename(self):
        self.assertEqual(
            F.diff_column_fix("SELECT fis.A FROM t fis", "SELECT fis.B FROM t fis"),
            ("A", "B"),
        )

    def test_refuses_multi_change_repair(self):
        """Cannot attribute cause when several things changed at once."""
        self.assertIsNone(F.diff_column_fix(
            "SELECT fis.A, fis.B FROM t fis", "SELECT fis.X, fis.Y FROM t fis"))

    def test_refuses_alias_change(self):
        self.assertIsNone(F.diff_column_fix(
            "SELECT a.Col FROM t a", "SELECT b.Other FROM t b"))

    def test_single_observation_not_yet_trusted(self):
        self._repair(n=1)
        self.assertEqual(F.learned_column_fixes(), {})

    def test_confirmed_mapping_is_trusted(self):
        self._repair(n=2)
        self.assertEqual(F.learned_column_fixes(), {"grossprofit": "SalesAmount"})

    def test_conflicting_evidence_refused(self):
        """Same wrong column repaired two different ways -> learn nothing."""
        self._repair(wrong="Foo", right="Bar", n=2)
        self._repair(wrong="Foo", right="Baz", n=2)
        self.assertEqual(F.learned_column_fixes(), {})

    def test_applies_to_a_query_never_seen_before(self):
        """The whole point: generalises beyond the query that taught it."""
        self._repair(n=2)
        fresh = "SELECT dd.CalendarYear, fis.GrossProfit FROM t fis"
        out, applied = F.apply_learned_fixes(fresh)
        self.assertIn("fis.SalesAmount", out)
        self.assertNotIn("GrossProfit", out)
        self.assertEqual(len(applied), 1)

    def test_noop_when_nothing_learned(self):
        sql = "SELECT fis.SalesAmount FROM t fis"
        out, applied = F.apply_learned_fixes(sql)
        self.assertEqual(out, sql)
        self.assertEqual(applied, [])

    def test_table_qualifier_never_rewritten(self):
        self._repair(wrong="FactInternetSales", right="Whatever", n=2)
        out, _ = F.apply_learned_fixes("SELECT x FROM dbo.FactInternetSales fis")
        self.assertIn("dbo.FactInternetSales", out)

    def test_lesson_names_the_correct_column(self):
        """Positive guidance beats negation."""
        err = "Invalid column name 'OrderNumber'. (207)"
        for _ in range(2):
            F.record_failure("q", "sql", err, stage="validation")
        self._repair(wrong="OrderNumber", right="SalesOrderNumber", n=2)
        block = F.lessons_block()
        self.assertIn("SalesOrderNumber", block)
        self.assertIn("correct column", block)

    def test_record_repair_never_raises(self):
        F.REPAIRS_PATH = "/nonexistent\x00/r.jsonl"
        self.assertFalse(F.record_repair("q", "a", "b", "e", stage="validation"))


class TestRealWorldRepairs(unittest.TestCase):
    """Cases taken verbatim from production failure logs."""

    def setUp(self):
        import tempfile
        self._f, self._r = F.FAILURES_PATH, F.REPAIRS_PATH
        d = tempfile.mkdtemp()
        F.FAILURES_PATH = os.path.join(d, "failures.jsonl")
        F.REPAIRS_PATH = os.path.join(d, "repairs.jsonl")
        F.clear_lessons_cache()

    def tearDown(self):
        F.FAILURES_PATH, F.REPAIRS_PATH = self._f, self._r
        F.clear_lessons_cache()

    def test_unbound_identifier_signature(self):
        """A bad join alias must produce a usable signature, not 'other:(, )'."""
        e = ("('42000', '[42000] [Microsoft][ODBC Driver 17 for SQL Server]"
             "[SQL Server]The multi-part identifier \"dps.ProductSubcategoryKey\" "
             "could not be bound. (4104) (SQLExecDirectW)')")
        self.assertEqual(F.error_signature(e),
                         "unbound_identifier:dps.ProductSubcategoryKey")

    def test_fallback_signature_keeps_message_text(self):
        """Regression: the fallback used to strip the entire message."""
        sig = F.error_signature("('42000', '[42000] [Driver]Something odd happened. (99)')")
        self.assertNotEqual(sig, "other:(, )")
        self.assertIn("Something odd", sig)

    def test_learns_column_to_expression(self):
        """GrossProfit -> SalesAmount - TotalProductCost, from a real log."""
        bad = "SELECT SUM(fis.GrossProfit) AS GrossProfit FROM t fis"
        good = ("SELECT SUM(fis.SalesAmount - fis.TotalProductCost) "
                "AS GrossProfit FROM t fis")
        fix = F.diff_column_fix(bad, good)
        self.assertIsNotNone(fix)
        self.assertEqual(fix[0], "GrossProfit")
        self.assertIn("SalesAmount", fix[1])
        self.assertIn("TotalProductCost", fix[1])

    def test_expression_applied_and_parenthesised(self):
        """Precedence must be preserved when substituting an expression."""
        bad = "SELECT SUM(fis.GrossProfit) AS GP FROM t fis"
        good = "SELECT SUM(fis.SalesAmount - fis.TotalProductCost) AS GP FROM t fis"
        err = "Invalid column name 'GrossProfit'. (207)"
        for _ in range(2):
            F.record_repair("q", bad, good, err, stage="validation")

        out, applied = F.apply_learned_fixes(
            "SELECT SUM(fis.GrossProfit) / 2 AS GP FROM t fis")
        self.assertNotIn("fis.GrossProfit", out)
        self.assertIn("(fis.SalesAmount - fis.TotalProductCost)", out)
        self.assertEqual(len(applied), 1)
