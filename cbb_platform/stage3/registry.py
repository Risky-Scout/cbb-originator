"""Stage 3 section 14: modeling/FEATURE_REGISTRY.json -- machine-readable metadata for EVERY feature column.

Columns of the feature tables are classified as KEY (identifiers, cutoff, versions), PROVENANCE (state source game /
available_at), STATUS (semantic missingness per family) or FEATURE. Every FEATURE gets a family by its controlled
prefix and a description / unit generated from a controlled naming grammar; a name the grammar cannot describe
raises (no anonymous feature columns).
"""
from __future__ import annotations

import json
import re

from cbb_platform.stage3 import event_stats, lineup_stats, player_state, spec, team_state

FEATURE_TABLES = ("feat.team_features", "feat.player_features", "feat.team_event_features", "feat.player_shot_features",
                  "feat.player_lineup_features")
KEYS = {"game_example_id", "player_game_example_id", "canonical_game_id", "canonical_team_id", "canonical_player_id",
        "opponent_team_id", "season", "is_home", "neutral_site", "prediction_cutoff_utc", "transformation_version"}
PREFIX_FAMILY = [  # longest first
    ("loc_team_", "team_shot_location"), ("loc_player_", "player_shot_location"), ("pstd_", "player_season_to_date"),
    ("pps_", "player_prior_season"), ("prof_", "player_profile"), ("role_", "player_role"), ("cold_start", "player_role"),
    ("ret_", "team_returning_minutes"), ("pr_", "player_recent"), ("pp_", "player_participation"), ("ps_", "player_shot_profile"), ("lu_", "player_lineup"),
    ("tr_", "team_results"), ("rest_", "team_rest"), ("te_", "team_efficiency"), ("tf_", "team_form"), ("ts_", "team_splits"),
    ("sos_", "team_sos"), ("tp_", "team_prior_season"), ("ev_", "team_event"),
]
FAMILY_META = {}
for mod in (team_state, player_state, event_stats, lineup_stats):
    for fam, meta in mod.FAMILIES.items():
        FAMILY_META[fam] = {"description": meta[0], "source": meta[1], "status_column": meta[-1]}

WORDS = {
    "pts": "points", "two": "two-point", "three": "three-point", "tpa": "three-point attempts", "tpm": "three-point makes",
    "two_pa": "two-point attempts", "two_pm": "two-point makes", "fta": "free-throw attempts", "ftm": "free-throw makes",
    "orb": "offensive rebounds", "drb": "defensive rebounds", "reb": "rebounds", "ast": "assists", "stl": "steals",
    "blk": "blocks", "tov": "turnovers", "pf": "personal fouls", "min": "minutes", "rim": "rim", "jumper": "two-point jumper",
    "jmp": "two-point jumper", "thr": "three-point", "ft": "free-throw", "c3": "corner three", "ab3": "above-the-break three",
    "paint": "paint (4-10 ft)", "mid": "mid-range (10+ ft two)", "opp": "opponent", "eff": "efficiency", "net": "net",
    "off": "offensive", "def": "defensive", "pace": "pace", "efg": "effective FG%", "usage": "usage share",
}


def unit_of(name: str) -> str:
    n = name
    if "_sum_" in n:
        return "count (sufficient statistic)"
    if "on_court_min" in n:
        return "count (exposure minutes; 0 = none)"
    if n == "cold_start_class" or n.endswith("_position") or n.endswith("_last_game_status") or n.endswith("_team_id"):
        return "category"
    if n.endswith("_same_team") or n.startswith("prof_has_") or n.endswith("_transfer_in"):
        return "boolean"
    if "eff" in n or "per100" in n:
        return "points per 100 possessions"
    if n.endswith("per40") or "_per40" in n:
        return "per 40 minutes"
    if any(n.endswith(s) for s in ("_pct", "_rate", "_share", "_fg", "_win_pct", "_usage")) or "_share" in n:
        return "proportion [0, 1]"
    if n == "rest_days":
        return "days"
    if "height" in n:
        return "inches"
    if "weight" in n:
        return "pounds"
    if n.endswith("_min") or "_min_" in n or n.endswith("_minutes") or "on_court_min" in n:
        return "minutes"
    if n.endswith("_pg") or "_per_app" in n:
        return "per game"
    if "recruit_rating" in n or "portal_stars" in n or "recruit_stars" in n or "recruit_ranking" in n:
        return "provider scale"
    if "recruit_year" in n:
        return "year"
    return "count"


