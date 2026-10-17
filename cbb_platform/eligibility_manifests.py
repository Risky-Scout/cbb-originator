"""Eligibility manifests for the frozen CBB v1 candidate (docs/MODEL_ELIGIBILITY_POLICY.md §2-§4). Development data only.

The v1 candidate fits three subsystems, so three manifests are required (policy §1: every subsystem that is fit,
calibrated or evaluated):

    ELIG_PARTICIPATION     participation logistic model              policy §2 id
    ELIG_MINUTES           minutes ridge + discretized normal        policy §2 id
    ELIG_BOX_COUNTS        7 conditional count distributions         policy §2 "later subsystem" (box-score rate x
                           (PTS REB AST FG3M STL BLK TOV)            minutes; NOT the PBP event heads of ELIG_EVENT_HEADS)

ELIG_GAME_ENVIRONMENT, ELIG_SHOT_OPPORTUNITY, ELIG_EVENT_HEADS, ELIG_LOCATION and ELIG_LINEUP_INTERACTIONS govern
subsystems v1 does not fit, calibrate or evaluate; they are recorded as NOT_REQUIRED in the index.

Each manifest's row list comes from an INDEPENDENT SQL statement of the frozen rule over the Stage 3 stores, and the
generator asserts it equals the rows the frozen loaders (props_v0.load_frames / props_v0_participation.load) hand to
fit(). The frozen artifact cannot record the manifest hash without changing its sha256, so the linkage (gate 1) is
recorded in the readiness ledger's model_artifact entry; gate 3 is enforced by that equality test, not by the frozen
training code consuming the list. Output is deterministic (no wall-clock content); 2025-26 is never read.

    .venv/bin/python -m cbb_platform.eligibility_manifests      -> modeling/eligibility/*.json + *.md + INDEX.json
"""
from __future__ import annotations

import hashlib
import json

import duckdb

from cbb_platform import config as C, lockbox, props_v0 as P, props_v0_participation as Q
from cbb_platform.stage3 import spec

OUT_DIR = C.PROJECT_ROOT / "modeling" / "eligibility"
MANIFEST_VERSION = "1.0.0"
RULE_DATE = "2026-10-13"
CODE_SHA = "6eab9ddc667942b271bdbc50cccc3c6a9d02843c"       # the candidate commit whose loaders define the frozen rules
STAT_SEASONS = tuple(range(2014, 2026))                       # 2013-14 .. 2024-25
PART_SEASONS = (2024, 2025)                                   # first seasons with confirmed DNP labels
FOLDS_STAT = {"A": {"train": list(range(2014, 2024)), "validation": [2024]},
              "B": {"train": list(range(2014, 2025)), "validation": [2025]},
              "FINAL": {"train": list(range(2014, 2026)), "validation": []}}
FOLDS_PART = {"EVALUATION": {"train": [2024], "validation": [2025]}, "FINAL": {"train": [2024, 2025], "validation": []}}
NOT_REQUIRED = ("ELIG_GAME_ENVIRONMENT", "ELIG_SHOT_OPPORTUNITY", "ELIG_EVENT_HEADS", "ELIG_LOCATION", "ELIG_LINEUP_INTERACTIONS")

BASE_SQL = {
    "ELIG_MINUTES": """SELECT l.player_game_example_id AS id, l.canonical_game_id AS game, l.season
        FROM lb.lab.player_game_labels l JOIN feat.player_features f USING (player_game_example_id)
        WHERE l.season BETWEEN 2014 AND 2025 AND l.participation_status = 'PLAYED' AND l.box_row_present
          AND l.minutes_label_available""",
    "ELIG_BOX_COUNTS": """SELECT l.player_game_example_id AS id, l.canonical_game_id AS game, l.season
        FROM lb.lab.player_game_labels l JOIN feat.player_features f USING (player_game_example_id)
        WHERE l.season BETWEEN 2014 AND 2025 AND l.participation_status = 'PLAYED' AND l.box_row_present
          AND l.minutes_label_available AND l.counting_labels_available""",
    "ELIG_PARTICIPATION": """SELECT l.player_game_example_id AS id, l.canonical_game_id AS game, l.season
        FROM lb.lab.player_game_labels l JOIN feat.player_features f USING (player_game_example_id)
        JOIN ex.player_game_examples x USING (player_game_example_id)
        WHERE l.season IN (2024, 2025) AND l.participation_status IN ('PLAYED', 'DNP_CONFIRMED')"""}

