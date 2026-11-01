"""Production boards and the WizardOfOdds production contract v2 (docs/integrations/
WIZARDOFODDS_CBB_ORIGINATOR_PRODUCTION_CONTRACT_v2.md). Reads the outputs of ONE run.daily call (its summary, its
PREDICTION ledger records, its immutable state version); never re-scores, never changes a forecast or a price.

    PUBLIC PROJECTION BOARD   every eligible player x supported market, with or without a sportsbook line
    INTERNAL PRICING BOARD    quoted lines only: probabilities, FAIR prices (model) beside EXECUTABLE prices (book)

Gates (each fails closed; the matrix is FAIL_CLOSED_MATRIX):
    model hash, production season, run / game / row reasons from the run, point-in-time / leakage flags, the 2 h
    schedule-freshness gate (prod/schedule_freshness.py), no post-tip publication, and for pricing the quote gates.
Publication records are deterministic (publication_id, payload_sha256, idempotency_key) and append-only; the transport
is PENDING_SITE_CONFIRMATION (no WizardOfOdds interface is confirmed), so only a local staging transport exists.
AUTOMATIC_ACTIONABLE_WAGER is DISABLED: no betting policy has been supplied by the architect.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import config as C
from cbb_platform.prod import config as PC, loader as LD, rotowire_feed as RW, slate as SL

CONTRACT_VERSION = "cbb-wizard-production-contract-2.0.0"
OPERATIONAL_MODE = "PRODUCTION"
PUBLISH_TRANSPORT = "PENDING_SITE_CONFIRMATION"
AUTOMATIC_ACTIONABLE_WAGER = "DISABLED"
DRAWS_PER_GAME = PC.SIMS                                    # 20,000 joint draws per game (frozen manifest)
SUPPORTED_MARKETS = PC.MARKETS                              # PTS REB AST FG3M STL BLK TOV PRA PR PA RA STOCKS

# ------------------------------------------------------------------ field visibility (configurable; private never public
# unless PUBLISH_PRIVATE_FIELDS is separately authorized)
PUBLIC_FIELDS = ("canonical_game_id", "game", "scheduled_tip_utc", "home_team_id", "home_team", "away_team_id",
                 "away_team", "player_id", "player_name", "team_id", "team", "market", "projection",
                 "projection_basis", "p_play", "pmf_q75", "distribution_method", "prediction_as_of",
                 "feature_cutoff", "data_cutoff", "schedule_verified_at", "model_version", "architecture",
                 "frozen_commit", "status", "suppression_reasons")
PRIVATE_FIELDS = ("line", "side", "p_over", "p_under", "p_push", "fair_decimal_over", "fair_decimal_under",
                  "fair_american_over", "fair_american_under", "sportsbook", "executable_decimal", "executable_american",
                  "quote_available_at", "collector_received_at", "quote_age_seconds", "raw_edge_ev_per_unit",
                  "mcse")
PUBLISH_PRIVATE_FIELDS = False

# ------------------------------------------------------------------ reasons that concern sportsbook quotes ONLY: they
# block pricing, never a public projection (projections never wait for a line)
QUOTE_ONLY_REASONS = frozenset({
    "ODDS_API_FAILURE", "QUOTE_INTEGRATION_UNVALIDATED", "ODDS_EVENT_NOT_YET_LISTED", "PLAYER_PROP_MARKET_NOT_YET_POSTED",
    "QUOTE_IDENTITY_UNRESOLVED", "QUOTE_STALE", "STALE_QUOTE", "POST_CUTOFF_QUOTE", "INVALID_QUOTE", "NO_VALID_QUOTE",
    "MARKET_NOT_OFFERED_FOR_EVENT"})
FEATURE_LEAKAGE_REASONS = frozenset({"FUTURE_LEAKAGE"})
FUTURE_DATA_REASONS = frozenset({"POST_CUTOFF_DATA", "PREDICTION_AFTER_CUTOFF"})

# ------------------------------------------------------------------ betting policy (architect-owned; never invented)
BETTING_POLICY_PATH = C.PROJECT_ROOT / "config" / "betting_policy.json"   # absent: architect has not supplied it
BETTING_POLICY_FIELDS = ("eligible_sportsbook_accounts", "bankroll", "stake_sizing_method", "kelly_fraction",
                         "max_wager", "daily_exposure_cap", "player_exposure_cap", "game_exposure_cap",
                         "correlated_market_exposure_cap", "min_edge_to_wager", "approved_by", "approved_at")

# ------------------------------------------------------------------ §18 fail-closed matrix
# condition: (PUBLICATION_ALLOWED, PRICING_ALLOWED, ACTIONABLE_BET_ALLOWED, RETRY_ALLOWED, ALERT_REQUIRED)
FAIL_CLOSED_MATRIX = {
    "ARTIFACT_HASH_MISMATCH": (False, False, False, False, True),
    "WRONG_PRODUCTION_SEASON": (False, False, False, False, True),
    "STALE_SCHEDULE": (False, False, False, True, True),
    "GAME_IDENTITY_AMBIGUITY": (False, False, False, True, True),
    "PLAYER_IDENTITY_AMBIGUITY": (False, False, False, True, False),
    "UNSUPPORTED_PLAYER_STATUS": (False, False, False, True, False),
    "STALE_QUOTE": (True, False, False, True, False),
    "ODDS_API_FAILURE": (True, False, False, True, True),
    "CBBD_FAILURE": (False, False, False, True, True),
    "MISSING_STATE": (False, False, False, True, True),
    "FAILED_STATE_PROMOTION": (False, False, False, True, True),
    "POINT_IN_TIME_VIOLATION": (False, False, False, False, True),
    "GAME_ALREADY_STARTED": (False, False, False, False, False),
    "SIMULATION_FAILURE": (False, False, False, True, True),
    "NON_NORMALIZED_PMF": (False, False, False, False, True),
    "PUBLICATION_FAILURE": (False, True, False, True, True),
    "PUBLICATION_RECONCILIATION_MISMATCH": (False, True, False, False, True),
    "DUPLICATE_PUBLICATION_CONFLICTING_PAYLOAD": (False, True, False, False, True),
    "BETTING_POLICY_MISSING": (True, True, False, False, False),
    "COMMERCIAL_RIGHTS_UNVERIFIED": (False, True, False, False, True),
    # architect ruling 2026-10-31: GTD / QUESTIONABLE / DOUBTFUL / unresolved RETURN_DESIGNATION -> PRICED_BUT_NON_ACTIONABLE
    "UNRESOLVED_AVAILABILITY": (True, True, False, True, False),
}

# ------------------------------------------------------------------ commercial / publication rights (frozen policy
# docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md §2-§3; spec v1 §9): a PUBLIC transport refuses any board
# whose production sources are not PRODUCTION_APPROVED. Mirrors the register; a status change is a new, approved
# register version (never edited here to make publication pass).
RIGHTS_REGISTER = {"CBB Starter Pack (frozen history extract)": "RESEARCH_ONLY",
                   "CBBD API (collegebasketballdata.com)": "UNVERIFIED",
                   "RotoWire CBB API (availability)": "UNVERIFIED"}
PUBLIC_BOARD_SOURCES = tuple(RIGHTS_REGISTER)


def rights_gate(register: dict = RIGHTS_REGISTER, sources=PUBLIC_BOARD_SOURCES) -> dict:
    bad = {s: register.get(s, "MISSING") for s in sources if register.get(s) != "PRODUCTION_APPROVED"}
    return {"COMMERCIAL_RIGHTS_GATE": "FAIL" if bad else "PASS", "unapproved_sources": bad}
# run reason codes -> matrix condition (unknown codes block everything: fail closed)
CODE_CONDITION = {
    "ARTIFACT_HASH_MISMATCH": "ARTIFACT_HASH_MISMATCH", "WRONG_PRODUCTION_SEASON": "WRONG_PRODUCTION_SEASON",
    "STALE_STATE": "STALE_SCHEDULE", "MISSING_SCHEDULE": "STALE_SCHEDULE",
    "AMBIGUOUS_GAME": "GAME_IDENTITY_AMBIGUITY", "UNMATCHED_GAME": "GAME_IDENTITY_AMBIGUITY",
    "UNKNOWN_TEAM": "GAME_IDENTITY_AMBIGUITY", "NON_DI_GAME": "GAME_IDENTITY_AMBIGUITY",
    "AMBIGUOUS_PLAYER": "PLAYER_IDENTITY_AMBIGUITY", "UNMATCHED_PLAYER": "PLAYER_IDENTITY_AMBIGUITY",
    "IDENTITY_CONFLICT": "PLAYER_IDENTITY_AMBIGUITY", "DUPLICATE_PROVIDER_IDENTITY": "PLAYER_IDENTITY_AMBIGUITY",
    "RECRUIT_IDENTITY_UNAVAILABLE": "PLAYER_IDENTITY_AMBIGUITY",
    "CONFIRMED_UNAVAILABLE": "UNSUPPORTED_PLAYER_STATUS", "NOT_ON_CURRENT_ROSTER": "UNSUPPORTED_PLAYER_STATUS",
    "TRANSFERRED_OUT": "UNSUPPORTED_PLAYER_STATUS", "NO_FROZEN_MEMBERSHIP_EVIDENCE": "UNSUPPORTED_PLAYER_STATUS",
    "MISSING_PLAYER_STATE": "MISSING_STATE", "MISSING_ROSTER": "MISSING_STATE", "STALE_ROSTER": "MISSING_STATE",
    "LIVE_DATA_FEED_UNAVAILABLE": "CBBD_FAILURE", "PROVIDER_FAILURE": "CBBD_FAILURE",
    "STATE_UPDATE_FAILED": "FAILED_STATE_PROMOTION",
    "FUTURE_LEAKAGE": "POINT_IN_TIME_VIOLATION", "POST_CUTOFF_DATA": "POINT_IN_TIME_VIOLATION",
    "PREDICTION_AFTER_CUTOFF": "POINT_IN_TIME_VIOLATION", "CUTOFF_UNRESOLVED": "POINT_IN_TIME_VIOLATION",
    "SCORING_FAILURE": "SIMULATION_FAILURE", "NON_PSD_DEPENDENCE": "SIMULATION_FAILURE",
    "UNSUPPORTED_ROTATION_SHOCK": "SIMULATION_FAILURE", "BUNDLE_CHECKSUM_FAILURE": "SIMULATION_FAILURE",
    "PMF_NOT_NORMALIZED": "NON_NORMALIZED_PMF", "INVALID_PROBABILITIES": "NON_NORMALIZED_PMF",
    "UNSUPPORTED_MARKET": "SIMULATION_FAILURE",
    "AVAILABILITY_FEED_UNAVAILABLE": "MISSING_STATE", "AVAILABILITY_STALE": "MISSING_STATE",
    "AVAILABILITY_IDENTITY_UNRESOLVED": "PLAYER_IDENTITY_AMBIGUITY",
    "AVAILABILITY_UNCERTAIN": "UNRESOLVED_AVAILABILITY", "AVAILABILITY_UNCERTAIN_ROTATION": "UNRESOLVED_AVAILABILITY",
    "AVAILABILITY_SOURCE_CONFLICT": "UNSUPPORTED_PLAYER_STATUS", "AVAILABILITY_STATUS_UNRECOGNIZED": "UNSUPPORTED_PLAYER_STATUS",
    **{c: ("ODDS_API_FAILURE" if c in ("ODDS_API_FAILURE", "QUOTE_INTEGRATION_UNVALIDATED") else "STALE_QUOTE")
       for c in QUOTE_ONLY_REASONS},
}


# codes that permit publication and pricing but never actionability (they are not suppression reasons)
PRICED_BLOCKED_REASONS = frozenset(c for c, k in CODE_CONDITION.items() if k == "UNRESOLVED_AVAILABILITY")
BOARD_STATES = ("PRICED_ACTIONABLE", "PRICED_BLOCKED", "SUPPRESSED")


def condition_of(code: str) -> tuple[bool, bool, bool, bool, bool]:
    """Matrix row of a reason code; schedule-freshness and other gate reasons are mapped too; unknown -> all False."""
    if code in FAIL_CLOSED_MATRIX:
        return FAIL_CLOSED_MATRIX[code]
    if code in CODE_CONDITION:
        return FAIL_CLOSED_MATRIX[CODE_CONDITION[code]]
    if code.startswith("SCHEDULE_") or code in ("GAME_NOT_IN_CURRENT_SCHEDULE", "GAME_IDENTITY_CHANGED",
                                                "HOME_AWAY_CHANGED", "GAME_NOT_LISTED_OR_CANCELLED",
                                                "NO_PROVIDER_VERIFICATION"):
        return FAIL_CLOSED_MATRIX["STALE_SCHEDULE"]
    if code == "GAME_ALREADY_STARTED":
        return FAIL_CLOSED_MATRIX["GAME_ALREADY_STARTED"]
    return (False, False, False, False, True)


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _iso(x):
    return None if x is None or (not isinstance(x, str) and pd.isna(x)) else _utc(x).isoformat()


def _num(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else float(x)


# ============================================================================ run context
@dataclass
class RunContext:
    summary: dict
    records: list[dict]                  # PREDICTION bodies of this run
    slate: pd.DataFrame                  # rebuilt from the immutable state version the run used
    state_manifest: dict
    increment_db: Path
    teams: dict
    data_label: str


def load_run(summary: dict, ledger_path: Path, state_root: Path = PC.STATE_ROOT) -> RunContext:
    rid = summary["run_id"]
    recs = []
    for line in Path(ledger_path).read_text().splitlines():
        r = json.loads(line)
        if r["record_type"] == "PREDICTION" and r["body"]["run_id"] == rid:
            recs.append(r["body"])
    vdir = Path(state_root) / "versions" / summary["state_version"]
    man = json.loads((vdir / "state_manifest.json").read_text())
    inc = vdir / "increment.duckdb"
    if hashlib.sha256(inc.read_bytes()).hexdigest() != man["increment_sha256"]:
        raise RuntimeError(f"state {summary['state_version']} changed after promotion")
    slate = SL.build(inc, summary["prediction_as_of"], man.get("schedule_as_of") or man["source_cutoff"])
    teams = {}
    cdb = Path(summary["run_dir"]) / "features" / "canonical_asof.duckdb"
    if cdb.exists():
        con = duckdb.connect(str(cdb), read_only=True)
        teams = dict(con.execute("SELECT canonical_team_id, any_value(display_name) FROM core.teams GROUP BY 1").fetchall())
        con.close()
    return RunContext(summary, recs, slate, man, inc, teams, summary.get("data_label", "LIVE"))


# ============================================================================ point in time
def outcome_rows_for(db: Path, game_id: str) -> int:
    """Outcome evidence of the TARGET game inside a database the forecast could read (result, team or player box)."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        tabs = {f"{s}.{t}" for s, t in con.execute("SELECT schema_name, table_name FROM duckdb_tables()").fetchall()}
        n = 0
        if "flagship.games" in tabs:
            n += con.execute("SELECT count(*) FROM flagship.games WHERE canonical_game_id = ? AND home_points IS NOT NULL",
                             [game_id]).fetchone()[0]
        for t in ("flagship.team_game_outcomes", "flagship.player_game_outcomes"):
            if t in tabs:
                n += con.execute(f"SELECT count(*) FROM {t} WHERE canonical_game_id = ?", [game_id]).fetchone()[0]
        return int(n)
    finally:
        con.close()


