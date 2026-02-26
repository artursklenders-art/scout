from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZonePlan:
    side: str
    setup_label: str
    entry_low: float
    entry_high: float
    invalidation: float
    first_target_primary: float
    first_target_secondary: float | None = None
    first_target_is_range: bool = False


@dataclass(frozen=True)
class PriceLevels:
    last_price: float
    prev_close: float
    prev_high: float
    prev_low: float


def make_price_levels(last_price: float, prev_close: float, prev_high: float, prev_low: float) -> PriceLevels:
    return PriceLevels(
        last_price=_r(last_price),
        prev_close=_r(prev_close),
        prev_high=_r(prev_high),
        prev_low=_r(prev_low),
    )


def build_long_plan(levels: PriceLevels) -> ZonePlan:
    if levels.last_price <= levels.prev_close:
        return long_reclaim_prev_close(levels)
    return long_pullback_into_support(levels)


def build_short_plan(levels: PriceLevels) -> ZonePlan:
    buffer_size = _buffer(levels)
    near_prev_high = levels.last_price >= (levels.prev_high - 0.30 * buffer_size)
    if near_prev_high:
        return short_reject_prev_high(levels)
    return short_breakdown_below_prev_close(levels)


def long_reclaim_prev_close(levels: PriceLevels) -> ZonePlan:
    buf = _buffer(levels)
    entry_low = _r(levels.prev_close - 0.20 * buf)
    entry_high = _r(levels.prev_close + 0.40 * buf)
    invalidation = _r(levels.prev_low - 0.20 * buf)
    target_low = _r(levels.prev_high - 0.20 * buf)
    target_high = _r(levels.prev_high + 0.20 * buf)

    return ZonePlan(
        side="Long",
        setup_label="Reclaim prev close",
        entry_low=min(entry_low, entry_high),
        entry_high=max(entry_low, entry_high),
        invalidation=invalidation,
        first_target_primary=min(target_low, target_high),
        first_target_secondary=max(target_low, target_high),
        first_target_is_range=True,
    )


def long_pullback_into_support(levels: PriceLevels) -> ZonePlan:
    buf = _buffer(levels)
    anchor = max(levels.prev_close, levels.last_price)
    entry_low = _r(anchor - 1.20 * buf)
    entry_high = _r(anchor - 0.40 * buf)
    invalidation = _r(levels.prev_low - 0.20 * buf)
    target_one = _r(levels.prev_close + 0.80 * buf)
    target_two = _r(levels.prev_high)

    return ZonePlan(
        side="Long",
        setup_label="Pullback into support",
        entry_low=min(entry_low, entry_high),
        entry_high=max(entry_low, entry_high),
        invalidation=invalidation,
        first_target_primary=target_one,
        first_target_secondary=target_two,
        first_target_is_range=False,
    )


def short_reject_prev_high(levels: PriceLevels) -> ZonePlan:
    buf = _buffer(levels)
    entry_low = _r(levels.prev_high - 0.40 * buf)
    entry_high = _r(levels.prev_high + 0.40 * buf)
    invalidation = _r(levels.prev_high + 1.20 * buf)
    target_one = _r(levels.prev_close)
    target_two = _r(levels.prev_low + 0.20 * buf)

    return ZonePlan(
        side="Short",
        setup_label="Reject prev high",
        entry_low=min(entry_low, entry_high),
        entry_high=max(entry_low, entry_high),
        invalidation=invalidation,
        first_target_primary=target_one,
        first_target_secondary=target_two,
        first_target_is_range=False,
    )


def short_breakdown_below_prev_close(levels: PriceLevels) -> ZonePlan:
    buf = _buffer(levels)
    entry_low = _r(levels.prev_close - 0.40 * buf)
    entry_high = _r(levels.prev_close + 0.20 * buf)
    invalidation = _r(levels.prev_close + 1.00 * buf)
    target_one = _r(levels.prev_low + 0.20 * buf)

    return ZonePlan(
        side="Short",
        setup_label="Breakdown below prev close",
        entry_low=min(entry_low, entry_high),
        entry_high=max(entry_low, entry_high),
        invalidation=invalidation,
        first_target_primary=target_one,
        first_target_secondary=None,
        first_target_is_range=False,
    )


def format_range(low: float, high: float) -> str:
    return f"{_r(low):.2f}-{_r(high):.2f}"


def format_target(plan: ZonePlan) -> str:
    if plan.first_target_secondary is None:
        return f"{_r(plan.first_target_primary):.2f}"

    if plan.first_target_is_range:
        low = min(plan.first_target_primary, plan.first_target_secondary)
        high = max(plan.first_target_primary, plan.first_target_secondary)
        return f"{low:.2f}-{high:.2f}"

    return f"{_r(plan.first_target_primary):.2f} then {_r(plan.first_target_secondary):.2f}"


def _buffer(levels: PriceLevels) -> float:
    price_component = _r(levels.last_price * 0.0015)
    range_component = _r((levels.prev_high - levels.prev_low) * 0.10)
    return max(price_component, range_component)


def _r(value: float) -> float:
    return round(float(value), 2)