RULE_TEXT = {
    "ELIG_MINUTES": "pregame player-game examples (Stage 3 universe) of seasons 2013-14..2024-25 labelled PLAYED with a "
                    "resolved target box row and an available minutes label (props_v0.load_frames population='scored'); "
                    "training uses season <= train_through",
    "ELIG_BOX_COUNTS": "ELIG_MINUTES rows whose counting labels are available (box row present and no source accounting "
                       "issue); rows with unavailable count labels stay in ELIG_MINUTES with NaN counts and are excluded "
                       "from every count fit and score (never imputed to 0)",
    "ELIG_PARTICIPATION": "pregame player-game examples of 2023-24 and 2024-25 labelled PLAYED (y = 1) or DNP_CONFIRMED "
                          "(y = 0); UNKNOWN_PARTICIPATION and NOT_ON_ROSTER are excluded, never converted"}

MISSINGNESS = {
    "ELIG_MINUTES": {
        "pregame inputs (22 minutes_features)": {"code": "NO_PRIOR_HISTORY / INSUFFICIENT_SAMPLE (spec.MISSING_CODES)",
            "handling": "row stays; standardized value set to 0 plus a <feature>__missing indicator", "assumption": "MAR given X"},
        "prior-season finals (h1..h3)": {"code": "NO_PRIOR_HISTORY", "handling": "row stays; contributes 0 history (shrunk to the position prior)",
                                         "assumption": "MAR given X"},
        "target minutes label": {"code": "n/a", "handling": "row excluded by the rule (counted in exclusions_report)",
                                 "assumption": "informative: a missing box row is not a 0 (PBP_QUARANTINE_SELECTION_BIAS_PLAN.md)"}},
    "ELIG_BOX_COUNTS": {
        "count labels with a source accounting issue": {"code": "n/a", "handling": "row excluded from count fits (NaN), kept for minutes",
                                                         "assumption": "MCAR with respect to the count given the box row (source defect)"},
        "pregame inputs": {"code": "as ELIG_MINUTES", "handling": "as ELIG_MINUTES", "assumption": "MAR given X"}},
    "ELIG_PARTICIPATION": {
        "pregame inputs (15 numeric + 8 flags)": {"code": "NO_PRIOR_HISTORY / UNKNOWN_PARTICIPATION",
            "handling": "numeric: 0 after standardization plus a missing indicator; flags: missing = 0", "assumption": "MAR given X"},
        "target label UNKNOWN_PARTICIPATION / NOT_ON_ROSTER": {"code": "UNKNOWN_PARTICIPATION",
            "handling": "row excluded (counted)", "assumption": "informative; never converted to DNP"}}}

QUARANTINE = {
    "identity": "target rows exist only for resolved canonical players in the pregame universe; unresolved box rows are "
                "counted in lab.target_box_rows_unresolved (EXCLUDED_BOX_ROW_IDENTITY_UNRESOLVED); identity_conflicts "
                "rows are masked in the src views (stage3/sources.py); history never crosses "
                "metadata.identity_link_quarantine (spec.link_guard)",
    "team_attribution": "v2.4 DEMIRROR_RECONCILED_GAMES copies are in qa.identity_remediation_quarantine and never "
                        "reach fact.player_games / the trusted views",
    "pbp_score_mismatch": "4,138 PBP FINAL_SCORE_MISMATCH games are excluded from flagship.plays; these subsystems read "
                          "box rows and the substitution feed, not plays, so the rule does not select their rows. The "
                          "eligible rows inside those games are COUNTED below (pbp_quarantine_overlap)",
    "event_team_attribution": "not applicable (no event rows are read)",
    "reconstruction_class": "not applicable (no possessions or stints are read)"}

