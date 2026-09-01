"""Provider-neutral token cost accounting and fail-closed budget decisions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING


class UsageAccountingError(RuntimeError):
    """Usage or pricing is insufficient for safe cost accounting."""


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
        ):
            value = getattr(self, field_name)
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer")
        if (
            self.input_tokens is not None
            and self.cached_input_tokens is not None
            and self.cached_input_tokens > self.input_tokens
        ):
            raise ValueError("cached input tokens cannot exceed input tokens")
        if (
            self.output_tokens is not None
            and self.reasoning_tokens is not None
            and self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError("reasoning tokens cannot exceed output tokens")


@dataclass(frozen=True, slots=True)
class PriceSchedule:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.currency != "USD":
            raise ValueError("only explicitly configured USD schedules are supported")
        for field_name in (
            "input_per_million",
            "cached_input_per_million",
            "output_per_million",
        ):
            value = getattr(self, field_name)
            if value < 0 or not value.is_finite():
                raise ValueError(f"{field_name} must be finite and non-negative")
        if self.cached_input_per_million > self.input_per_million:
            raise ValueError("cached input price cannot exceed normal input price")


@dataclass(frozen=True, slots=True)
class CostEstimate:
    amount: Decimal
    microusd_ceiling: int
    conservative: bool


def calculate_cost(usage: TokenUsage, prices: PriceSchedule) -> CostEstimate:
    """Calculate cost without double-counting reasoning included in output."""

    if usage.input_tokens is None or usage.output_tokens is None:
        raise UsageAccountingError("provider usage is incomplete")
    conservative = usage.cached_input_tokens is None
    cached_tokens = usage.cached_input_tokens or 0
    uncached_tokens = usage.input_tokens - cached_tokens
    million = Decimal(1_000_000)
    amount = (
        Decimal(uncached_tokens) * prices.input_per_million
        + Decimal(cached_tokens) * prices.cached_input_per_million
        + Decimal(usage.output_tokens) * prices.output_per_million
    ) / million
    microusd = int((amount * million).to_integral_value(rounding=ROUND_CEILING))
    return CostEstimate(
        amount=amount,
        microusd_ceiling=microusd,
        conservative=conservative,
    )


@dataclass(frozen=True, slots=True)
class BudgetWindow:
    name: str
    used_microusd: int
    reserved_microusd: int
    hard_limit_microusd: int

    def __post_init__(self) -> None:
        for field_name in (
            "used_microusd",
            "reserved_microusd",
            "hard_limit_microusd",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    blocked_by: tuple[str, ...]


def authorize_reservation(
    windows: tuple[BudgetWindow, ...], requested_microusd: int
) -> BudgetDecision:
    if isinstance(requested_microusd, bool) or requested_microusd < 0:
        raise ValueError("requested_microusd must be a non-negative integer")
    if not windows:
        return BudgetDecision(allowed=False, blocked_by=("missing_budget_window",))
    blocked = tuple(
        window.name
        for window in windows
        if window.used_microusd
        + window.reserved_microusd
        + requested_microusd
        > window.hard_limit_microusd
    )
    return BudgetDecision(allowed=not blocked, blocked_by=blocked)
