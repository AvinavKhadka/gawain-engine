"""
test_sql_dialect.py — Tests for the T-SQL sanitiser in server/database.py

These lock in every dialect mistake an LLM has actually produced against this
schema, so the same bug cannot resurface silently.

    python -m unittest test_sql_dialect -v
"""

from __future__ import annotations

import unittest

from server.sql_dialect import preprocess_sql, _strip_trailing_limit, _apply_top


class TestLimitStripping(unittest.TestCase):
    """LIMIT is the failure that cost two LLM round-trips in production."""

    def test_plain_limit(self):
        sql, n = _strip_trailing_limit("SELECT a FROM t ORDER BY a DESC LIMIT 20")
        self.assertEqual(n, 20)
        self.assertNotIn("LIMIT", sql.upper())

    def test_limit_with_semicolon(self):
        sql, n = _strip_trailing_limit("SELECT a FROM t LIMIT 20;")
        self.assertEqual(n, 20)
        self.assertNotIn("LIMIT", sql.upper())

    def test_limit_offset(self):
        sql, n = _strip_trailing_limit("SELECT a FROM t LIMIT 20 OFFSET 40")
        self.assertEqual(n, 20)
        self.assertNotIn("OFFSET", sql.upper())

    def test_mysql_two_arg_limit(self):
        """MySQL `LIMIT offset, count` — the count is the row cap."""
        sql, n = _strip_trailing_limit("SELECT a FROM t LIMIT 40, 20")
        self.assertEqual(n, 20)
        self.assertNotIn("LIMIT", sql.upper())

    def test_fetch_first(self):
        sql, n = _strip_trailing_limit("SELECT a FROM t ORDER BY a FETCH FIRST 10 ROWS ONLY")
        self.assertEqual(n, 10)
        self.assertNotIn("FETCH", sql.upper())

    def test_no_limit_is_untouched(self):
        original = "SELECT TOP 5 a FROM t ORDER BY a DESC"
        sql, n = _strip_trailing_limit(original)
        self.assertIsNone(n)
        self.assertEqual(sql, original)

    def test_column_named_limit_not_mangled(self):
        """A column called `limit_value` must not trigger the stripper."""
        original = "SELECT limit_value FROM t"
        sql, n = _strip_trailing_limit(original)
        self.assertIsNone(n)
        self.assertEqual(sql, original)


class TestApplyTop(unittest.TestCase):

    def test_injects_top(self):
        self.assertIn("SELECT TOP 20", _apply_top("SELECT a FROM t", 20))

    def test_preserves_distinct_order(self):
        """T-SQL requires SELECT DISTINCT TOP n, not SELECT TOP n DISTINCT."""
        out = _apply_top("SELECT DISTINCT a FROM t", 20)
        self.assertIn("SELECT DISTINCT TOP 20", out)
        self.assertNotIn("TOP 20 DISTINCT", out)

    def test_existing_top_not_doubled(self):
        out = _apply_top("SELECT TOP 5 a FROM t", 20)
        self.assertEqual(out.upper().count("TOP"), 1)
        self.assertIn("TOP 5", out)

    def test_only_outermost_select(self):
        out = _apply_top("SELECT a FROM (SELECT b FROM t) x", 10)
        self.assertEqual(out.upper().count("TOP"), 1)
        self.assertTrue(out.upper().startswith("SELECT TOP 10"))


