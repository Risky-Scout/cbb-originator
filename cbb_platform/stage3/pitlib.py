"""Shared point-in-time machinery.

A STATE stream is one row per (entity, season, source game) ordered by `available_at` (= the source game's
historical_knowable_at). Each row holds sufficient statistics AFTER that source game: season-to-date sums, rolling
last-N sums and exponentially weighted sums. A feature row is the ASOF match of a target's prediction_cutoff_utc to
the latest state with available_at <= cutoff (`attach`). The target game's own state has available_at = tip + 4 h >
cutoff, so it can never be matched; nor can any later game.
"""
from __future__ import annotations

import math

from cbb_platform.stage3 import spec

R = 0.5 ** (1.0 / spec.EWM_HALF_LIFE_GAMES)          # per-game decay; weight of a game k back = R ** k


def w(part: str) -> str:
    return f"(PARTITION BY {part} ORDER BY available_at, source_tip, source_game_id)"


def std(col: str, part: str) -> str:
    return f"sum({col}) OVER (PARTITION BY {part} ORDER BY available_at, source_tip, source_game_id ROWS UNBOUNDED PRECEDING)"


def roll(col: str, part: str, n: int) -> str:
    return (f"sum({col}) OVER (PARTITION BY {part} ORDER BY available_at, source_tip, source_game_id "
            f"ROWS BETWEEN {n - 1} PRECEDING AND CURRENT ROW)")


def ewm(col: str, part: str, idx: str = "k") -> str:
    """sum_i col_i * R**(k - i) over the season so far (k = this row's 1-based index). Stable: R**(-k) <= 2**(k/5)."""
    return f"(pow({R}, {idx}) * {std(f'({col}) * pow({1.0 / R}, {idx})', part)})"


def attach(con, target: str, state: str, keys: tuple[str, ...], out: str, select: str | None = None) -> None:
    """out = target ASOF LEFT JOIN state on keys and target.prediction_cutoff_utc >= state.available_at."""
    dup = con.execute(f"""SELECT count(*) FROM (SELECT {', '.join(keys)}, available_at FROM {state}
                          GROUP BY ALL HAVING count(*) > 1)""").fetchone()[0]
    if dup:
        raise RuntimeError(f"{state}: {dup} non-unique (entity, available_at) states -- ASOF would be ambiguous")
    on = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    select = select or f"s.* EXCLUDE ({', '.join(keys)})"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE {out} AS
        SELECT t.*, {select} FROM {target} t ASOF LEFT JOIN {state} s
        ON {on} AND t.prediction_cutoff_utc >= s.available_at""")


def ratio(num: str, den: str, scale: float = 1.0) -> str:
    return f"CASE WHEN ({den}) > 0 THEN {scale} * ({num}) / ({den}) END"


assert math.isclose(R ** spec.EWM_HALF_LIFE_GAMES, 0.5)
