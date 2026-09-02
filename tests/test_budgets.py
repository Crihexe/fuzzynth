from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from fuzzynth.accounting import TokenUsage, UsageAccountingError
from fuzzynth.budgets import (
    BudgetLedger,
    BudgetLimitError,
    MeterPolicy,
    load_meter_policies,
    load_request_caps,
)


class BudgetConfigurationTests(unittest.TestCase):
    def test_loads_owner_budget_meters(self) -> None:
        policies = load_meter_policies(Path("config/budgets.toml"))

        self.assertFalse(policies["spark_alternate"].metered)
        self.assertEqual(
            policies["luna_alternate"].hard_total_microunits,
            1_250_000_000,
        )
        self.assertEqual(
            policies["luna_alternate"].hard_output_tokens,
            42_000_000,
        )
        self.assertEqual(
            policies["luna_official"].hard_total_microunits,
            4_900_000,
        )
        self.assertNotIn("terra_alternate", policies)

    def test_loads_bounded_complete_response_request_caps(self) -> None:
        caps = load_request_caps(Path("config/budgets.toml"))

        self.assertEqual(caps.wall_seconds, 300)
        self.assertEqual(caps.max_response_bytes, 16_777_216)
        self.assertEqual(caps.max_program_bytes, 524_288)
        self.assertEqual(caps.max_context_bytes, 131_072)


class BudgetLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.policy = MeterPolicy(
            meter_id="test",
            unit="credit",
            metered=True,
            input_per_million=Decimal("5"),
            cached_input_per_million=Decimal("0.5"),
            output_per_million=Decimal("30"),
            hard_total_microunits=40_000_000,
            hard_uncached_input_tokens=2_000_000,
            hard_cached_input_tokens=20_000_000,
            hard_output_tokens=1_000_000,
        )
        self.ledger = BudgetLedger(
            Path(self.temporary.name) / "budget.sqlite3",
            {"test": self.policy},
        )

    def tearDown(self) -> None:
        self.ledger.close()
        self.temporary.cleanup()

    def reserve(self, *, output: int = 100_000):
        return self.ledger.reserve(
            "test",
            campaign_id="campaign",
            worker_id="worker",
            max_input_tokens=100_000,
            max_output_tokens=output,
        )

    def test_reserves_worst_case_and_settles_actual_categories(self) -> None:
        reservation = self.reserve()
        before = self.ledger.status("test")
        self.assertEqual(before["uncached_input_tokens"], 100_000)
        self.assertEqual(before["cached_input_tokens"], 100_000)

        settlement = self.ledger.settle(
            reservation.reservation_id,
            TokenUsage(
                input_tokens=80_000,
                cached_input_tokens=60_000,
                output_tokens=10_000,
                reasoning_tokens=8_000,
            ),
        )

        self.assertEqual(settlement.actual_uncached_input_tokens, 20_000)
        self.assertEqual(settlement.actual_cached_input_tokens, 60_000)
        self.assertEqual(settlement.actual_microunits, 430_000)
        self.assertFalse(settlement.reservation_overrun)

    def test_blocks_when_any_hard_dimension_would_be_exceeded(self) -> None:
        with self.assertRaises(BudgetLimitError) as raised:
            self.ledger.reserve(
                "test",
                campaign_id="campaign",
                worker_id="worker",
                max_input_tokens=100_000,
                max_output_tokens=1_000_001,
            )

        self.assertIn("output_tokens", raised.exception.blocked_by)

    def test_unknown_usage_keeps_full_reservation(self) -> None:
        reservation = self.reserve()
        self.ledger.mark_uncertain(reservation.reservation_id)

        status = self.ledger.status("test")
        self.assertEqual(status["output_tokens"], 100_000)
        self.assertEqual(status["uncertain_reservations"], 1)

    def test_incomplete_usage_cannot_be_settled(self) -> None:
        reservation = self.reserve()

        with self.assertRaises(UsageAccountingError):
            self.ledger.settle(
                reservation.reservation_id,
                TokenUsage(input_tokens=None, output_tokens=10),
            )

    def test_released_pre_dispatch_reservation_does_not_count(self) -> None:
        reservation = self.reserve()
        self.ledger.release(reservation.reservation_id)

        status = self.ledger.status("test")
        self.assertEqual(status["output_tokens"], 0)
        self.assertEqual(status["total_microunits"], 0)


if __name__ == "__main__":
    unittest.main()
