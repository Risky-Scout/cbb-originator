"""RotoWire CBB player availability -> the existing availability interface (prod/inputs.apply_availability).

AVAILABILITY CONDITIONING ONLY. Nothing here fits, refits or changes JM1 (marginals, minutes, dispersion, dependence,
feature set). A status never becomes a numeric P(play): confirmed OUT-like states are unavailable, confirmed ACTIVE is
available, every uncertain state is preserved verbatim beside the model's own base P(play) and gates the row.

Sources (prod/rotowire_acquire.py):
  * News/Injuries.php -- append-only timestamped injury updates: the PRIMARY point-in-time evidence;
  * Injuries.php      -- the current injury list: reconciliation only; its changes are recorded as SNAPSHOT_OBSERVATION
                         events (collected_at only), never written back over a news update;
  * News/Updates.php  -- optional, disabled by default; only records carrying a structured status are used.
Notes / Analysis text is never copied into an event and never reaches a model (it stays in the licensed raw cache).

Point in time (POINT_IN_TIME_POLICY v2.0 §3.1, docs/AVAILABILITY_SOURCE_GATES_G4A_G4B.md item 7):
  knowable_at = max(collected_at, source_datetime)  -- a late-collected update is never backdated to its source time;
  an event is usable at T only if knowable_at < T.  DNP (POSTGAME_ONLY) is never pregame evidence, for any game.

Identity (strict, never fuzzy; prod/matching.py normalization): explicit curated maps first, then the exact normalized
name inside the resolved team's current roster. RESOLVED / AMBIGUOUS / UNMATCHED; only RESOLVED events affect a
player; an unresolved record with a non-ACTIVE status fails its games closed (AVAILABILITY_IDENTITY_UNRESOLVED).

Field paths are the directive's field list (Player.Id, SportDataIOId, Team.Id, Injury.*, DateTime, LastEdited,
Priority). They are NOT yet verified against a live CBB response (SCHEMA_VERIFIED = False): a record without its
required fields is MALFORMED and never becomes an event; a response that is not a record list fails the poll.

    .venv/bin/python -m cbb_platform.prod.rotowire_feed poll    [--force]   one poll if due (snapshot + news)
    .venv/bin/python -m cbb_platform.prod.rotowire_feed smoke               ONE read-only live smoke (counts only)
    .venv/bin/python -m cbb_platform.prod.rotowire_feed status
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from cbb_platform.prod import config as PC, matching as MT, rotowire_acquire as RA

PROVIDER = "ROTOWIRE"
SCHEMA_VERIFIED = False

# ============================================================================ status contract (directive §3)
STATUS_MAP = {"ACT": "ACTIVE", "OUT": "OUT", "GTD": "GTD", "Q": "QUESTIONABLE", "D": "DOUBTFUL", "SUSP": "SUSPENDED",
              "NA": "UNAVAILABLE", "IR": "LONG_TERM_OUT", "PUP": "LONG_TERM_OUT", "IR-R": "RETURN_DESIGNATION",
              "DNP": "POSTGAME_ONLY"}
OUT_LIKE = ("OUT", "SUSPENDED", "UNAVAILABLE", "LONG_TERM_OUT")
# Architect ruling 2026-10-31: an UNRESOLVED status is PRICED_BUT_NON_ACTIONABLE (projection, PMF, fair price and a
# diagnostic edge are produced with the model's own P(play); never actionable). UNKNOWN (an unrecognized provider code)
# is not a ruling status: it stays suppressed (fail closed), as does a snapshot/news conflict.
UNRESOLVED = ("GTD", "QUESTIONABLE", "DOUBTFUL", "RETURN_DESIGNATION")
UNCERTAIN = UNRESOLVED + ("UNKNOWN",)
POSTGAME = ("POSTGAME_ONLY",)
NOT_LISTED = "NOT_LISTED"           # snapshot observation: a previously listed player left the current injury list
NEWS_KINDS = ("NEWS_UPDATE", "UPDATES_STREAM")
KIND_OF = {"news_injuries": "NEWS_UPDATE", "news_updates": "UPDATES_STREAM", "snapshot": "SNAPSHOT_OBSERVATION"}
GATE_UNCERTAIN = "AVAILABILITY_UNCERTAIN"
GATE_CONFLICT = "AVAILABILITY_SOURCE_CONFLICT"
GATE_UNRECOGNIZED = "AVAILABILITY_STATUS_UNRECOGNIZED"


def normalize_status(raw) -> str:
    """Exact provider codes only (case / whitespace insensitive). Null, empty or any other value -> UNKNOWN."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "UNKNOWN"
    return STATUS_MAP.get(str(raw).strip().upper(), "UNKNOWN")