def describe(name: str, fam: str) -> str:
    window = ("last 3 box rows" if "_l3_" in name else "last 5 box rows" if "_l5_" in name else
              "last 10 games" if "_l10_" in name else "exponentially weighted, half-life 5 games" if "_ewm_" in name else
              "previous season (window)" if fam.endswith("prior_season") else
              "cumulative since 2023-24" if "window" in name else "season to date")
    body = re.sub(r"^(loc_team_|loc_player_|pstd_|pps_|prof_|role_|pr_|pp_|ps_|lu_|tr_|rest_|te_|tf_|ts_|sos_|tp_|ev_)", "", name)
    body = re.sub(r"^(l3_|l5_|l10_|ewm_)", "", body)
    toks = body.split("_")
    words = [WORDS.get(t, t) for t in toks]
    return f"{' '.join(words)} ({window}); family {fam}: {FAMILY_META.get(fam, {}).get('description', '')}"


def classify(col: str) -> str:
    if col in KEYS:
        return "KEY"
    if "_state_" in col:
        return "PROVENANCE"
    if col.endswith("_status"):
        return "STATUS"
    return "FEATURE"


def family_of(col: str) -> str:
    for p, f in PREFIX_FAMILY:
        if col.startswith(p):
            return f
    raise ValueError(f"feature column {col!r} has no registered family prefix (anonymous feature)")


def build(con) -> dict:
    entries = []
    for t in FEATURE_TABLES:
        for col, typ in con.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_schema || '.' || table_name = '{t}' ORDER BY ordinal_position").fetchall():
            kind = classify(col)
            if kind != "FEATURE":
                entries.append({"feature_name": col, "table": t, "kind": kind, "data_type": typ, "approved_for_modeling": False})
                continue
            fam = family_of(col)
            meta = FAMILY_META[fam]
            grain = "player-game example" if t in ("feat.player_features", "feat.player_shot_features", "feat.player_lineup_features") else "team-game example"
            entries.append({
                "feature_name": col, "table": t, "kind": "FEATURE", "data_type": typ, "feature_family": fam,
                "description": describe(col, fam), "unit": unit_of(col), "grain": grain,
                "source_tables": meta["source"], "status_column": meta["status_column"],
                "historical_window": describe(col, fam).split("(")[1].split(")")[0],
                "minimum_observations": spec.MIN_OBS["lineup_pair" if fam == "player_lineup" else
                                                     "event" if "shot" in fam or fam == "team_event" else
                                                     "team" if fam.startswith("team") else "player"],
                "null_meaning": (f"NULL only with a non-OK {meta['status_column']} (NO_PRIOR_HISTORY / SOURCE_NOT_AVAILABLE / "
                                 "INSUFFICIENT_SAMPLE keeps values) or a zero denominator for a ratio; counts are 0, never NULL"),
                "pit_cutoff_rule": "ASOF state with available_at <= prediction_cutoff_utc (" + spec.CUTOFF_RULE[:60] + "...)",
                "version": spec.STAGE3_VERSION,
                "leakage_risk": ("MEDIUM: CBBD roster snapshot is season-level (retrospective membership/attributes)"
                                 if col in ("prof_position", "prof_height_in", "prof_weight_lb") else
                                 "LOW: pregame state only (PIT ASOF)"),
                "approved_for_modeling": True,
            })
    reg = {"version": spec.STAGE3_VERSION, "upstream_dataset": spec.UPSTREAM_DATASET, "cutoff_rule": spec.CUTOFF_RULE,
           "missing_codes": spec.MISSING_CODES, "families": FAMILY_META,
           "feature_count": sum(1 for e in entries if e["kind"] == "FEATURE"), "entries": entries}
    spec.REGISTRY_JSON.parent.mkdir(parents=True, exist_ok=True)
    spec.REGISTRY_JSON.write_text(json.dumps(reg, indent=1))
    return reg
