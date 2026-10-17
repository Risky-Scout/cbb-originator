"""Eligibility manifests (docs/MODEL_ELIGIBILITY_POLICY.md §4): every row hash is recomputed from the frozen rule and must
equal the committed manifest (gate 2); the rule SQL equals the rows the frozen loaders hand to fit (gate 3, asserted
inside build); no lockbox row, every exclusion counted, deterministic output."""
from __future__ import annotations

import json

import pytest

from cbb_platform import eligibility_manifests as M, lockbox


@pytest.fixture(scope="module")
def built():
    return M.build()


def test_required_manifests_present_and_not_required_listed(built):
    assert set(built) == {"ELIG_PARTICIPATION", "ELIG_MINUTES", "ELIG_BOX_COUNTS"}
    idx = json.loads((M.OUT_DIR / "INDEX.json").read_text())
    assert set(idx["required_for_v1"]) == set(built) and set(idx["not_required_for_v1"]) == set(M.NOT_REQUIRED)
    assert idx == json.loads(M.dumps(M.index(built)))


def test_committed_manifests_reproduce_byte_identically(built):
    for mid, man in built.items():
        assert (M.OUT_DIR / f"{mid}.json").read_text() == M.dumps(man), mid
        assert (M.OUT_DIR / f"{mid}.md").read_text() == M.render(man), mid


def test_deterministic_regeneration(built):
    again = M.build()
    assert {k: M.dumps(v) for k, v in again.items()} == {k: M.dumps(v) for k, v in built.items()}


def test_no_lockbox_rows_and_counts_consistent(built):
    for mid, man in built.items():
        assert lockbox.LOCKBOX_SEASON not in man["seasons"] and not man["lockbox_season_present"]
        assert max(man["seasons"]) == lockbox.LAST_DEVELOPMENT_SEASON
        assert sum(man["sample_counts"]["by_season"].values()) == man["eligible_player_games"]["count"]
        assert man["pit_fidelity_requirement"]["prohibited_classes_admitted"] == []
        assert man["dataset_version"] == "cbb_data_v2.4+5f19d31e524a"
    assert built["ELIG_PARTICIPATION"]["seasons"] == [2024, 2025]
    assert built["ELIG_MINUTES"]["seasons"] == list(range(2014, 2026))
    assert built["ELIG_BOX_COUNTS"]["eligible_player_games"]["count"] <= built["ELIG_MINUTES"]["eligible_player_games"]["count"]


def test_every_exclusion_is_counted(built):
    """Complete accounting per season: eligible + every counted exclusion = every pregame label row plus every target box
    row outside the pregame universe or without a resolved identity (nothing disappears unexplained)."""
    con = M._con()
    total = dict(con.execute("""SELECT season, sum(n) FROM (
            SELECT season, count(*) AS n FROM lb.lab.player_game_labels GROUP BY 1
            UNION ALL SELECT season, count(*) FROM lb.lab.target_box_rows_outside_universe GROUP BY 1
            UNION ALL SELECT season, count(*) FROM lb.lab.target_box_rows_unresolved GROUP BY 1)
        WHERE season BETWEEN 2014 AND 2025 GROUP BY 1""").fetchall())
    labels = dict(con.execute("SELECT season, count(*) FROM lb.lab.player_game_labels WHERE season BETWEEN 2014 AND 2025 GROUP BY 1").fetchall())
    con.close()
    for mid in ("ELIG_MINUTES", "ELIG_BOX_COUNTS"):
        man = built[mid]
        assert set(man["exclusions_report"]) == {str(s) for s in range(2014, 2026)}
        for s in range(2014, 2026):
            n = man["sample_counts"]["by_season"].get(str(s), 0) + sum(man["exclusions_report"][str(s)].values())
            assert n == total[s], (mid, s)
    p = built["ELIG_PARTICIPATION"]
    for s in range(2014, 2026):
        n = p["sample_counts"]["by_season"].get(str(s), 0) + sum(p["exclusions_report"][str(s)].values())
        assert n == labels[s], s
    assert all(k.startswith("EXCLUDED_SEASON_BEFORE") for s in range(2014, 2024) for k in p["exclusions_report"][str(s)])