def point_in_time(ctx: RunContext, game_id: str) -> dict:
    s = ctx.summary
    aso = _utc(s["prediction_as_of"])
    gr = set(s.get("game_reasons", {}).get(game_id, []))
    rr = set(s.get("run_reasons", []))
    sl = ctx.slate.set_index("canonical_game_id")
    cut = _utc(sl.loc[game_id, "feature_cutoff"]) if game_id in sl.index else None
    recs = [r for r in ctx.records if r["game_id"] == game_id]
    feat = bool(gr & FEATURE_LEAKAGE_REASONS) or cut is None or any(_utc(r["feature_cutoff"]) != cut for r in recs)
    cdb = Path(s["run_dir"]) / "features" / "canonical_asof.duckdb"
    outcome = outcome_rows_for(ctx.increment_db, game_id) > 0 or (cdb.exists() and outcome_rows_for(cdb, game_id) > 0)
    future = bool((gr | rr) & FUTURE_DATA_REASONS) or (cut is not None and aso > cut) \
        or _utc(ctx.state_manifest["source_cutoff"]) > aso
    return {"POINT_IN_TIME_LEGALITY": "FAIL" if (feat or outcome or future) else "PASS",
            "TARGET_GAME_FEATURE_LEAKAGE_DETECTED": bool(feat),
            "TARGET_GAME_OUTCOME_LEAKAGE_DETECTED": bool(outcome),
            "FUTURE_DATA_LEAKAGE_DETECTED": bool(future)}


