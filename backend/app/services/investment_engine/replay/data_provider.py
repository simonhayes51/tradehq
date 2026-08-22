# app/services/investment_engine/replay/data_provider.py
"""
The structural no-lookahead guard for the FC26 replay harness.

ReplayDataProvider holds the FULL historical dataset privately (every
card's entire price series, position group) but every read method takes
an `as_of` date and can only ever return observations strictly at-or-
before it. The orchestrator (engine.py) never touches the underlying
dict directly - it only ever goes through this class, so "can the
replay accidentally see tomorrow's price" reduces to "does this one
class have a bug", not "did every call site remember to slice
correctly". test_investment_replay.py's leakage tests assert this
class's methods directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CardMeta:
    card_id: int
    name: str
    primary_position: Optional[str]


class LookaheadError(RuntimeError):
    """Raised if calling code somehow asks for data at/after the replay
    clock - should never happen through the public accessors below, but
    exists as a hard failure rather than a silently-wrong result if a
    future edit introduces a boundary bug."""


class ReplayDataProvider:
    def __init__(
        self,
        *,
        prices: Dict[int, Dict[date, float]],
        meta: Dict[int, CardMeta],
        label: str,
    ):
        # Leading underscore: not "private by convention" here, but the
        # literal thing every other method in this class exists to
        # gatekeep access to. Nothing outside this class may read these
        # two dicts directly.
        self._prices = {cid: dict(series) for cid, series in prices.items()}
        self._meta = dict(meta)
        self.label = label  # human-readable data-source description, surfaced in the report
        self._all_dates = sorted({d for series in self._prices.values() for d in series})

    @property
    def all_dates(self) -> Sequence[date]:
        return tuple(self._all_dates)

    @property
    def all_card_ids(self) -> Sequence[int]:
        return tuple(self._prices.keys())

    def meta(self, card_id: int) -> Optional[CardMeta]:
        return self._meta.get(card_id)

    def first_observed_date(self, card_id: int) -> Optional[date]:
        series = self._prices.get(card_id)
        if not series:
            return None
        return min(series)

    def price_history_up_to(self, card_id: int, as_of: date, *, inclusive: bool = True) -> List[Tuple[date, float]]:
        """Every observation for `card_id` at-or-before `as_of`
        (inclusive) or strictly-before (inclusive=False), chronological.
        This is the ONLY way engine.py ever reads a card's prices."""
        series = self._prices.get(card_id, {})
        if inclusive:
            items = [(d, p) for d, p in series.items() if d <= as_of]
        else:
            items = [(d, p) for d, p in series.items() if d < as_of]
        return sorted(items, key=lambda dp: dp[0])

    def price_on(self, card_id: int, as_of: date) -> Optional[float]:
        series = self._prices.get(card_id, {})
        return series.get(as_of)

    def cards_first_available_on_or_before(self, as_of: date) -> List[int]:
        """Every card_id whose EARLIEST observation is at-or-before
        `as_of` - i.e. every card the replay clock could plausibly know
        about right now. A card whose first price is next week is not
        yet in scope, exactly matching the bootstrap requirement that
        the engine must not assume more launch history than genuinely
        existed at that point."""
        out = []
        for cid, series in self._prices.items():
            first = min(series) if series else None
            if first is not None and first <= as_of:
                out.append(cid)
        return sorted(out)

    def assert_no_future_access(self, card_id: int, as_of: date, observed_dates: Sequence[date]) -> None:
        """Defensive self-check callable from tests/engine.py: raises if
        any date in `observed_dates` is at/after `as_of`."""
        bad = [d for d in observed_dates if d >= as_of]
        if bad:
            raise LookaheadError(f"card_id={card_id} as_of={as_of} saw future dates: {bad}")