PIT_FIDELITY = {
    "checkpoint": "single pregame cutoff = scheduled tip - 1 h (spec.CUTOFF_RULE), i.e. the T-1h checkpoint",
    "inputs": {"earlier-game box rows, participation and substitution feeds": "DATE_KNOWN (HR-1: tip + 4 h, or 06:00 ET next day)",
               "prior-season finals, previous-season roster returners, recruiting class s-1, portal year s-1": "PRESEASON_ASSUMED"},
    "prohibited_classes_admitted": [],
    "note": "no SOURCE_RECEIPT_UNKNOWN or UNUSABLE_FOR_HISTORICAL_PIT input enters these subsystems (end-of-season roster "
            "snapshots are used only for the NOT_ON_ROSTER LABEL, which is excluded). The PRESEASON_ASSUMED class is carried "
            "by the Stage 3 1900-01-01 preseason sentinel, not by an explicit fidelity column: the bitemporal rebuild "
            "(Phase A2 item 1) is outstanding (ARCHITECTURE_PRECEDENCE.md §4.4)"}


def _h(ids) -> str:
    return hashlib.sha256(",".join(sorted(ids)).encode()).hexdigest()


def _con():
    con = duckdb.connect(str(spec.FEATURES_DB), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute(f"ATTACH '{spec.LABELS_DB}' AS lb (READ_ONLY)")
    return con


def _rows(con, sql: str) -> list[tuple]:
    rows = con.execute(f"SELECT id, game, season FROM ({sql}) ORDER BY id").fetchall()
    if rows and max(r[2] for r in rows) >= lockbox.LOCKBOX_SEASON:
        raise lockbox.LockboxViolation("a lockbox row reached an eligibility manifest")
    return rows


def _loader_rows() -> dict:
    """The rows the frozen training loaders produce (the gate-3 comparison)."""
    cfg = P.load_config()
    frame, finals, excl = P.load_frames(cfg)
    part, counts = Q.load(seasons=PART_SEASONS)
    return {"ELIG_MINUTES": sorted(frame.loc[frame["y_min"].notna(), "player_game_example_id"]),
            "ELIG_BOX_COUNTS": sorted(frame.loc[frame["counts_ok"].astype(bool), "player_game_example_id"]),
            "ELIG_PARTICIPATION": sorted(part.loc[part["y"].notna(), "player_game_example_id"]),
            "_excl": excl, "_part_counts": counts, "_finals": finals, "_cfg": cfg}


def _pbp_overlap(game_ids: set) -> dict:
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)        # same open config as every other canonical reader
    con.execute(f"SET threads = {C.DUCKDB_THREADS}")
    q = set(r[0] for r in con.execute("""SELECT DISTINCT canonical_game_id FROM qa.source_defects
                                         WHERE family = 'PBP' AND reason_code = 'FINAL_SCORE_MISMATCH'""").fetchall())
    con.close()
    return {"quarantined_games_total": len(q), "eligible_games_inside": len(game_ids & q)}


def build() -> dict:
    s3 = json.loads(spec.STAGE3_MANIFEST.read_text())
    dm = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    folds = json.loads((spec.FOLDS_DIR / "fold_manifests.json").read_text())
    loader = _loader_rows()
    con = _con()
    try:
        return _build(con, loader, s3, dm, folds)
    finally:
        con.close()