# ============================================================================ quote gates (internal pricing)
QUOTE_GATES = ("EXACT_EVENT_MATCH", "STRICT_PLAYER_IDENTITY", "SUPPORTED_MARKET", "VALID_LINE", "VALID_SIDE",
               "VALID_PRICE", "QUOTE_FRESH_AT_CHECKPOINT", "GAME_NOT_STARTED", "PROVIDER_RETURNED_QUOTE",
               "MODEL_HASH_GATE", "POINT_IN_TIME_GATE")


def quote_gates(rec: dict, checkpoint_at, tip, model_ok: bool, pit_ok: bool) -> dict:
    """Every gate must hold for a row to carry ACTIONABLE pricing. The run only attaches a sportsbook to a row when the
    quote was VALID after strict game / player matching (prod/matching.py) and freshness at prediction time
    (board.quote_freshness); freshness is re-checked here at the checkpoint."""
    cp = _utc(checkpoint_at)
    has_q = rec.get("sportsbook") is not None and rec.get("best_decimal_odds") is not None
    line, side, dec = rec.get("line"), rec.get("side"), rec.get("best_decimal_odds")
    qa = rec.get("quote_available_at")
    g = {"EXACT_EVENT_MATCH": has_q, "STRICT_PLAYER_IDENTITY": has_q,          # matched under matching.py, never fuzzy
         "SUPPORTED_MARKET": rec.get("market") in SUPPORTED_MARKETS,
         "VALID_LINE": line is not None and float(line) >= 0 and float(line * 2) == round(line * 2),
         "VALID_SIDE": side in ("OVER", "UNDER"),
         "VALID_PRICE": dec is not None and np.isfinite(dec) and dec > 1.0,
         "QUOTE_FRESH_AT_CHECKPOINT": bool(has_q and qa is not None and _utc(qa) <= cp
                                           and (cp - _utc(qa)).total_seconds() <= PC.QUOTE_MAX_AGE_SECONDS),
         "GAME_NOT_STARTED": tip is not None and cp < _utc(tip),
         "PROVIDER_RETURNED_QUOTE": has_q and rec.get("collector_received_at") is not None,
         "MODEL_HASH_GATE": bool(model_ok), "POINT_IN_TIME_GATE": bool(pit_ok)}
    return {"gates": g, "QUOTE_QUALITY_GATE": "PASS" if all(g.values()) else "FAIL",
            "failed": [k for k, v in g.items() if not v]}