class TestPreprocessIntegration(unittest.TestCase):
    """End-to-end: the exact statements the models actually emitted."""

    def test_real_qwen_output_with_limit(self):
        """Verbatim from a failing run — correct joins, wrong dialect."""
        sql = """SELECT
    dd.CalendarYear,
    SUM(fis.SalesAmount) AS Revenue,
    SUM(fis.TotalProductCost) AS COGS,
    SUM(fis.SalesAmount - fis.TotalProductCost) AS GrossProfit
FROM
    dbo.FactInternetSales fis
JOIN
    dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey
GROUP BY
    dd.CalendarYear
ORDER BY
    Revenue DESC
LIMIT 20;"""
        out = preprocess_sql(sql)
        self.assertNotIn("LIMIT", out.upper())
        self.assertIn("SELECT TOP 20", out)
        # the rest of the query must survive intact
        self.assertIn("dbo.FactInternetSales fis", out)
        self.assertIn("GROUP BY", out)
        self.assertIn("ORDER BY", out)
        self.assertTrue(out.rstrip().endswith("Revenue DESC"))

    def test_markdown_fence_residue(self):
        out = preprocess_sql("```sql\nSELECT a FROM t\n```")
        self.assertNotIn("`", out)
        self.assertEqual(out, "SELECT a FROM t")

    def test_backtick_identifiers_removed(self):
        out = preprocess_sql("SELECT `col` FROM `table`")
        self.assertNotIn("`", out)

    def test_postgres_cast(self):
        out = preprocess_sql("SELECT fis.SalesAmount::int FROM t")
        self.assertIn("CAST(fis.SalesAmount AS int)", out)
        self.assertNotIn("::", out)

    def test_ifnull_and_now(self):
        out = preprocess_sql("SELECT IFNULL(a, 0), NOW() FROM t")
        self.assertIn("ISNULL(", out)
        self.assertIn("GETDATE()", out)
        self.assertNotIn("IFNULL", out.upper())

    def test_top_at_end_relocated(self):
        out = preprocess_sql("SELECT a FROM t ORDER BY a DESC TOP 10")
        self.assertTrue(out.upper().startswith("SELECT TOP 10"))
        self.assertEqual(out.upper().count("TOP"), 1)

    def test_valid_tsql_passes_through_unchanged(self):
        """No false positives on already-correct SQL."""
        good = ("SELECT TOP 20 dpc.EnglishProductCategoryName, SUM(fis.SalesAmount) AS Revenue "
                "FROM dbo.FactInternetSales fis "
                "JOIN dbo.DimProduct dp ON fis.ProductKey = dp.ProductKey "
                "GROUP BY dpc.EnglishProductCategoryName ORDER BY Revenue DESC")
        self.assertEqual(preprocess_sql(good), good)

    def test_idempotent(self):
        """Running the sanitiser twice must not change the result."""
        sql = "SELECT a FROM t ORDER BY a DESC LIMIT 20;"
        once = preprocess_sql(sql)
        self.assertEqual(preprocess_sql(once), once)

    def test_empty_input(self):
        self.assertEqual(preprocess_sql(""), "")

    def test_cte_with_limit(self):
        sql = ("WITH x AS (SELECT a FROM t) "
               "SELECT a FROM x ORDER BY a DESC LIMIT 5")
        out = preprocess_sql(sql)
        self.assertNotIn("LIMIT", out.upper())
        # TOP must land on the outer SELECT, not inside the CTE
        self.assertRegex(out, r"(?i)\)\s*SELECT TOP 5\b")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestChartDetection(unittest.TestCase):
    """Chart type is inferred from result SHAPE, not requested by the LLM."""

    def _t(self, df):
        from server.database import detect_chart
        c = detect_chart(df, "t")
        return c["type"] if c else None

    def test_few_categories_one_metric_is_doughnut(self):
        import pandas as pd
        df = pd.DataFrame({"Category": ["Bikes", "Components", "Clothing", "Accessories"],
                           "Revenue": [28.3, 11.8, 2.1, 1.3]})
        self.assertEqual(self._t(df), "doughnut")

    def test_multiple_metrics_never_doughnut(self):
        """A doughnut renders one series — extra measures would vanish."""
        import pandas as pd
        df = pd.DataFrame({"Category": ["A", "B", "C", "D"],
                           "Revenue": [4.0, 3.0, 2.0, 1.0],
                           "COGS": [2.0, 1.5, 1.0, 0.5]})
        self.assertEqual(self._t(df), "bar")

    def test_negative_values_never_doughnut(self):
        import pandas as pd
        df = pd.DataFrame({"Category": ["A", "B", "C"], "Profit": [5.0, -2.0, 3.0]})
        self.assertEqual(self._t(df), "bar")

    def test_many_categories_is_horizontal_bar(self):
        import pandas as pd
        df = pd.DataFrame({"Subcategory": [f"Sub {i}" for i in range(12)],
                           "Revenue": [float(i) for i in range(12)]})
        self.assertEqual(self._t(df), "horizontal_bar")

    def test_long_labels_go_horizontal(self):
        """Long names are unreadable rotated 45 degrees on an x-axis."""
        import pandas as pd
        df = pd.DataFrame({"Subcategory": [f"Mountain Bike Model {i}" for i in range(4)],
                           "Revenue": [4.0, 3.0, 2.0, 1.0]})
        self.assertEqual(self._t(df), "horizontal_bar")

    def test_time_only_is_line(self):
        import pandas as pd
        df = pd.DataFrame({"CalendarYear": [2011, 2012, 2013], "Revenue": [1.0, 2.0, 3.0]})
        self.assertEqual(self._t(df), "line")

    def test_two_numerics_is_scatter(self):
        import pandas as pd
        df = pd.DataFrame({"OrderQuantity": [1.0, 2.0, 3.0], "SalesAmount": [10.0, 20.0, 30.0]})
        self.assertEqual(self._t(df), "scatter")