def interface_status(normalized: str) -> str:
    """Status written into the inputs.apply_availability frame (OUT-like states are inputs.UNAVAILABLE)."""
    if normalized in POSTGAME:
        raise AvailabilityLeakage("POSTGAME_ONLY (DNP) can never be pregame availability evidence")
    return "NO_REPORT" if normalized == NOT_LISTED else normalized


class AvailabilityLeakage(RuntimeError):
    pass


class MalformedResponse(RuntimeError):
    pass


# ============================================================================ schema (directive field list; unverified)
LIST_KEYS = ("Updates", "Injuries", "Players", "News", "Items", "Data")
COMMON = {
    "rotowire_player_id": ("Player.Id", "PlayerId"),
    "sportdataio_player_id": ("Player.SportDataIOId", "Player.SportsDataIOId", "SportDataIOId", "SportsDataIOId"),
    "first_name": ("Player.FirstName", "FirstName"), "last_name": ("Player.LastName", "LastName"),
    "position": ("Player.Position", "Position"),
    "rotowire_team_id": ("Team.Id", "Player.Team.Id", "TeamId"),
    "team_name": ("Team.Name", "Team.School", "Player.Team.Name", "TeamName"),
    "team_code": ("Team.Code", "Player.Team.Code", "TeamCode"),
    "raw_status": ("Injury.Status", "Status"),
    "injury_type": ("Injury.Type",), "injury_location": ("Injury.Location",), "injury_detail": ("Injury.Detail",),
    "injury_side": ("Injury.Side",), "return_date": ("Injury.ReturnDate", "ReturnDate"),
    "priority": ("Priority",), "source_datetime_raw": ("DateTime", "Date", "Injury.Date"),
    "source_last_edited_raw": ("LastEdited",),
}
SCHEMA = {
    "news_injuries": {**COMMON, "rotowire_update_id": ("Id", "UpdateId")},
    "news_updates": {**COMMON, "rotowire_update_id": ("Id", "UpdateId")},
    "snapshot": {**COMMON, "rotowire_player_id": ("Player.Id", "PlayerId", "Id")},
}
REQUIRED = {"news_injuries": ("rotowire_update_id", "rotowire_player_id", "raw_status"),
            "news_updates": ("rotowire_update_id", "rotowire_player_id"),
            "snapshot": ("rotowire_player_id", "raw_status")}
STATUS_PATHS = COMMON["raw_status"]


def _path(rec: dict, path: str):
    cur = rec
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return KeyError
        cur = cur[k]
    return cur


def _first(rec: dict, paths) -> tuple[bool, object]:
    for p in paths:
        v = _path(rec, p)
        if v is not KeyError:
            return True, v
    return False, None


def records_of(body) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        hits = [k for k in LIST_KEYS if isinstance(body.get(k), list)]
        if len(hits) == 1:
            return body[hits[0]]
        if not hits and isinstance(body.get("Player"), dict):
            return [body]
    raise MalformedResponse("MALFORMED_RESPONSE: no single record list in the body")