# ============================================================================ betting policy
def betting_policy(path: Path = BETTING_POLICY_PATH) -> dict:
    """Architect-supplied real-money policy. Absent or incomplete -> ARCHITECT_BETTING_POLICY_REQUIRED. In this phase
    AUTOMATIC_ACTIONABLE_WAGER stays DISABLED even with a complete policy (no bet placement is authorized)."""
    p = Path(path)
    pol = json.loads(p.read_text()) if p.exists() else None
    missing = list(BETTING_POLICY_FIELDS) if pol is None else [f for f in BETTING_POLICY_FIELDS if pol.get(f) in (None, "")]
    return {"ARCHITECT_BETTING_POLICY_REQUIRED": bool(missing), "missing_fields": missing,
            "AUTOMATIC_ACTIONABLE_WAGER": AUTOMATIC_ACTIONABLE_WAGER}


# ============================================================================ actionability
def global_actionable_blocks(policy: dict | None = None) -> list[str]:
    """Blocks that apply to every row whatever its own gates: no automatic wager, no architect betting policy, G4B
    availability validation not passed (PC.REAL_MONEY_ACTIONABILITY), wagering disabled."""
    pol = policy if policy is not None else betting_policy()
    b = []
    if AUTOMATIC_ACTIONABLE_WAGER != "ENABLED":
        b.append("AUTOMATIC_ACTIONABLE_WAGER_DISABLED")
    if pol["ARCHITECT_BETTING_POLICY_REQUIRED"]:
        b.append("BETTING_POLICY_MISSING")
    if PC.REAL_MONEY_ACTIONABILITY != "ENABLED":
        b.append(PC.REAL_MONEY_ACTIONABILITY)
    if not PC.WAGERING_ENABLED:
        b.append("WAGERING_DISABLED")
    return b