def _build(con, loader, s3, dm, folds) -> dict:
    out = {}
    for mid, sql in BASE_SQL.items():
        rows = _rows(con, sql)
        ids = [r[0] for r in rows]
        if ids != loader[mid]:
            raise AssertionError(f"{mid}: SQL rule rows differ from the frozen loader rows (gate 3)")
        games = sorted({r[1] for r in rows})
        by_season = {}
        for _, _, s in rows:
            by_season[str(s)] = by_season.get(str(s), 0) + 1
        seasons = sorted(int(s) for s in by_season)
        fold_def = FOLDS_PART if mid == "ELIG_PARTICIPATION" else FOLDS_STAT
        fold_counts = {f: {part: {"seasons": ss, "player_games": sum(by_season.get(str(s), 0) for s in ss),
                                  "sha256": _h([r[0] for r in rows if r[2] in ss])} for part, ss in d.items()}
                       for f, d in fold_def.items()}
        if mid == "ELIG_PARTICIPATION":
            excl = {s: {k: v for k, v in c.items() if k not in ("PLAYED", "DNP_CONFIRMED")} for s, c in loader["_part_counts"].items()}
            pre = con.execute("""SELECT season, count(*) FROM lb.lab.player_game_labels WHERE season BETWEEN 2014 AND 2023
                                 GROUP BY 1 ORDER BY 1""").fetchall()
            for s, n in pre:
                excl[str(s)] = {"EXCLUDED_SEASON_BEFORE_CONFIRMED_DNP_LABELS (2013-14..2022-23)": int(n)}
        else:
            excl = {}
            for s, c in sorted(loader["_excl"].items()):
                e = {k: v for k, v in c.items() if k != "SCORED" and not (mid == "ELIG_MINUTES" and k.startswith("SCORED_MIN_ONLY"))}
                excl[str(s)] = e
        man = {"manifest_id": mid, "version": MANIFEST_VERSION, "created_at": RULE_DATE, "code_sha": CODE_SHA,
               "generator": "cbb_platform/eligibility_manifests.py",
               "candidate": {"tag_original": "cbb-props-v1-prelockbox-2024-25",
                             "artifact_sha256": "96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f"},
               "dataset_version": dm["dataset_version"], "dataset_status": dm["status"],
               "dataset_linkage": {"canonical_content_digest": dm["canonical_content_digest"],
                                   "canonical_build_id": dm["canonical_build_id"]},
               "feature_version": {"stage3_version": s3["version"], "upstream_dataset": s3["upstream_dataset"],
                                   "features_db_sha_of_fingerprints": s3["features_db_sha_of_fingerprints"],
                                   "labels_db_sha_of_fingerprints": s3["labels_db_sha_of_fingerprints"]},
               "seasons": seasons, "lockbox_season_present": lockbox.LOCKBOX_SEASON in seasons,
               "rule": RULE_TEXT[mid], "rule_sql": " ".join(sql.split()),
               "eligible_games": {"rule": "distinct canonical_game_id of the eligible player-games", "count": len(games),
                                  "sha256": _h(games)},
               "eligible_player_games": {"rule": RULE_TEXT[mid], "count": len(ids), "sha256": _h(ids)},
               "required_sources": {"lab.player_game_labels": "Stage 3 label store (participation_status, box_row_present, "
                                                              "minutes_label_available, counting_labels_available)",
                                    "feat.player_features": "Stage 3 approved pregame features (prediction cutoff = tip - 1 h)",
                                    "upstream": "flagship.played_games / fact.player_games (trusted), flagship.substitutions, "
                                                "core.rosters, core.recruits, core.transfers"},
               "missingness_mechanism": MISSINGNESS[mid], "quarantine_rules": QUARANTINE,
               "pbp_quarantine_overlap": _pbp_overlap(set(games)),
               "identity_requirements": "target player-game has a canonical player id (RESOLVED); cross-season history "
                                        "never crosses a quarantined identity link; identity-conflict player-seasons masked",
               "pit_fidelity_requirement": PIT_FIDELITY,
               "sample_counts": {"by_season": dict(sorted(by_season.items())), "folds": fold_counts,
                                 "core_walk_forward_reference": [f["fold"] for f in folds["folds"]]},
               "exclusions_report": excl,
               "selection_bias_reference": "docs/PBP_QUARANTINE_SELECTION_BIAS_PLAN.md (box-row absence is informative; "
                                           "the participation exclusion of UNKNOWN labels is non-random)"}
        if mid == "ELIG_BOX_COUNTS":
            cfg, fin = loader["_cfg"], loader["_finals"]
            pf = fin[fin["min"] >= cfg["rate_prior_min_minutes"]]
            keys = sorted(f"{p}|{s}" for p, s in zip(pf["canonical_player_id"], pf["season"]))
            man["eligible_player_seasons_for_rate_prior"] = {
                "rule": f"player-season box finals (feat.player_box_state, last available row) with minutes >= "
                        f"{cfg['rate_prior_min_minutes']}; method-of-moments prior strength k uses seasons <= train_through",
                "count": len(keys), "sha256": _h(keys)}
        man["manifest_sha256_of_content"] = hashlib.sha256(json.dumps(man, sort_keys=True).encode()).hexdigest()
        out[mid] = man
    return out


