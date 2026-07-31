"""Tests for refusing questions the schema cannot answer."""

from __future__ import annotations

import unittest

from server.answerability import check_answerable, Unanswerable

SCHEMA = """=== AdventureWorksDW2019 Schema ===

KEY RELATIONSHIPS:
  dbo.FactInternetSales.CustomerKey -> dbo.DimCustomer.CustomerKey

Table: dbo.FactInternetSales
  - CustomerKey (int)
  - ProductKey (int)
  - SalesAmount (money)
  - TotalProductCost (money)
  - OrderQuantity (smallint)

Table: dbo.DimCustomer
  - CustomerKey (int) [PK]
  - FirstName (nvarchar)
  - EnglishOccupation (nvarchar)
  - YearlyIncome (money)
  - Gender (nchar)
  - MaritalStatus (nchar)
  - CommuteDistance (nvarchar)

Table: dbo.DimSalesTerritory
  - SalesTerritoryKey (int) [PK]
  - SalesTerritoryRegion (nvarchar)
  - SalesTerritoryCountry (nvarchar)

Table: dbo.DimDate
  - DateKey (int) [PK]
  - CalendarYear (smallint)
  - EnglishMonthName (nvarchar)
"""


class TestRefusals(unittest.TestCase):
    """Verbatim questions that previously returned confident wrong answers."""

    def _refuses(self, question):
        with self.assertRaises(Unanswerable) as ctx:
            check_answerable(question, SCHEMA)
        return ctx.exception

    def test_age_group_refused(self):
        e = self._refuses("Break down sales by customer age group")
        self.assertEqual(e.concept, "age group")
        self.assertIn("no age group data", e.message)

    def test_payment_method_refused(self):
        e = self._refuses("What are sales by payment method?")
        self.assertEqual(e.concept, "payment method")

    def test_returns_refused(self):
        self._refuses("Show me net revenue after returns by territory")

    def test_store_location_refused(self):
        self._refuses("Show revenue by store location")

    def test_refusal_lists_available_attributes(self):
        """A refusal must be actionable, not just a dead end."""
        e = self._refuses("Break down sales by customer age group")
        self.assertIn("EnglishOccupation", e.message)
        self.assertIn("Gender", e.message)


class TestAllowed(unittest.TestCase):
    """Answerable questions must never be blocked."""

    def _allows(self, question):
        try:
            check_answerable(question, SCHEMA)
        except Unanswerable as e:
            self.fail(f"wrongly refused {question!r}: {e.message}")

    def test_territory(self):
        self._allows("Show sales by territory region")

    def test_occupation_segment(self):
        """'segment' must resolve to EnglishOccupation, not be refused."""
        self._allows("What is revenue by customer segment?")

    def test_plain_totals(self):
        self._allows("Show total sales by year")

    def test_profit_is_derivable(self):
        """Profit is not a column but is computable from amount and cost."""
        self._allows("Show gross profit by month")

    def test_income_exists(self):
        self._allows("Show sales by customer income")

    def test_gender_exists(self):
        self._allows("Break down revenue by gender")


class TestSafety(unittest.TestCase):

    def test_empty_inputs_never_raise(self):
        check_answerable("", SCHEMA)
        check_answerable("anything", "")

    def test_unparseable_schema_does_not_block(self):
        """If the schema cannot be read, allow through rather than refuse."""
        check_answerable("Break down sales by customer age group", "garbage")

    def test_concept_present_in_other_schema(self):
        """The same question is answerable against a schema that has the data."""
        schema = SCHEMA + "\nTable: dbo.DimPayment\n  - PaymentMethod (nvarchar)\n"
        check_answerable("What are sales by payment method?", schema)


if __name__ == "__main__":
    unittest.main(verbosity=2)