def actionable_block_reasons(row: dict, *, priced: bool, codes, checkpoint_at, tip, prediction_as_of,
                             availability: dict | None, global_blocks) -> list[str]:
    """Row actionability: ACTIONABLE only when this list is empty. Every input is from ONE run (prediction_as_of): the
    availability state, the player / minutes state, the quote and the price. A price computed while a player was
    GTD / Q / D is never unlocked: after the status resolves to ACT only a NEW run can be actionable, and only if its
    availability observation is still fresh at the checkpoint and its sportsbook quote was posted after the status in
    force became knowable (a quote from before the news has not priced it)."""
    cp = _utc(checkpoint_at)
    r = list(global_blocks)
    if not priced:
        r.append("NOT_PRICED")
    if row.get("availability_status") in RW.UNRESOLVED or set(codes) & PRICED_BLOCKED_REASONS:
        r.append("UNRESOLVED_AVAILABILITY")
    last = (availability or {}).get("last_success") or {}
    if availability is None:
        r.append("AVAILABILITY_SOURCE_NOT_CONFIGURED")
    elif not last or any(v is None for v in last.values()):
        r.append("AVAILABILITY_FEED_UNAVAILABLE")
    else:
        mtt = -1.0 if tip is None else (_utc(tip) - cp).total_seconds() / 60.0
        age = (cp - min(_utc(v) for v in last.values())).total_seconds() / 60.0
        if age > RW.freshness_bound_minutes(mtt):
            r.append("AVAILABILITY_STALE_AT_CHECKPOINT")
    ra = row.get("availability_received_at")
    if ra is not None:
        ra = _utc(ra)
        if ra >= _utc(prediction_as_of):
            r.append("REPRICE_REQUIRED")
        qa = row.get("quote_available_at")
        if qa is None or _utc(qa) < ra:
            r.append("QUOTE_PREDATES_AVAILABILITY_STATE")
    return sorted(set(r))


# ============================================================================ boards
def _model_gate() -> tuple[bool, dict]:
    try:
        return True, LD.verify()
    except LD.ArtifactIntegrityError as e:
        return False, {"error": str(e), "reason": e.reason}


def _decide(codes) -> dict:
    rows = [condition_of(c) for c in codes]
    return {"publication_allowed": all(r[0] for r in rows), "pricing_allowed": all(r[1] for r in rows),
            "actionable_bet_allowed": False}         # AUTOMATIC_ACTIONABLE_WAGER = DISABLED (no betting policy)


