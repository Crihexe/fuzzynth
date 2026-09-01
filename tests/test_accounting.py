from __future__ import annotations

from decimal import Decimal
import unittest

from fuzzynth.accounting import (
    BudgetWindow,
    PriceSchedule,
    TokenUsage,
    UsageAccountingError,
    authorize_reservation,
    calculate_cost,
)


class CostAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prices = PriceSchedule(
            input_per_million=Decimal("2.00"),
            cached_input_per_million=Decimal("0.50"),
            output_per_million=Decimal("8.00"),
        )

    def test_calculates_cached_and_output_cost_without_reasoning_duplication(self) -> None:
        result = calculate_cost(
            TokenUsage(
                input_tokens=1_000_000,
                cached_input_tokens=250_000,
                output_tokens=500_000,
                reasoning_tokens=400_000,
            ),
            self.prices,
        )

        self.assertEqual(result.amount, Decimal("5.625"))
        self.assertEqual(result.microusd_ceiling, 5_625_000)
        self.assertFalse(result.conservative)

    def test_missing_cached_count_uses_conservative_uncached_price(self) -> None:
        result = calculate_cost(
            TokenUsage(input_tokens=10, output_tokens=1), self.prices
        )

        self.assertTrue(result.conservative)
        self.assertEqual(result.microusd_ceiling, 28)

    def test_missing_primary_usage_fails_closed(self) -> None:
        with self.assertRaisesRegex(UsageAccountingError, "incomplete"):
            calculate_cost(
                TokenUsage(input_tokens=10, output_tokens=None), self.prices
            )

    def test_rejects_impossible_usage(self) -> None:
        with self.assertRaisesRegex(ValueError, "reasoning"):
            TokenUsage(input_tokens=1, output_tokens=2, reasoning_tokens=3)


class BudgetTests(unittest.TestCase):
    def test_all_windows_must_fit_reservation(self) -> None:
        decision = authorize_reservation(
            (
                BudgetWindow("hour", 40, 10, 100),
                BudgetWindow("day", 900, 50, 1_000),
            ),
            requested_microusd=60,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.blocked_by, ("hour", "day"))

    def test_missing_windows_fails_closed(self) -> None:
        decision = authorize_reservation((), requested_microusd=1)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.blocked_by, ("missing_budget_window",))


if __name__ == "__main__":
    unittest.main()
