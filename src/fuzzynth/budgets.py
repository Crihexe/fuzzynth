"""Durable, fail-closed multidimensional campaign budget reservations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import os
from pathlib import Path
import sqlite3
import tomllib
import uuid

from fuzzynth.accounting import TokenUsage, UsageAccountingError


class BudgetConfigurationError(RuntimeError):
    """A budget policy is missing or cannot safely authorize work."""


class BudgetLimitError(RuntimeError):
    """A reservation would exceed at least one hard budget dimension."""

    def __init__(self, blocked_by: tuple[str, ...]):
        super().__init__("budget reservation blocked: " + ", ".join(blocked_by))
        self.blocked_by = blocked_by


@dataclass(frozen=True, slots=True)
class TokenRates:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal


@dataclass(frozen=True, slots=True)
class MeterPolicy:
    meter_id: str
    unit: str
    metered: bool
    input_per_million: Decimal = Decimal(0)
    cached_input_per_million: Decimal = Decimal(0)
    output_per_million: Decimal = Decimal(0)
    hard_total_microunits: int | None = None
    hard_uncached_input_tokens: int | None = None
    hard_cached_input_tokens: int | None = None
    hard_output_tokens: int | None = None
    historical_reserve_microunits: int = 0
    pricing_profiles: Mapping[str, TokenRates] = field(default_factory=dict)
    legacy_meter_ids: tuple[str, ...] = ()

    def rates(self, pricing_profile: str | None = None) -> TokenRates:
        if pricing_profile is None:
            return TokenRates(
                input_per_million=self.input_per_million,
                cached_input_per_million=self.cached_input_per_million,
                output_per_million=self.output_per_million,
            )
        try:
            return self.pricing_profiles[pricing_profile]
        except KeyError as exc:
            raise BudgetConfigurationError(
                f"unknown pricing profile {self.meter_id}.{pricing_profile}"
            ) from exc

    def charge(
        self,
        *,
        uncached_input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        pricing_profile: str | None = None,
    ) -> int:
        if not self.metered:
            return 0
        rates = self.rates(pricing_profile)
        amount = (
            Decimal(uncached_input_tokens) * rates.input_per_million
            + Decimal(cached_input_tokens) * rates.cached_input_per_million
            + Decimal(output_tokens) * rates.output_per_million
        ) / Decimal(1_000_000)
        return int(
            (amount * Decimal(1_000_000)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )


@dataclass(frozen=True, slots=True)
class RequestCaps:
    wall_seconds: float
    max_response_bytes: int
    max_program_bytes: int
    max_feedback_bytes: int
    max_context_bytes: int


def load_request_caps(path: Path) -> RequestCaps:
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BudgetConfigurationError("unable to load request caps") from exc
    if document.get("schema_version") != 2:
        raise BudgetConfigurationError("unsupported budget configuration version")
    raw = document.get("request_caps")
    if not isinstance(raw, dict):
        raise BudgetConfigurationError("request caps are missing")
    wall_seconds = raw.get("wall_seconds")
    if (
        isinstance(wall_seconds, bool)
        or not isinstance(wall_seconds, (int, float))
        or wall_seconds <= 0
    ):
        raise BudgetConfigurationError("wall_seconds must be positive")

    def positive(name: str) -> int:
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise BudgetConfigurationError(f"{name} must be a positive integer")
        return value

    return RequestCaps(
        wall_seconds=float(wall_seconds),
        max_response_bytes=positive("max_response_bytes"),
        max_program_bytes=positive("max_program_bytes"),
        max_feedback_bytes=positive("max_feedback_bytes"),
        max_context_bytes=positive("max_context_bytes"),
    )


def _optional_nonnegative_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BudgetConfigurationError(f"{name} must be a non-negative integer")
    return value


def load_meter_policies(path: Path) -> dict[str, MeterPolicy]:
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BudgetConfigurationError("unable to load budget configuration") from exc
    if document.get("schema_version") != 2:
        raise BudgetConfigurationError("unsupported budget configuration version")
    configured = document.get("meters")
    if not isinstance(configured, dict):
        raise BudgetConfigurationError("budget configuration has no meters")

    policies: dict[str, MeterPolicy] = {}
    for meter_id, raw in configured.items():
        if not isinstance(raw, dict) or raw.get("enabled", True) is not True:
            continue
        metered = raw.get("metered")
        unit = raw.get("unit")
        if not isinstance(metered, bool) or not isinstance(unit, str) or not unit:
            raise BudgetConfigurationError(f"invalid meter: {meter_id}")

        def decimal_value(
            source: dict[str, object], name: str, label: str
        ) -> Decimal:
            value = source.get(name, "0")
            if not isinstance(value, str):
                raise BudgetConfigurationError(f"{label}.{name} must be a string")
            try:
                parsed = Decimal(value)
            except Exception as exc:
                raise BudgetConfigurationError(
                    f"{label}.{name} is not decimal"
                ) from exc
            if not parsed.is_finite() or parsed < 0:
                raise BudgetConfigurationError(
                    f"{label}.{name} must be finite and non-negative"
                )
            return parsed

        raw_profiles = raw.get("pricing_profiles", {})
        if not isinstance(raw_profiles, dict):
            raise BudgetConfigurationError(
                f"{meter_id}.pricing_profiles must be a table"
            )
        pricing_profiles: dict[str, TokenRates] = {}
        for profile_id, profile in raw_profiles.items():
            if (
                not isinstance(profile_id, str)
                or not profile_id
                or not isinstance(profile, dict)
            ):
                raise BudgetConfigurationError(
                    f"invalid pricing profile for {meter_id}"
                )
            label = f"{meter_id}.pricing_profiles.{profile_id}"
            rates = TokenRates(
                input_per_million=decimal_value(
                    profile, "input_per_million", label
                ),
                cached_input_per_million=decimal_value(
                    profile, "cached_input_per_million", label
                ),
                output_per_million=decimal_value(
                    profile, "output_per_million", label
                ),
            )
            if rates.cached_input_per_million > rates.input_per_million:
                raise BudgetConfigurationError(
                    f"{label} cached input rate exceeds input rate"
                )
            pricing_profiles[profile_id] = rates

        raw_legacy_ids = raw.get("legacy_meter_ids", [])
        if not isinstance(raw_legacy_ids, list) or any(
            not isinstance(item, str) or not item or item == meter_id
            for item in raw_legacy_ids
        ):
            raise BudgetConfigurationError(
                f"{meter_id}.legacy_meter_ids must contain distinct old meter names"
            )
        legacy_meter_ids = tuple(raw_legacy_ids)
        if len(set(legacy_meter_ids)) != len(legacy_meter_ids):
            raise BudgetConfigurationError(
                f"{meter_id}.legacy_meter_ids contains duplicates"
            )

        policy = MeterPolicy(
            meter_id=meter_id,
            unit=unit,
            metered=metered,
            input_per_million=decimal_value(raw, "input_per_million", meter_id),
            cached_input_per_million=decimal_value(
                raw, "cached_input_per_million", meter_id
            ),
            output_per_million=decimal_value(
                raw, "output_per_million", meter_id
            ),
            hard_total_microunits=_optional_nonnegative_int(
                raw.get("hard_total_microunits"),
                f"{meter_id}.hard_total_microunits",
            ),
            hard_uncached_input_tokens=_optional_nonnegative_int(
                raw.get("hard_uncached_input_tokens"),
                f"{meter_id}.hard_uncached_input_tokens",
            ),
            hard_cached_input_tokens=_optional_nonnegative_int(
                raw.get("hard_cached_input_tokens"),
                f"{meter_id}.hard_cached_input_tokens",
            ),
            hard_output_tokens=_optional_nonnegative_int(
                raw.get("hard_output_tokens"),
                f"{meter_id}.hard_output_tokens",
            ),
            historical_reserve_microunits=_optional_nonnegative_int(
                raw.get("historical_reserve_microunits", 0),
                f"{meter_id}.historical_reserve_microunits",
            )
            or 0,
            pricing_profiles=pricing_profiles,
            legacy_meter_ids=legacy_meter_ids,
        )
        if metered and policy.hard_total_microunits is None:
            raise BudgetConfigurationError(
                f"metered policy {meter_id} requires a hard total"
            )
        if policy.cached_input_per_million > policy.input_per_million:
            raise BudgetConfigurationError(
                f"{meter_id} cached input rate exceeds input rate"
            )
        policies[meter_id] = policy
    configured_ids = set(policies)
    claimed_legacy_ids: set[str] = set()
    for policy in policies.values():
        aliases = set(policy.legacy_meter_ids)
        if aliases & configured_ids or aliases & claimed_legacy_ids:
            raise BudgetConfigurationError("budget meter legacy aliases overlap")
        claimed_legacy_ids.update(aliases)
    return policies


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: str
    meter_id: str
    reserved_uncached_input_tokens: int
    reserved_cached_input_tokens: int
    reserved_output_tokens: int
    reserved_microunits: int
    pricing_profile: str | None


@dataclass(frozen=True, slots=True)
class Settlement:
    reservation_id: str
    actual_uncached_input_tokens: int
    actual_cached_input_tokens: int
    actual_output_tokens: int
    actual_microunits: int
    reservation_overrun: bool
    exhausted_by: tuple[str, ...]


_SCHEMA = """
CREATE TABLE reservation (
  id TEXT PRIMARY KEY,
  meter_id TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active', 'settled', 'uncertain', 'released')),
  reserved_uncached_input_tokens INTEGER NOT NULL CHECK(reserved_uncached_input_tokens >= 0),
  reserved_cached_input_tokens INTEGER NOT NULL CHECK(reserved_cached_input_tokens >= 0),
  reserved_output_tokens INTEGER NOT NULL CHECK(reserved_output_tokens >= 0),
  reserved_microunits INTEGER NOT NULL CHECK(reserved_microunits >= 0),
  actual_uncached_input_tokens INTEGER CHECK(actual_uncached_input_tokens >= 0),
  actual_cached_input_tokens INTEGER CHECK(actual_cached_input_tokens >= 0),
  actual_output_tokens INTEGER CHECK(actual_output_tokens >= 0),
  actual_reasoning_tokens INTEGER CHECK(actual_reasoning_tokens >= 0),
  actual_microunits INTEGER CHECK(actual_microunits >= 0),
  pricing_profile TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
) STRICT;
CREATE INDEX reservation_meter_idx ON reservation(meter_id, status, created_at);
PRAGMA user_version = 2;
"""


class BudgetLedger:
    def __init__(self, path: Path, policies: dict[str, MeterPolicy]):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path
        self.policies = policies
        self.connection = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.execute("PRAGMA busy_timeout = 10000")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self.connection.executescript(_SCHEMA)
        elif version == 1:
            with self.connection:
                self.connection.execute(
                    "ALTER TABLE reservation ADD COLUMN pricing_profile TEXT"
                )
                self.connection.execute("PRAGMA user_version = 2")
        elif version != 2:
            self.connection.close()
            raise BudgetConfigurationError("unsupported budget ledger version")
        self._migrate_legacy_meter_ids()

    def _migrate_legacy_meter_ids(self) -> None:
        migrations = tuple(
            (legacy_id, policy.meter_id)
            for policy in self.policies.values()
            for legacy_id in policy.legacy_meter_ids
        )
        if not migrations:
            return
        try:
            with self.connection:
                for legacy_id, meter_id in migrations:
                    self.connection.execute(
                        "UPDATE reservation SET meter_id = ? WHERE meter_id = ?",
                        (meter_id, legacy_id),
                    )
        except sqlite3.Error as exc:
            raise BudgetConfigurationError(
                "budget meter alias migration failed"
            ) from exc

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> BudgetLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _policy(self, meter_id: str) -> MeterPolicy:
        try:
            return self.policies[meter_id]
        except KeyError as exc:
            raise BudgetConfigurationError(f"unknown budget meter: {meter_id}") from exc

    def _totals(self, meter_id: str) -> tuple[int, int, int, int]:
        row = self.connection.execute(
            """
            SELECT
              coalesce(sum(CASE WHEN status = 'settled' THEN actual_uncached_input_tokens
                                WHEN status IN ('active', 'uncertain') THEN reserved_uncached_input_tokens
                                ELSE 0 END), 0),
              coalesce(sum(CASE WHEN status = 'settled' THEN actual_cached_input_tokens
                                WHEN status IN ('active', 'uncertain') THEN reserved_cached_input_tokens
                                ELSE 0 END), 0),
              coalesce(sum(CASE WHEN status = 'settled' THEN actual_output_tokens
                                WHEN status IN ('active', 'uncertain') THEN reserved_output_tokens
                                ELSE 0 END), 0),
              coalesce(sum(CASE WHEN status = 'settled' THEN actual_microunits
                                WHEN status IN ('active', 'uncertain') THEN reserved_microunits
                                ELSE 0 END), 0)
            FROM reservation WHERE meter_id = ?
            """,
            (meter_id,),
        ).fetchone()
        return tuple(int(value) for value in row)

    @staticmethod
    def _blocked(
        policy: MeterPolicy,
        totals: tuple[int, int, int, int],
    ) -> tuple[str, ...]:
        uncached, cached, output, microunits = totals
        checks = (
            ("uncached_input_tokens", uncached, policy.hard_uncached_input_tokens),
            ("cached_input_tokens", cached, policy.hard_cached_input_tokens),
            ("output_tokens", output, policy.hard_output_tokens),
            ("total_microunits", microunits, policy.hard_total_microunits),
        )
        return tuple(
            name
            for name, used, limit in checks
            if limit is not None and used > limit
        )

    def reserve(
        self,
        meter_id: str,
        *,
        campaign_id: str,
        worker_id: str,
        max_input_tokens: int,
        max_output_tokens: int,
        pricing_profile: str | None = None,
    ) -> Reservation:
        for name, value in (
            ("max_input_tokens", max_input_tokens),
            ("max_output_tokens", max_output_tokens),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        policy = self._policy(meter_id)
        policy.rates(pricing_profile)
        reserved_microunits = policy.charge(
            uncached_input_tokens=max_input_tokens,
            cached_input_tokens=0,
            output_tokens=max_output_tokens,
            pricing_profile=pricing_profile,
        )
        reservation = Reservation(
            reservation_id=f"budget-{uuid.uuid4()}",
            meter_id=meter_id,
            # Cache disposition is unknown before the call. Reserve against both
            # token caps, while pricing the expensive uncached case only once.
            reserved_uncached_input_tokens=max_input_tokens,
            reserved_cached_input_tokens=max_input_tokens,
            reserved_output_tokens=max_output_tokens,
            reserved_microunits=reserved_microunits,
            pricing_profile=pricing_profile,
        )
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self._totals(meter_id)
            proposed = (
                current[0] + reservation.reserved_uncached_input_tokens,
                current[1] + reservation.reserved_cached_input_tokens,
                current[2] + reservation.reserved_output_tokens,
                current[3]
                + reservation.reserved_microunits
                + policy.historical_reserve_microunits,
            )
            blocked = self._blocked(policy, proposed)
            if blocked:
                self.connection.rollback()
                raise BudgetLimitError(blocked)
            self.connection.execute(
                """
                INSERT INTO reservation (
                  id, meter_id, campaign_id, worker_id, status,
                  reserved_uncached_input_tokens, reserved_cached_input_tokens,
                  reserved_output_tokens, reserved_microunits,
                  actual_uncached_input_tokens, actual_cached_input_tokens,
                  actual_output_tokens, actual_reasoning_tokens,
                  actual_microunits, pricing_profile, created_at, finished_at
                ) VALUES (
                  ?, ?, ?, ?, 'active', ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL,
                  ?, ?, NULL
                )
                """,
                (
                    reservation.reservation_id,
                    meter_id,
                    campaign_id,
                    worker_id,
                    reservation.reserved_uncached_input_tokens,
                    reservation.reserved_cached_input_tokens,
                    reservation.reserved_output_tokens,
                    reservation.reserved_microunits,
                    reservation.pricing_profile,
                    now,
                ),
            )
            self.connection.commit()
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise BudgetConfigurationError("budget reservation transaction failed") from exc
        return reservation

    def settle(self, reservation_id: str, usage: TokenUsage) -> Settlement:
        if usage.input_tokens is None or usage.output_tokens is None:
            raise UsageAccountingError("provider usage is incomplete")
        cached = usage.cached_input_tokens or 0
        uncached = usage.input_tokens - cached
        row = self.connection.execute(
            """
            SELECT meter_id, status, reserved_uncached_input_tokens,
                   reserved_cached_input_tokens, reserved_output_tokens,
                   reserved_microunits, pricing_profile
            FROM reservation WHERE id = ?
            """,
            (reservation_id,),
        ).fetchone()
        if row is None or row[1] != "active":
            raise BudgetConfigurationError("reservation is missing or not active")
        policy = self._policy(row[0])
        actual_microunits = policy.charge(
            uncached_input_tokens=uncached,
            cached_input_tokens=cached,
            output_tokens=usage.output_tokens,
            pricing_profile=row[6],
        )
        overrun = (
            (
                policy.hard_uncached_input_tokens is not None
                and uncached > row[2]
            )
            or (
                policy.hard_cached_input_tokens is not None
                and cached > row[3]
            )
            or (
                policy.hard_output_tokens is not None
                and usage.output_tokens > row[4]
            )
            or (
                policy.hard_total_microunits is not None
                and actual_microunits > row[5]
            )
        )
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE reservation SET
                      status = 'settled',
                      actual_uncached_input_tokens = ?,
                      actual_cached_input_tokens = ?,
                      actual_output_tokens = ?,
                      actual_reasoning_tokens = ?,
                      actual_microunits = ?,
                      finished_at = ?
                    WHERE id = ? AND status = 'active'
                    """,
                    (
                        uncached,
                        cached,
                        usage.output_tokens,
                        usage.reasoning_tokens,
                        actual_microunits,
                        now,
                        reservation_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise BudgetConfigurationError("budget settlement failed") from exc
        totals = self._totals(policy.meter_id)
        totals = (
            totals[0],
            totals[1],
            totals[2],
            totals[3] + policy.historical_reserve_microunits,
        )
        return Settlement(
            reservation_id=reservation_id,
            actual_uncached_input_tokens=uncached,
            actual_cached_input_tokens=cached,
            actual_output_tokens=usage.output_tokens,
            actual_microunits=actual_microunits,
            reservation_overrun=overrun,
            exhausted_by=self._blocked(policy, totals),
        )

    def _change_active_status(self, reservation_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            cursor = self.connection.execute(
                f"""
                UPDATE reservation SET status = ?, finished_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (status, now, reservation_id),
            )
        if cursor.rowcount != 1:
            raise BudgetConfigurationError("reservation is missing or not active")

    def mark_uncertain(self, reservation_id: str) -> None:
        self._change_active_status(reservation_id, "uncertain")

    def release(self, reservation_id: str) -> None:
        self._change_active_status(reservation_id, "released")

    def status(self, meter_id: str) -> dict[str, int | str | None]:
        policy = self._policy(meter_id)
        uncached, cached, output, microunits = self._totals(meter_id)
        microunits += policy.historical_reserve_microunits
        uncertain = self.connection.execute(
            """
            SELECT count(*) FROM reservation
            WHERE meter_id = ? AND status = 'uncertain'
            """,
            (meter_id,),
        ).fetchone()[0]
        return {
            "meter_id": meter_id,
            "unit": policy.unit,
            "uncached_input_tokens": uncached,
            "cached_input_tokens": cached,
            "output_tokens": output,
            "total_microunits": microunits,
            "hard_total_microunits": policy.hard_total_microunits,
            "hard_uncached_input_tokens": policy.hard_uncached_input_tokens,
            "hard_cached_input_tokens": policy.hard_cached_input_tokens,
            "hard_output_tokens": policy.hard_output_tokens,
            "uncertain_reservations": int(uncertain),
        }