def build_boards(ctx: RunContext, checkpoint_at, schedule_gate: dict, *, checkpoint_label: str,
                 publish_private_fields: bool = PUBLISH_PRIVATE_FIELDS) -> dict:
    """Returns {"public": payload, "internal": payload, "game_status": {...}}. schedule_gate: schedule_freshness.gate()
    output for the same games at the same checkpoint (a game missing from it FAILS)."""
    cp = _utc(checkpoint_at)
    s = ctx.summary
    model_ok, ident_now = _model_gate()
    ident = s["model_identity"]
    if model_ok and ident_now.get("artifact_sha256") != ident.get("artifact_sha256"):
        model_ok = False
    sl = ctx.slate.set_index("canonical_game_id")
    run_codes = set(s.get("run_reasons", []))
    if not model_ok:
        run_codes.add("ARTIFACT_HASH_MISMATCH")
    if ident.get("production_season") not in (None, PC.PRODUCTION_SEASON):
        run_codes.add("WRONG_PRODUCTION_SEASON")
    if ctx.data_label != "LIVE":
        run_codes.add("FIXTURE_DATA_NOT_PUBLISHABLE")
    games, public, internal = {}, [], []
    pol = betting_policy()
    gblocks = global_actionable_blocks(pol)
    for gid in sorted({r["game_id"] for r in ctx.records}):
        pit = point_in_time(ctx, gid)
        sg = (schedule_gate.get("games") or {}).get(gid) or {"SCHEDULE_FRESHNESS_GATE": "FAIL",
                                                             "reasons": ["SCHEDULE_FRESHNESS_NOT_EVALUATED"]}
        tip = sl.loc[gid, "start_time"] if gid in sl.index else None
        codes = set(run_codes) | set(s.get("game_reasons", {}).get(gid, []))
        if pit["POINT_IN_TIME_LEGALITY"] != "PASS":
            codes.add("POINT_IN_TIME_VIOLATION")
        codes |= set(sg["reasons"]) if sg["SCHEDULE_FRESHNESS_GATE"] != "PASS" else set()
        if tip is None or cp >= _utc(tip):
            codes.add("GAME_ALREADY_STARTED")
        dec = _decide(codes - QUOTE_ONLY_REASONS)
        games[gid] = {**pit, "SCHEDULE_FRESHNESS_GATE": sg["SCHEDULE_FRESHNESS_GATE"],
                      "schedule_verified_at": sg.get("verified_at"), "codes": sorted(codes), **dec,
                      "pricing_allowed": dec["pricing_allowed"] and _decide(codes)["pricing_allowed"]}
        srow = sl.loc[gid] if gid in sl.index else None
        gbase = {"canonical_game_id": gid, "scheduled_tip_utc": _iso(tip),
                 "home_team_id": None if srow is None else srow.home_team_id,
                 "home_team": None if srow is None else ctx.teams.get(srow.home_team_id, srow.home_team_id),
                 "away_team_id": None if srow is None else srow.away_team_id,
                 "away_team": None if srow is None else ctx.teams.get(srow.away_team_id, srow.away_team_id),
                 "prediction_as_of": _iso(s["prediction_as_of"]),
                 "data_cutoff": ctx.state_manifest["source_cutoff"], "schedule_verified_at": sg.get("verified_at"),
                 "model_version": ident["model_version"], "architecture": ident.get("architecture"),
                 "frozen_commit": ident.get("frozen_commit")}
        seen = {}
        for r in ctx.records:
            if r["game_id"] != gid:
                continue
            k = (r["player_id"], r["market"])
            row_codes = {c for c in str(r.get("suppression_reason") or "").split(";") if c} - set(codes) - {""}
            row_nonquote = row_codes - QUOTE_ONLY_REASONS
            if k not in seen:
                pub_ok = games[gid]["publication_allowed"] and not (row_nonquote - PRICED_BLOCKED_REASONS) \
                    and r.get("projection") is not None and r["market"] in SUPPORTED_MARKETS \
                    and _decide(row_nonquote)["publication_allowed"]
                reasons = sorted((codes - QUOTE_ONLY_REASONS) | row_nonquote)
                if pub_ok:                                       # actionability blocks are private, not suppression
                    reasons = sorted(set(reasons) - PRICED_BLOCKED_REASONS)
                seen[k] = {**gbase, "game": r["game"], "player_id": r["player_id"], "player_name": r["player"],
                           "team_id": r["team_id"], "team": ctx.teams.get(r["team_id"], r["team_id"]),
                           "market": r["market"], "projection": _num(r.get("projection")) if pub_ok else None,
                           "projection_basis": "MEAN_CONDITIONAL_ON_PLAYING",
                           "p_play": _num(r.get("p_play")), "pmf_q75": r.get("pmf_q75") if pub_ok else None,
                           "distribution_method": r.get("distribution_method"),
                           "feature_cutoff": _iso(r["feature_cutoff"]),
                           "status": "PUBLISHABLE" if pub_ok else "SUPPRESSED", "suppression_reasons": reasons,
                           "availability_status": r.get("availability_status"),
                           "availability_received_at": r.get("availability_received_at")}
                public.append(seen[k])
            if r.get("line") is None:
                continue
            qg = quote_gates(r, cp, tip, model_ok, pit["POINT_IN_TIME_LEGALITY"] == "PASS")
            price_ok = games[gid]["pricing_allowed"] and not (row_codes - PRICED_BLOCKED_REASONS) \
                and qg["QUOTE_QUALITY_GATE"] == "PASS" and seen[k]["status"] == "PUBLISHABLE"
            blocks = actionable_block_reasons(r, priced=price_ok, codes=codes | row_codes, checkpoint_at=cp, tip=tip,
                                              prediction_as_of=s["prediction_as_of"], availability=s.get("availability"),
                                              global_blocks=gblocks)
            state = "SUPPRESSED" if not price_ok else "PRICED_BLOCKED" if blocks else "PRICED_ACTIONABLE"
            over = r["side"] == "OVER"
            internal.append({**seen[k], "line": r["line"], "side": r["side"],
                             "p_over": r.get("probability") if over else None,
                             "p_under": r.get("probability") if not over else None,
                             "p_push": r.get("push_probability"),
                             "fair_decimal_over": r.get("fair_decimal") if over else None,
                             "fair_decimal_under": r.get("fair_decimal") if not over else None,
                             "fair_american_over": r.get("fair_american") if over else None,
                             "fair_american_under": r.get("fair_american") if not over else None,
                             "price_kind": {"fair": "MODEL_FAIR_PRICE", "executable": "SPORTSBOOK_EXECUTABLE_PRICE"},
                             "sportsbook": r.get("sportsbook"), "executable_decimal": r.get("best_decimal_odds"),
                             "executable_american": r.get("best_american_odds"),
                             "quote_available_at": r.get("quote_available_at"),
                             "collector_received_at": r.get("collector_received_at"),
                             "quote_age_seconds": None if r.get("quote_available_at") is None
                             else (cp - _utc(r["quote_available_at"])).total_seconds(),
                             # raw edge only against a real executable quote that passed every gate; never vs a fair price
                             "raw_edge_ev_per_unit": r.get("edge") if qg["QUOTE_QUALITY_GATE"] == "PASS" else None,
                             "mcse": r.get("mcse"), "QUOTE_QUALITY_GATE": qg["QUOTE_QUALITY_GATE"],
                             "failed_quote_gates": qg["failed"],
                             "pricing_status": "PRICING_ALLOWED" if price_ok else "PRICING_SUPPRESSED",
                             "board_state": state, "actionable_block_reasons": blocks,
                             "actionable_bet_allowed": state == "PRICED_ACTIONABLE",
                             "stake": 0.0})                     # no architect staking policy: never a stake
    pub_fields = PUBLIC_FIELDS + (PRIVATE_FIELDS if publish_private_fields else ())
    public = [{k: r.get(k) for k in pub_fields} for r in public]
    head = {"contract_version": CONTRACT_VERSION, "operational_mode": OPERATIONAL_MODE,
            "model_identity": {k: ident.get(k) for k in ("model_version", "architecture", "frozen_commit", "frozen_tag",
                                                        "artifact_sha256", "production_manifest")},
            "draws_per_game": DRAWS_PER_GAME, "supported_markets": list(SUPPORTED_MARKETS),
            "run_id": s["run_id"], "state_version": s["state_version"], "checkpoint_label": checkpoint_label,
            "checkpoint_at": cp.isoformat(), "prediction_as_of": _iso(s["prediction_as_of"]),
            "data_cutoff": ctx.state_manifest["source_cutoff"], "data_label": ctx.data_label,
            "schedule_freshness_policy": PC.SCHEDULE_FRESHNESS_POLICY}
    return {"public": envelope({**head, "board_kind": "PUBLIC_PROJECTION_BOARD", "rows": public,
                                "games": {g: {k: v[k] for k in ("publication_allowed", "POINT_IN_TIME_LEGALITY",
                                                                "SCHEDULE_FRESHNESS_GATE", "schedule_verified_at")}
                                          for g, v in games.items()}}),
            "internal": envelope({**head, "board_kind": "INTERNAL_PRICING_BOARD", "rows": internal,
                                  "betting_policy": pol, "real_money_actionability": PC.REAL_MONEY_ACTIONABILITY,
                                  "availability_g4b_status": PC.AVAILABILITY_G4B_STATUS, "games": games}),
            "game_status": games}