def parse_ts(raw) -> tuple[str | None, str]:
    """ISO-8601 with an explicit offset -> UTC iso. A naive timestamp is never guessed into a zone."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, "MISSING"
    try:
        t = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return None, "UNPARSEABLE"
    if t.tzinfo is None:
        return None, "NAIVE_UNRESOLVED"
    return t.astimezone(timezone.utc).isoformat(), "OK"


def _txt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def extract(rec, endpoint: str) -> dict | None:
    """Structured fields of one record, or None when a required field is absent (MALFORMED_RECORD)."""
    if not isinstance(rec, dict):
        return None
    out, present = {}, {}
    for k, paths in SCHEMA[endpoint].items():
        present[k], out[k] = _first(rec, paths)
    if any(not present[k] for k in REQUIRED[endpoint]) or _txt(out["rotowire_player_id"]) is None:
        return None
    if endpoint == "news_updates" and not present["raw_status"]:
        return None                                      # a non-status news item carries no availability evidence
    for k in out:
        if k != "priority":
            out[k] = _txt(out[k])
    try:
        out["priority"] = None if out["priority"] is None else int(out["priority"])
    except (TypeError, ValueError):
        out["priority"] = None
    out["source_datetime"], out["source_datetime_status"] = parse_ts(out.pop("source_datetime_raw"))
    out["source_last_edited"], out["source_last_edited_status"] = parse_ts(out.pop("source_last_edited_raw"))
    out["normalized_status"] = normalize_status(out["raw_status"])
    out["record_sha256"] = hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()
    return out


# ============================================================================ identity (strict)
def position_group(pos) -> frozenset:
    s = (_txt(pos) or "").upper()
    g = set()
    for w, c in (("GUARD", "G"), ("FORWARD", "F"), ("CENTER", "C")):
        if w in s:
            g.add(c)
    if not g and len(s) <= 5:                            # provider codes: G, F, C, G-F, F/C
        g = {ch for ch in s if ch in "GFC"}
    return frozenset(g)


@dataclass
class IdentityContext:
    teams: pd.DataFrame                      # canonical_team_id, school, display_name, mascot
    roster: pd.DataFrame                     # canonical_player_id, canonical_team_id, first_name, last_name, player_name, position
    team_map: pd.DataFrame | None = None     # rotowire_team_id, canonical_team_id
    player_map: pd.DataFrame | None = None   # rotowire_player_id | sportdataio_player_id, canonical_player_id
    _tidx: dict = field(default=None, repr=False)
    _by_team: dict = field(default=None, repr=False)

    def __post_init__(self):
        t = self.teams.copy()
        if "mascot" not in t:
            t["mascot"] = None
        self._tidx = MT.team_index(t, None)
        r = self.roster.copy()
        full = (r["first_name"].fillna("") + " " + r["last_name"].fillna("")).str.strip()
        r["_n"] = [MT.norm(f) or MT.norm(p) for f, p in zip(full, r["player_name"])]
        r["_g"] = r["position"].map(position_group)
        self._by_team = {k: d for k, d in r.groupby("canonical_team_id")}
        self._roster = r

    def resolve_team(self, f: dict) -> tuple[str | None, str]:
        if self.team_map is not None and f.get("rotowire_team_id") is not None:
            ids = set(self.team_map.loc[self.team_map["rotowire_team_id"].astype(str) == str(f["rotowire_team_id"]),
                                        "canonical_team_id"])
            if len(ids) == 1:
                return next(iter(ids)), "EXPLICIT_MAP"
            if len(ids) > 1:
                return None, "AMBIGUOUS"
        if not f.get("team_name"):
            return None, "UNMATCHED"
        tid, how = MT.resolve_team(f["team_name"], self._tidx)
        return tid, "EXACT_NORMALIZED" if tid else how

    def resolve(self, f: dict) -> dict:
        tid, thow = self.resolve_team(f)
        out = {"canonical_team_id": tid, "team_resolution": thow, "canonical_player_id": None}
        mapped = set()
        if self.player_map is not None:
            for col in ("rotowire_player_id", "sportdataio_player_id"):
                if col in self.player_map and f.get(col) is not None:
                    mapped |= set(self.player_map.loc[self.player_map[col].astype(str) == str(f[col]),
                                                      "canonical_player_id"])
        if len(mapped) > 1:
            return {**out, "identity_resolution_status": "AMBIGUOUS", "identity_method": "EXPLICIT_MAP_CONFLICT"}
        if mapped:
            pid = next(iter(mapped))
            on = self._roster[self._roster["canonical_player_id"] == pid]
            if tid is not None:
                on = on[on["canonical_team_id"] == tid]
            if on["canonical_team_id"].nunique() == 1:
                return {**out, "canonical_team_id": on["canonical_team_id"].iat[0], "canonical_player_id": pid,
                        "identity_resolution_status": "RESOLVED", "identity_method": "EXPLICIT_MAP"}
            return {**out, "identity_resolution_status": "AMBIGUOUS" if len(on) else "UNMATCHED",
                    "identity_method": "EXPLICIT_MAP_NOT_ON_ROSTER"}
        if tid is None:
            return {**out, "identity_resolution_status": "AMBIGUOUS" if thow == "AMBIGUOUS" else "UNMATCHED",
                    "identity_method": f"TEAM_{thow}"}
        k = MT.norm(f"{f.get('first_name') or ''} {f.get('last_name') or ''}")
        c = self._by_team.get(tid)
        hits = c[c["_n"] == k] if (c is not None and k) else pd.DataFrame()
        if len(hits) > 1:
            return {**out, "identity_resolution_status": "AMBIGUOUS", "identity_method": "EXACT_NAME_MULTIPLE"}
        if len(hits) == 0:
            return {**out, "identity_resolution_status": "UNMATCHED", "identity_method": "EXACT_NAME_NONE"}
        g, rg = position_group(f.get("position")), hits["_g"].iat[0]
        if g and rg and not (g & rg):
            return {**out, "identity_resolution_status": "AMBIGUOUS", "identity_method": "EXACT_NAME_POSITION_CONFLICT"}
        return {**out, "canonical_player_id": hits["canonical_player_id"].iat[0], "identity_resolution_status": "RESOLVED",
                "identity_method": "EXACT_NAME_ON_TEAM_ROSTER"}


def load_identity_context(state_root: Path = PC.STATE_ROOT, history_db: Path = PC.HISTORY_CANONICAL,
                          map_root: Path = PC.ROTOWIRE_MAP_ROOT) -> IdentityContext:
    import duckdb
    from cbb_platform.prod import state as ST
    st = ST.current(state_root)
    if st is None:
        raise RuntimeError("no promoted 2026-27 state: no current roster to resolve RotoWire identities against")
    con = duckdb.connect(str(st["increment_db"]), read_only=True)
    roster = con.execute("""SELECT canonical_player_id, canonical_team_id, first_name, last_name, player_name, position
                            FROM flagship.rosters WHERE season = ?""", [PC.PRODUCTION_SEASON]).df()
    inc_teams = con.execute("SELECT canonical_team_id, school, display_name, NULL AS mascot FROM core.teams").df()
    con.close()
    con = duckdb.connect(str(history_db), read_only=True)
    teams = con.execute("""SELECT canonical_team_id, any_value(school) AS school, any_value(display_name) AS display_name,
                                  any_value(mascot) AS mascot FROM core.teams GROUP BY 1""").df()
    con.close()
    teams = pd.concat([teams, inc_teams[~inc_teams["canonical_team_id"].isin(teams["canonical_team_id"])]], ignore_index=True)
    teams["mascot"] = teams["mascot"].astype(object).where(teams["mascot"].notna(), None)   # pd.NA is not a truth value
    rd = lambda n: pd.read_csv(Path(map_root) / n, dtype=str) if (Path(map_root) / n).exists() else None
    return IdentityContext(teams, roster, rd("rotowire_team_map.csv"), rd("rotowire_player_map.csv"))


# ============================================================================ append-only event store
EVENT_FIELDS = ("provider", "event_kind", "event_id", "semantic_key", "rotowire_update_id", "rotowire_player_id",
                "sportdataio_player_id", "rotowire_team_id", "canonical_player_id", "canonical_team_id",
                "canonical_game_id", "first_name", "last_name", "position", "raw_status", "normalized_status", "priority",
                "injury_type", "injury_location", "injury_detail", "injury_side", "return_date", "source_datetime",
                "source_datetime_status", "source_last_edited", "source_last_edited_status", "collected_at_utc",
                "knowable_at", "source_endpoint", "raw_payload_sha256", "record_sha256", "identity_resolution_status",
                "identity_method", "team_resolution", "reconciliation", "feed_version")
SEMANTIC = ("event_kind", "rotowire_update_id", "rotowire_player_id", "raw_status", "normalized_status", "priority",
            "injury_type", "injury_location", "injury_detail", "injury_side", "return_date", "source_datetime",
            "source_last_edited")


class EventStore:
    """events.jsonl: one immutable line per new semantic event; lines are never rewritten or deleted."""

    def __init__(self, root: Path = PC.ROTOWIRE_ROOT):
        self.root = Path(root)
        self.path = self.root / "events.jsonl"

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def frame(self) -> pd.DataFrame:
        ev = self.load()
        return pd.DataFrame(ev, columns=list(EVENT_FIELDS) + ["seq"]) if ev else pd.DataFrame(columns=list(EVENT_FIELDS) + ["seq"])

    def append(self, events: list[dict]) -> int:
        """Append the events whose semantic key is new (idempotent). Returns the number written."""
        if not events:
            return 0
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / ".lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            have = self.load()
            keys = {e["semantic_key"] for e in have}
            seq = len(have)
            new = []
            for e in events:
                if e["semantic_key"] in keys:
                    continue
                keys.add(e["semantic_key"])
                new.append({**e, "seq": seq})
                seq += 1
            if new:
                with self.path.open("a") as f:
                    for e in new:
                        f.write(json.dumps(e, sort_keys=True, default=str) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            return len(new)


def semantic_key(e: dict) -> str:
    return hashlib.sha256(json.dumps([e.get(k) for k in SEMANTIC], default=str).encode()).hexdigest()


def _event(f: dict, ident: dict, kind: str, endpoint: str, collected_at: str, raw_sha: str, **extra) -> dict:
    e = {k: None for k in EVENT_FIELDS}
    e.update({k: f.get(k) for k in EVENT_FIELDS if k in f})
    e.update(ident)
    sd = _txt(f.get("source_datetime")) if kind in NEWS_KINDS else None
    e.update({"provider": PROVIDER, "event_kind": kind, "source_endpoint": RA.PATHS[endpoint],
              "collected_at_utc": collected_at, "raw_payload_sha256": raw_sha,
              "knowable_at": max(collected_at, sd, key=lambda x: pd.Timestamp(x)) if sd else collected_at,
              "feed_version": PC.ROTOWIRE_FEED_VERSION, "canonical_game_id": None, **extra})
    e["semantic_key"] = semantic_key(e)
    e["event_id"] = "RW-" + e["semantic_key"][:20]
    return e


# ============================================================================ point-in-time state
def _ts(x):
    return pd.Timestamp(x) if x is not None and not (isinstance(x, float) and pd.isna(x)) else pd.NaT


def _order(ev: pd.DataFrame) -> pd.DataFrame:
    ev = ev.copy()
    ev["_eff"] = [(_ts(s) if k in NEWS_KINDS and _txt(s) else _ts(c)) for s, k, c in
                  zip(ev["source_datetime"], ev["event_kind"], ev["collected_at_utc"])]
    ev["_led"] = ev["source_last_edited"].map(lambda x: _ts(x) if _txt(x) else pd.Timestamp("1970-01-01", tz="UTC"))
    ev["_col"] = ev["collected_at_utc"].map(_ts)
    return ev.sort_values(["_eff", "_led", "_col", "seq"], kind="mergesort")


def usable(events: pd.DataFrame, as_of) -> pd.DataFrame:
    """Events legally knowable strictly before as_of. POSTGAME_ONLY (DNP) never passes."""
    if events.empty:
        return events
    T = pd.Timestamp(as_of)
    T = T.tz_localize("UTC") if T.tzinfo is None else T.tz_convert("UTC")
    k = events["knowable_at"].map(_ts)
    c = events["collected_at_utc"].map(_ts)
    return events[(k < T) & (c < T) & ~events["normalized_status"].isin(POSTGAME)]


def player_state(events: pd.DataFrame, as_of) -> pd.DataFrame:
    """One row per rotowire_player_id: the effective pregame status at as_of (news primary, snapshot reconciliation)."""
    u = usable(events, as_of)
    cols = ["rotowire_player_id", "effective_status", "gate", "news_status", "snapshot_status", "knowable_at",
            "canonical_player_id", "canonical_team_id", "identity_resolution_status", "rotowire_update_id", "raw_status",
            "priority", "return_date"]
    if u.empty:
        return pd.DataFrame(columns=cols)
    u = _order(u)
    out = []
    for pid, g in u.groupby("rotowire_player_id", sort=True):
        news = g[g["event_kind"].isin(NEWS_KINDS)]
        snap = g[g["event_kind"] == "SNAPSHOT_OBSERVATION"]
        n = news.iloc[-1] if len(news) else None
        s = snap.iloc[-1] if len(snap) else None
        gate = ""
        if n is not None and (s is None or s["_col"] <= _ts(n["knowable_at"])):
            eff, src = n["normalized_status"], n
        elif n is None:
            eff, src = s["normalized_status"], s
        else:                                            # a newer snapshot than the latest news update
            same = s["normalized_status"] == n["normalized_status"] or (
                s["normalized_status"] == NOT_LISTED and n["normalized_status"] == "ACTIVE")
            eff, src = (n["normalized_status"], n) if same else ("UNKNOWN", s)
            gate = "" if same else GATE_CONFLICT
        if eff in UNRESOLVED and not gate:
            gate = GATE_UNCERTAIN
        elif eff == "UNKNOWN" and not gate:
            gate = GATE_UNRECOGNIZED
        last = g.iloc[-1]                                # the most recent identity resolution for this provider id
        out.append({"rotowire_player_id": pid, "effective_status": eff, "gate": gate,
                    "news_status": None if n is None else n["normalized_status"],
                    "snapshot_status": None if s is None else s["normalized_status"], "knowable_at": src["knowable_at"],
                    "canonical_player_id": last["canonical_player_id"], "canonical_team_id": last["canonical_team_id"],
                    "identity_resolution_status": last["identity_resolution_status"],
                    "rotowire_update_id": src["rotowire_update_id"], "raw_status": src["raw_status"],
                    "priority": src["priority"], "return_date": src["return_date"]})
    return pd.DataFrame(out, columns=cols)


def availability_frame(events: pd.DataFrame, games: pd.DataFrame, as_of) -> tuple[pd.DataFrame, dict]:
    """games: canonical_game_id, home_team_id, away_team_id, feature_cutoff. Returns (the inputs.apply_availability
    frame, {game_id: [reasons]}). Each game uses the state at min(as_of, its feature cutoff)."""
    rows, reasons = [], {}
    aso = pd.Timestamp(as_of)
    aso = aso.tz_localize("UTC") if aso.tzinfo is None else aso.tz_convert("UTC")
    for g in games.itertuples(index=False):
        T = min(aso, pd.Timestamp(g.feature_cutoff))
        stt = player_state(events, T)
        teams = {g.home_team_id, g.away_team_id}
        reasons[g.canonical_game_id] = []
        bad = stt[(stt["identity_resolution_status"] != "RESOLVED")
                  & ~stt["effective_status"].isin(("ACTIVE", NOT_LISTED))
                  & (stt["canonical_team_id"].isna() | stt["canonical_team_id"].isin(teams))]
        if len(bad):
            reasons[g.canonical_game_id].append("AVAILABILITY_IDENTITY_UNRESOLVED")
        ok = stt[(stt["identity_resolution_status"] == "RESOLVED") & stt["canonical_team_id"].isin(teams)]
        for r in ok.itertuples(index=False):
            rows.append({"game_id": g.canonical_game_id, "player_id": r.canonical_player_id, "team_id": r.canonical_team_id,
                         "status": interface_status(r.effective_status), "received_at": pd.Timestamp(r.knowable_at),
                         "availability_source": PROVIDER, "availability_raw_status": r.raw_status,
                         "availability_gate": r.gate, "rotowire_player_id": r.rotowire_player_id,
                         "rotowire_update_id": r.rotowire_update_id, "news_status": r.news_status,
                         "snapshot_status": r.snapshot_status})
    fr = pd.DataFrame(rows, columns=["game_id", "player_id", "team_id", "status", "received_at", "availability_source",
                                     "availability_raw_status", "availability_gate", "rotowire_player_id",
                                     "rotowire_update_id", "news_status", "snapshot_status"])
    if fr["status"].isin(POSTGAME).any():                # hard guard (interface_status already refuses)
        raise AvailabilityLeakage("POSTGAME_ONLY reached the pregame availability frame")
    return fr, reasons


# ============================================================================ freshness (policy §8 bounds)
def freshness_bound_minutes(minutes_to_tip: float) -> int:
    for lo, bound in PC.AVAILABILITY_FRESHNESS_BOUNDS_MINUTES:
        if minutes_to_tip >= lo:
            return bound
    return PC.AVAILABILITY_FRESHNESS_BOUNDS_MINUTES[-1][1]


NEWS_COVERAGE_GAP = "ROTOWIRE_WEB_NEWS_COVERAGE_GAP"   # a news window that does not overlap the previous one
NEWS_GAP_STATUS = "COVERAGE_GAP"                       # request-ledger status of such a news acquisition


def news_gap_open(entries: list[dict], as_of) -> datetime | None:
    """collected_at of the latest news COVERAGE_GAP at or before as_of that no later PROCESSED news acquisition has
    closed, else None. While a gap is open, items may have been missed: every game fails closed (never assume none)."""
    lim = RA._utc(pd.Timestamp(as_of).to_pydatetime())
    gaps = [RA._utc(e["collected_at"]) for e in entries if e.get("endpoint") == "news_injuries"
            and e.get("status") == NEWS_GAP_STATUS and e.get("collected_at") and RA._utc(e["collected_at"]) <= lim]
    if not gaps:
        return None
    g, ok = max(gaps), RA.last_success(entries, "news_injuries", lim)
    return g if ok is None or ok <= g else None


def freshness(entries: list[dict], games: pd.DataFrame, as_of) -> dict:
    """{game_id: [reasons]} + detail. Both the news feed and the snapshot must have a PROCESSED acquisition, and no
    news coverage gap may be open (news_gap_open)."""
    aso = RA._utc(pd.Timestamp(as_of).to_pydatetime())
    last = {ep: RA.last_success(entries, ep, aso) for ep in ("news_injuries", "snapshot")}
    gap = news_gap_open(entries, aso)
    out, detail = {}, {}
    for g in games.itertuples(index=False):
        tip = RA._utc(pd.Timestamp(g.start_time).to_pydatetime())
        mtt = (tip - aso).total_seconds() / 60.0
        bound = freshness_bound_minutes(mtt)
        if gap is not None:
            out[g.canonical_game_id] = [NEWS_COVERAGE_GAP]
            age = None
        elif any(v is None for v in last.values()):
            out[g.canonical_game_id] = ["AVAILABILITY_FEED_UNAVAILABLE"]
            age = None
        else:
            age = (aso - min(last.values())).total_seconds() / 60.0
            out[g.canonical_game_id] = ["AVAILABILITY_STALE"] if age > bound else []
        detail[g.canonical_game_id] = {"minutes_to_tip": round(mtt, 1), "bound_minutes": bound,
                                       "acquisition_age_minutes": None if age is None else round(age, 1)}
    return {"game_reasons": out, "detail": detail, "news_coverage_gap_since": gap.isoformat() if gap else None,
            "last_success": {k: (v.isoformat() if v else None) for k, v in last.items()}}


# ============================================================================ ingest + poll
def ingest(fetch: dict, endpoint: str, ctx: IdentityContext, store: EventStore, session: RA.Session) -> dict:
    """One fetched response -> new events (idempotent). Logs a PROCESSED (or MALFORMED_RESPONSE) ledger entry."""
    collected = fetch["collected_at"]
    base = {"endpoint": endpoint, "collected_at": collected, "raw_payload_sha256": fetch["raw_payload_sha256"]}
    try:
        recs = records_of(fetch["body"])
    except MalformedResponse as e:
        session._log({**base, "status": "MALFORMED_RESPONSE", "error": str(e)})
        return {"endpoint": endpoint, "status": "MALFORMED_RESPONSE", "records": 0}
    parsed, malformed = [], 0
    for r in recs:
        f = extract(r, endpoint)
        if f is None:
            malformed += 1
            continue
        parsed.append(f)
    return ingest_parsed(parsed, len(recs), malformed, fetch, endpoint, ctx, store, session)


def ingest_parsed(parsed: list[dict], n_records: int, malformed: int, fetch: dict, endpoint: str, ctx, store: EventStore,
                  session, extra: dict | None = None) -> dict:
    """Already-extracted records (extract() field layout) -> new events (idempotent) + a PROCESSED ledger entry.
    The one reconciliation path for every acquisition source: the API adapter (ingest above) and the authenticated web
    adapter (prod/rotowire_web_acquire.py) both land here. `extra` adds provenance fields to every event (e.g. the
    web source path); it never changes a status, an ordering key or a semantic key field."""
    extra = dict(extra or {})
    collected = fetch["collected_at"]
    base = {"endpoint": endpoint, "collected_at": collected, "raw_payload_sha256": fetch["raw_payload_sha256"]}
    kind = KIND_OF[endpoint]
    events = []
    if endpoint == "snapshot":
        prior = store.frame()
        prior = prior[prior["collected_at_utc"].map(_ts) <= _ts(collected)] if len(prior) else prior
        last_snap = {}
        if len(prior):
            ps = _order(prior[prior["event_kind"] == "SNAPSHOT_OBSERVATION"])
            last_snap = ps.groupby("rotowire_player_id").tail(1).set_index("rotowire_player_id").to_dict("index")
            pn = _order(prior[prior["event_kind"].isin(NEWS_KINDS)])
            last_news = pn.groupby("rotowire_player_id").tail(1).set_index("rotowire_player_id")["normalized_status"].to_dict()
        else:
            last_news = {}
        seen = set()
        for f in parsed:
            pid = f["rotowire_player_id"]
            seen.add(pid)
            prev = last_snap.get(pid)
            if prev is not None and prev["normalized_status"] == f["normalized_status"] and prev["raw_status"] == f["raw_status"]:
                continue
            rec = "MATCHES_LATEST_NEWS" if last_news.get(pid) == f["normalized_status"] else "NO_MATCHING_NEWS_UPDATE"
            e = _event({**f, "source_datetime": None}, ctx.resolve(f), kind, endpoint, collected, fetch["raw_payload_sha256"],
                       reconciliation=rec, **extra)
            e["semantic_key"] = semantic_key({**e, "source_datetime": collected})   # a snapshot change is a new observation
            e["event_id"] = "RW-" + e["semantic_key"][:20]
            events.append(e)
        for pid, prev in last_snap.items():
            if pid not in seen and prev["normalized_status"] != NOT_LISTED:
                f = {k: prev.get(k) for k in ("sportdataio_player_id", "rotowire_team_id", "first_name", "last_name",
                                               "position")}
                f.update(rotowire_player_id=pid, raw_status=None, normalized_status=NOT_LISTED, record_sha256=None)
                ident = {k: prev.get(k) for k in ("canonical_player_id", "canonical_team_id", "identity_resolution_status",
                                                  "identity_method", "team_resolution")}
                e = _event(f, ident, kind, endpoint, collected, fetch["raw_payload_sha256"], reconciliation="LEFT_CURRENT_LIST",
                           **extra)
                e["semantic_key"] = semantic_key({**e, "source_datetime": collected})
                e["event_id"] = "RW-" + e["semantic_key"][:20]
                events.append(e)
    else:
        events = [_event(f, ctx.resolve(f), kind, endpoint, collected, fetch["raw_payload_sha256"], **extra) for f in parsed]
    written = store.append(events)
    status = pd.Series([e["normalized_status"] for e in events]).value_counts().to_dict() if events else {}
    ident = pd.Series([e["identity_resolution_status"] for e in events]).value_counts().to_dict() if events else {}
    session._log({**base, "status": "PROCESSED", "records": n_records, "malformed_records": malformed,
                  "events_new": written})
    return {"endpoint": endpoint, "status": "PROCESSED", "records": n_records, "malformed_records": malformed,
            "events_considered": len(events), "events_new": written, "status_counts": status, "identity_counts": ident,
            "timestamp_status": pd.Series([f["source_datetime_status"] for f in parsed]).value_counts().to_dict()}


def poll(session: RA.Session, ctx: IdentityContext, store: EventStore) -> dict:
    """News first (primary), then the snapshot (reconciliation). A provider failure is recorded, never raised: the
    freshness gate then fails the affected games closed."""
    eps = ["news_injuries", "snapshot"] + (["news_updates"] if PC.ROTOWIRE_INCLUDE_UPDATES else [])
    out = {}
    for ep in eps:
        try:
            out[ep] = ingest(session.get(ep), ep, ctx, store, session)
        except (RA.RotoWireProviderFailure, RA.RotoWireBudgetExceeded) as e:
            out[ep] = {"endpoint": ep, "status": "PROVIDER_FAILURE", "error": str(e)}
    return out


def poll_mode(now, tips) -> tuple[str, int]:
    """Cadence (PROPOSED_NOT_APPROVED): CRITICAL_PRE_TIP inside the window before any tip, GAME_DAY when a tip is within
    24 h, else OFF_DAY."""
    t = pd.Timestamp(now)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    ahead = [(pd.Timestamp(x) - t).total_seconds() / 3600.0 for x in tips]
    ahead = [h for h in ahead if h > 0]
    mode = ("CRITICAL_PRE_TIP" if any(h <= PC.ROTOWIRE_CRITICAL_WINDOW_HOURS for h in ahead)
            else "GAME_DAY" if any(h <= 24 for h in ahead) else "OFF_DAY")
    return mode, PC.ROTOWIRE_POLL_MINUTES[mode]


def due(entries: list[dict], now, tips) -> dict:
    mode, minutes = poll_mode(now, tips)
    last = [RA.last_success(entries, ep, now) for ep in ("news_injuries", "snapshot")]
    t = RA._utc(pd.Timestamp(now).to_pydatetime())
    stale = any(x is None or (t - x) >= timedelta(minutes=minutes) for x in last)
    return {"mode": mode, "interval_minutes": minutes, "due": stale}


def smoke(session: RA.Session, ctx: IdentityContext | None, store: EventStore) -> dict:
    """ONE read-only pass over the two primary endpoints. Reports counts and schema presence only (no text)."""
    rep = {}
    for ep in ("snapshot", "news_injuries"):
        try:
            f = session.get(ep)
        except (RA.RotoWireProviderFailure, RA.RotoWireBudgetExceeded) as e:
            rep[ep] = {"http": "FAILURE", "error": str(e)[:200]}
            continue
        try:
            recs = records_of(f["body"])
        except MalformedResponse as e:
            rep[ep] = {"http": f["http_status"], "error": str(e)}
            continue
        fields = {k: sum(1 for r in recs if isinstance(r, dict) and _first(r, p)[0]) for k, p in SCHEMA[ep].items()}
        parsed = [x for x in (extract(r, ep) for r in recs) if x is not None]
        ident = (pd.Series([ctx.resolve(x)["identity_resolution_status"] for x in parsed]).value_counts().to_dict()
                 if ctx is not None and parsed else "NO_IDENTITY_CONTEXT")
        rep[ep] = {"http": f["http_status"], "endpoint": RA.PATHS[ep], "records": len(recs), "parsed": len(parsed),
                   "field_presence": fields,
                   "status_counts": pd.Series([x["normalized_status"] for x in parsed]).value_counts().to_dict(),
                   "unknown_raw_status_values": int(sum(1 for x in parsed if x["normalized_status"] == "UNKNOWN")),
                   "identity_counts": ident,
                   "timestamp_parse": pd.Series([x["source_datetime_status"] for x in parsed]).value_counts().to_dict()}
    return rep


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.rotowire_feed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll")
    p.add_argument("--force", action="store_true")
    p.add_argument("--tips", nargs="*", default=[])
    sub.add_parser("smoke")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    store = EventStore()
    if a.cmd == "status":
        ev = store.frame()
        print(json.dumps({"events": int(len(ev)), "by_kind": ev["event_kind"].value_counts().to_dict() if len(ev) else {},
                          "by_identity": ev["identity_resolution_status"].value_counts().to_dict() if len(ev) else {},
                          "due": due(RA.ledger(), datetime.now(timezone.utc), a.tips if hasattr(a, "tips") else [])},
                         indent=1, default=str))
        return
    if not os.environ.get(RA.KEY_ENV):
        print(json.dumps({"ROTOWIRE_LIVE_TEST": "BLOCKED_NO_KEY"}))
        return
    try:
        ctx = load_identity_context()
    except Exception as e:                               # noqa: BLE001 -- smoke still reports schema without identity
        ctx = None
        print(json.dumps({"identity_context": f"unavailable ({type(e).__name__})"}))
    sess = RA.Session(a.cmd)
    if a.cmd == "smoke":
        print(json.dumps(smoke(sess, ctx, store), indent=1, default=str))
        return
    d = due(RA.ledger(), datetime.now(timezone.utc), a.tips)
    if not (a.force or d["due"]):
        print(json.dumps({"poll": "NOT_DUE", **d}))
        return
    if ctx is None:
        raise SystemExit("no identity context: refusing to ingest unresolvable events")
    print(json.dumps(poll(sess, ctx, store), indent=1, default=str))


if __name__ == "__main__":
    main()