SCHEMA_FIXTURE = """=== AdventureWorksDW2019 Schema ===

KEY RELATIONSHIPS:
  dbo.FactInternetSales.ProductKey -> dbo.DimProduct.ProductKey

Table: dbo.FactInternetSales
  - ProductKey (int) [PK]
  - OrderDateKey (int)
  - SalesOrderNumber (nvarchar)
  - SalesAmount (money)
  - TotalProductCost (money)
  - OrderQuantity (smallint)

Table: dbo.DimDate
  - DateKey (int) [PK]
  - FullDateAlternateKey (date)
  - CalendarYear (smallint)
  - EnglishMonthName (nvarchar)
  - MonthNumberOfYear (tinyint)
  - EnglishDayNameOfWeek (nvarchar)
  - DayNumberOfWeek (tinyint)

Table: dbo.DimProduct
  - ProductKey (int) [PK]
  - EnglishProductName (nvarchar)
  - ProductSubcategoryKey (int)

Table: dbo.DimProductCategory
  - ProductCategoryKey (int) [PK]
  - EnglishProductCategoryName (nvarchar)
"""


class TestSchemaAwareRepair(unittest.TestCase):
    """Generalised column repair — works for names never hardcoded anywhere."""

    def setUp(self):
        from server.schema_index import SchemaIndex
        self.idx = SchemaIndex(SCHEMA_FIXTURE)

    def _fix(self, sql):
        return self.idx.fix_columns(sql)[0]

    def test_parses_tables_and_columns(self):
        self.assertIn("dbo.FactInternetSales", self.idx.tables)
        self.assertIn("SalesOrderNumber", self.idx.tables["dbo.FactInternetSales"])

    def test_ordernumber_resolves_by_containment(self):
        """The exact production failure — never hardcoded."""
        out = self._fix("SELECT COUNT(fis.OrderNumber) FROM dbo.FactInternetSales fis")
        self.assertIn("fis.SalesOrderNumber", out)

    def test_english_prefix_resolved_by_normalisation(self):
        out = self._fix("SELECT dpc.CategoryName FROM dbo.DimProductCategory dpc")
        self.assertIn("dpc.EnglishProductCategoryName", out)

    def test_calendar_prefix_resolved(self):
        out = self._fix("SELECT dd.CalendarMonthName FROM dbo.DimDate dd")
        self.assertIn("dd.EnglishMonthName", out)

    def test_valid_columns_untouched(self):
        sql = ("SELECT fis.SalesAmount, dd.CalendarYear "
               "FROM dbo.FactInternetSales fis JOIN dbo.DimDate dd "
               "ON fis.OrderDateKey = dd.DateKey")
        self.assertEqual(self._fix(sql), sql)

    def test_table_qualifiers_untouched(self):
        """dbo.FactInternetSales must never be treated as alias.Column."""
        sql = "SELECT fis.SalesAmount FROM dbo.FactInternetSales fis"
        self.assertIn("dbo.FactInternetSales", self._fix(sql))

    def test_ambiguous_reference_left_alone(self):
        """DayOfWeek matches DayNumberOfWeek AND EnglishDayNameOfWeek equally."""
        sql = "SELECT dd.CalendarDayOfWeek FROM dbo.DimDate dd"
        out = self.idx.fix_columns(sql)[0]
        self.assertEqual(out, sql)   # refuses to guess

    def test_unknown_column_left_for_llm(self):
        sql = "SELECT fis.TotallyMadeUpThing FROM dbo.FactInternetSales fis"
        self.assertEqual(self._fix(sql), sql)

    def test_alias_scoping_prefers_own_table(self):
        """ProductKey exists on two tables; the alias decides which."""
        sql = ("SELECT dp.ProductKey FROM dbo.DimProduct dp")
        self.assertEqual(self._fix(sql), sql)

    def test_repair_is_noop_without_schema(self):
        """Database offline -> sanitiser must not crash or alter the SQL."""
        import server.database as db
        saved = db._schema_cache
        try:
            db._schema_cache = None
            sql = "SELECT fis.OrderNumber FROM dbo.FactInternetSales fis"
            self.assertEqual(db.repair_columns(sql), sql)
        finally:
            db._schema_cache = saved