# ============================================================================ deterministic identity / idempotency
def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()


# re-verification instants: a re-send of the same content at a later checkpoint is a replay, not a new version
VOLATILE_FIELDS = frozenset({"checkpoint_at", "schedule_verified_at", "quote_age_seconds"})


def _stable(x):
    if isinstance(x, dict):
        return {k: _stable(v) for k, v in x.items() if k not in VOLATILE_FIELDS}
    if isinstance(x, list):
        return [_stable(v) for v in x]
    return x


def envelope(board: dict) -> dict:
    """publication_id = the LOGICAL slot (board kind, game set, checkpoint label); payload_sha256 = exact content;
    idempotency_key = slot + content. Same board twice -> same key (one logical publication); a materially different
    board for the same slot -> same publication_id, new key, supersession lineage."""
    games = sorted(board.get("games", {}))
    slot = {"contract_version": board["contract_version"], "board_kind": board["board_kind"], "games": games,
            "checkpoint_label": board["checkpoint_label"]}
    pid = "PUB-" + hashlib.sha256(canonical_json(slot)).hexdigest()[:24]
    psha = hashlib.sha256(canonical_json(_stable(board))).hexdigest()
    return {**board, "publication_id": pid, "payload_sha256": psha,
            "idempotency_key": hashlib.sha256(f"{pid}|{psha}".encode()).hexdigest()}


# ============================================================================ publication ledger + transports
class PublicationRejected(RuntimeError):
    def __init__(self, reason: str, msg: str = ""):
        super().__init__(f"{reason}: {msg}")
        self.reason = reason


class TransportPending(RuntimeError):
    pass


class PendingSiteConfirmationTransport:
    """The only production transport until the site owner confirms one (WIZARDOFODDS_INPUTS_REQUIRED)."""
    name = PUBLISH_TRANSPORT
    public = True

    def send(self, payload: dict) -> dict:
        raise TransportPending("WizardOfOdds CBB publication transport is PENDING_SITE_CONFIRMATION")

    def read_back(self, publication_id: str, idempotency_key: str):
        raise TransportPending("WizardOfOdds CBB publication transport is PENDING_SITE_CONFIRMATION")