def render(man: dict) -> str:
    e = man["eligible_player_games"]
    lines = [f"# {man['manifest_id']} v{man['version']}", "",
             f"Generated by `{man['generator']}`. Do not edit by hand. Dataset `{man['dataset_version']}` "
             f"({man['dataset_status']}); Stage 3 `{man['feature_version']['stage3_version']}`.", "",
             f"**Rule:** {man['rule']}", "",
             f"* Seasons: {man['seasons'][0]}..{man['seasons'][-1]} (lockbox present: {man['lockbox_season_present']})",
             f"* Eligible player-games: {e['count']:,} (sha256 `{e['sha256']}`)",
             f"* Eligible games: {man['eligible_games']['count']:,} (sha256 `{man['eligible_games']['sha256']}`)",
             f"* PBP-quarantined games containing eligible rows: {man['pbp_quarantine_overlap']['eligible_games_inside']}",
             "", "| season | eligible | excluded (by rule) |", "|---|---|---|"]
    for s, n in man["sample_counts"]["by_season"].items():
        ex = man["exclusions_report"].get(s, {})
        lines.append(f"| {s} | {n:,} | {'; '.join(f'{k} {v:,}' for k, v in sorted(ex.items())) or '—'} |")
    for s, ex in man["exclusions_report"].items():
        if s not in man["sample_counts"]["by_season"]:
            lines.append(f"| {s} | 0 | {'; '.join(f'{k} {v:,}' for k, v in sorted(ex.items()))} |")
    lines += ["", f"PIT fidelity: {man['pit_fidelity_requirement']['checkpoint']}. {man['pit_fidelity_requirement']['note']}", ""]
    return "\n".join(lines)


def dumps(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True) + "\n"


def index(mans: dict) -> dict:
    return {"policy": "docs/MODEL_ELIGIBILITY_POLICY.md", "version": MANIFEST_VERSION,
            "required_for_v1": {m: {"path": f"modeling/eligibility/{m}.json",
                                    "eligible_player_games_sha256": v["eligible_player_games"]["sha256"],
                                    "count": v["eligible_player_games"]["count"]} for m, v in mans.items()},
            "not_required_for_v1": {m: "NOT_REQUIRED: v1 fits, calibrates and evaluates no such subsystem (policy §1)"
                                    for m in NOT_REQUIRED},
            "gate_1_linkage": "recorded in data/manifests/LOCKBOX_READINESS_v2.json model_artifact.eligibility_manifests "
                              "(the frozen artifact itself cannot carry the hash without changing its sha256)",
            "gate_3_enforcement": "equality test: rule SQL rows == frozen loader rows (tests/test_eligibility_manifests.py)"}


def write() -> dict:
    mans = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for mid, man in mans.items():
        (OUT_DIR / f"{mid}.json").write_text(dumps(man))
        (OUT_DIR / f"{mid}.md").write_text(render(man))
    (OUT_DIR / "INDEX.json").write_text(dumps(index(mans)))
    return {m: hashlib.sha256((OUT_DIR / f"{m}.json").read_bytes()).hexdigest() for m in mans}


if __name__ == "__main__":
    print(json.dumps(write(), indent=1))