class TestGenericColumnResolution(unittest.TestCase):
    """_COL_FIXES (27 hardcoded regexes) was deleted. These prove the generic
    schema resolver covers the same ground without naming any column."""

    SCHEMA = """=== S ===

Table: dbo.DimProduct
  - EnglishProductName (nvarchar)

Table: dbo.DimDate
  - EnglishMonthName (nvarchar)
  - MonthNumberOfYear (tinyint)
  - DayNumberOfWeek (tinyint)
  - EnglishDayNameOfWeek (nvarchar)
  - FullDateAlternateKey (date)
"""

    def setUp(self):
        import server.database as db
        self.db = db
        self._saved = db._schema_cache
        db._schema_cache = self.SCHEMA
        db._schema_index = None

    def tearDown(self):
        self.db._schema_cache = self._saved
        self.db._schema_index = None

    def _fix(self, sql):
        return self.db.preprocess_sql(sql)

    def test_english_prefix(self):
        self.assertIn("dp.EnglishProductName",
                      self._fix("SELECT dp.ProductName FROM dbo.DimProduct dp"))

    def test_calendar_prefix(self):
        self.assertIn("dd.EnglishMonthName",
                      self._fix("SELECT dd.CalendarMonthName FROM dbo.DimDate dd"))

    def test_number_and_name_stay_distinct(self):
        """MonthNumber must not resolve to the month NAME.

        Normalisation previously stripped 'number' and 'name', collapsing
        MonthNumberOfYear and EnglishMonthName to the same key — a wrong
        answer that runs cleanly.
        """
        out = self._fix("SELECT dd.MonthNumber FROM dbo.DimDate dd")
        self.assertIn("dd.MonthNumberOfYear", out)
        self.assertNotIn("EnglishMonthName", out)

    def test_date_column(self):
        self.assertIn("dd.FullDateAlternateKey",
                      self._fix("SELECT dd.FullDate FROM dbo.DimDate dd"))

    def test_genuinely_ambiguous_left_alone(self):
        """DayOfWeek matches DayNumberOfWeek and EnglishDayNameOfWeek equally,
        so it is left for the LLM rather than guessed."""
        sql = "SELECT dd.CalendarDayOfWeek FROM dbo.DimDate dd"
        self.assertIn("CalendarDayOfWeek", self._fix(sql))