class LocalStagingTransport:
    """Local, private staging target used by tests: content-addressed files keyed by idempotency key."""
    name = "LOCAL_STAGING"
    public = False                                          # private: the rights gate applies to public transports

    def __init__(self, root: Path):
        self.root = Path(root)

    def send(self, payload: dict) -> dict:
        d = self.root / payload["publication_id"]
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{payload['idempotency_key']}.json"
        if not p.exists():
            p.write_bytes(canonical_json(payload))
        return {"stored": str(p)}

    def read_back(self, publication_id: str, idempotency_key: str):
        p = self.root / publication_id / f"{idempotency_key}.json"
        return json.loads(p.read_text()) if p.exists() else None


def _receipts(path: Path) -> list[dict]:
    p = Path(path)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def publish(payload: dict, transport, receipts_path: Path, *, now, tips: dict | None = None) -> dict:
    """Idempotent, append-only publication. Returns the receipt (status PUBLISHED | DUPLICATE_NOOP | SUPERSEDES).
    Refuses (PublicationRejected) a payload whose hash does not match its content, any post-tip game, or a payload
    with no publishable row; a transport failure or a read-back mismatch is recorded and raised (retry is safe)."""
    t = _utc(now)
    body = {k: v for k, v in payload.items() if k not in ("publication_id", "payload_sha256", "idempotency_key")}
    if envelope(body)["payload_sha256"] != payload["payload_sha256"] or \
            envelope(body)["idempotency_key"] != payload["idempotency_key"]:
        raise PublicationRejected("DUPLICATE_PUBLICATION_CONFLICTING_PAYLOAD", "payload does not match its hash")
    tips = tips if tips is not None else {r["canonical_game_id"]: r.get("scheduled_tip_utc") for r in payload["rows"]}
    if any(v is None or t >= _utc(v) for v in tips.values()):
        raise PublicationRejected("GAME_ALREADY_STARTED", "no publication at or after tip")
    if getattr(transport, "public", True):
        rg = rights_gate()
        if rg["COMMERCIAL_RIGHTS_GATE"] != "PASS":
            raise PublicationRejected("COMMERCIAL_RIGHTS_UNVERIFIED", json.dumps(rg["unapproved_sources"]))
    if payload["board_kind"] == "PUBLIC_PROJECTION_BOARD" and not any(r["status"] == "PUBLISHABLE" for r in payload["rows"]):
        raise PublicationRejected("NOTHING_PUBLISHABLE", "every row is suppressed")
    prior = [r for r in _receipts(receipts_path) if r["publication_id"] == payload["publication_id"]
             and r["status"] in ("PUBLISHED", "SUPERSEDES")]
    if any(r["idempotency_key"] == payload["idempotency_key"] for r in prior):
        rec = {"status": "DUPLICATE_NOOP", "publication_id": payload["publication_id"],
               "idempotency_key": payload["idempotency_key"], "at": t.isoformat()}
        _append(receipts_path, rec)
        return rec
    version = len(prior) + 1
    try:
        transport.send(payload)
        back = transport.read_back(payload["publication_id"], payload["idempotency_key"])
    except TransportPending:
        raise
    except Exception as e:                                    # noqa: BLE001 -- recorded; nothing is marked published
        _append(receipts_path, {"status": "PUBLICATION_FAILURE", "publication_id": payload["publication_id"],
                                "idempotency_key": payload["idempotency_key"], "at": t.isoformat(),
                                "error": f"{type(e).__name__}: {str(e)[:300]}"})
        raise PublicationRejected("PUBLICATION_FAILURE", str(e)[:300]) from e
    if back is None or back.get("payload_sha256") != payload["payload_sha256"]:
        _append(receipts_path, {"status": "PUBLICATION_RECONCILIATION_MISMATCH", "publication_id": payload["publication_id"],
                                "idempotency_key": payload["idempotency_key"], "at": t.isoformat()})
        raise PublicationRejected("PUBLICATION_RECONCILIATION_MISMATCH", "read-back differs from the sent payload")
    rec = {"status": "PUBLISHED" if not prior else "SUPERSEDES", "publication_id": payload["publication_id"],
           "version": version, "idempotency_key": payload["idempotency_key"], "payload_sha256": payload["payload_sha256"],
           "supersedes": prior[-1]["idempotency_key"] if prior else None, "transport": transport.name,
           "at": t.isoformat(), "read_back": "MATCH"}
    _append(receipts_path, rec)
    return rec


def _append(path: Path, rec: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def production_readiness(gates: dict) -> dict:
    """CBB_ORIGINATOR_PRODUCTION_READINESS is claimed only when every listed gate is PASS."""
    ok = bool(gates) and all(v == "PASS" for v in gates.values())
    return {"CBB_ORIGINATOR_PRODUCTION_MODE": OPERATIONAL_MODE,
            "CBB_ORIGINATOR_PRODUCTION_READINESS": "READY_FOR_REAL_2026_27_PREDICTION_AND_PRICING" if ok else "NOT_READY",
            "failing": sorted(k for k, v in gates.items() if v != "PASS")}


__all__ = ["build_boards", "load_run", "point_in_time", "quote_gates", "betting_policy", "envelope", "publish",
           "FAIL_CLOSED_MATRIX", "condition_of", "production_readiness", "actionable_block_reasons",
           "global_actionable_blocks"]
