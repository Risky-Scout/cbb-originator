"""PRODUCTION-ONLY The Odds API GAME-LINE acquisition (Release B, SHADOW): spreads, totals, moneylines, team totals and
alternates for 2026-27 NCAAB. Separate session, guard, endpoints, cache folders, ledger purpose and credit budget from
the certified player-prop path (prod.odds_acquire stays exactly as certified: no featured-market call there).

    events           GET /v4/sports/basketball_ncaab/events            0 credits (shared listing with the player path)
    game_odds        GET /v4/sports/basketball_ncaab/odds              bulk featured markets h2h, spreads, totals
                     cost = 3 markets x region-equivalents (10 approved books = 1)  -> 3 credits per capture, all games
    game_event_odds  GET /v4/sports/basketball_ncaab/events/{id}/odds  team_totals, alternate_spreads, alternate_totals,
                     alternate_team_totals (cost = markets returned x region-equivalents; listed, pregame, <= 36 h)

Captures: ops/game_quotes/<ET date>/<capture_id>.parquet (normalised rows) + .json receipt. Quote state for a
forecast is the latest row per (game, book, market_type, side, point) received at or before the forecast instant.
CLOSE = the last row received before the scheduled tip within CLOSE_WINDOW_MIN; nothing received after tip is ever
used (no backfill after the cutoff is called contemporaneous).
"""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path

import pandas as pd

from cbb_platform.prod import config as PC, odds_acquire as OA, quotes as QT

SPORT = QT.SPORT_KEY
GAME_PATHS = {"events": f"/v4/sports/{SPORT}/events", "game_odds": f"/v4/sports/{SPORT}/odds",
              "game_event_odds": f"/v4/sports/{SPORT}/events/{{eventId}}/odds"}
CORE_KEYS = ("h2h", "spreads", "totals")
EVENT_KEYS = ("team_totals", "alternate_spreads", "alternate_totals", "alternate_team_totals")
KEY_TYPE = {"h2h": "MONEYLINE", "spreads": "SPREAD", "totals": "GAME_TOTAL", "alternate_spreads": "ALT_SPREAD",
            "alternate_totals": "ALT_TOTAL"}                       # team totals: HOME_/AWAY_ by the outcome's team
GAME_ALLOWED = {"events": OA.ALLOWED["events"],
                "game_odds": {"regions", "bookmakers", "markets", "oddsFormat", "dateFormat", "commenceTimeFrom",
                              "commenceTimeTo"},
                "game_event_odds": {"eventId", "regions", "bookmakers", "markets", "oddsFormat", "dateFormat"}}
GAME_MAX_CREDITS_PER_CAPTURE = 400
GAME_MAX_LIVE_REQUESTS_PER_CAPTURE = 80
CLOSE_WINDOW_MIN = 30
VERSION = "cbb-game-quotes-1.0.0"
COLS = ["capture_id", "game_id", "provider_event_id", "book", "market_key", "market_type", "side", "team_id", "point",
        "american", "decimal", "market_last_update", "collector_received_at", "quote_id"]


def american_to_decimal(a) -> float | None:
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(a) or a == 0 or -100 < a < 100:
        return None
    return 1 + (a / 100 if a > 0 else 100 / -a)


class GameSession(OA.Session):
    """odds_acquire.Session with the game-line allowlist (and nothing else)."""
    paths = GAME_PATHS

    def __init__(self, stage: str, **kw):
        kw.setdefault("max_live_requests", GAME_MAX_LIVE_REQUESTS_PER_CAPTURE)
        kw.setdefault("max_credits", GAME_MAX_CREDITS_PER_CAPTURE)
        super().__init__(stage, **kw)

    def _estimated_cost(self, endpoint, params):
        if endpoint == "events":
            return 0
        return len(set(OA._markets(params.get("markets", "")))) * OA.region_equivalents(params)

    def _check_request(self, endpoint, params, event_commence, now):
        if endpoint == "events":
            return OA.check_request(endpoint, params, event_commence, now)
        if endpoint not in GAME_ALLOWED:
            raise OA.OddsGuardViolation(f"GAME_ODDS_GUARD: endpoint {endpoint!r} not allowed")
        extra = sorted(set(params) - GAME_ALLOWED[endpoint])
        if extra:
            raise OA.OddsGuardViolation(f"GAME_ODDS_GUARD: parameters {extra} not allowed for {endpoint}")
        if params.get("oddsFormat") != "american" or params.get("dateFormat") != "iso":
            raise OA.OddsGuardViolation("GAME_ODDS_GUARD: oddsFormat=american and dateFormat=iso required")
        if ("regions" in params) == ("bookmakers" in params):
            raise OA.OddsGuardViolation("GAME_ODDS_GUARD: exactly one of regions / bookmakers")
        if "bookmakers" in params:
            bk = OA._markets(params["bookmakers"])
            if not bk or any(b not in PC.ODDS_BOOKMAKERS for b in bk) or len(bk) > PC.ODDS_BOOKMAKERS_PER_REGION:
                raise OA.OddsGuardViolation("GAME_ODDS_GUARD: bookmakers must be approved keys, <= 10")
        elif params["regions"] not in OA.ALLOWED_REGIONS:
            raise OA.OddsGuardViolation("GAME_ODDS_GUARD: region not allowed")
        mk = OA._markets(params.get("markets", ""))
        allowed = CORE_KEYS if endpoint == "game_odds" else EVENT_KEYS
        if not mk or any(m not in allowed for m in mk) or len(mk) != len(set(mk)):
            raise OA.OddsGuardViolation(f"GAME_ODDS_GUARD: {endpoint} markets must be distinct keys of {allowed}")
        a, b = OA._season_window()
        if endpoint == "game_odds":
            lo, hi = OA._utc(params["commenceTimeFrom"]), OA._utc(params["commenceTimeTo"])
            if not (a <= lo <= hi <= b) or OA._utc(now) > hi:
                raise OA.OddsGuardViolation("GAME_ODDS_GUARD: commence window outside 2026-27 / already past")
            return
        eid = str(params["eventId"])
        ct = event_commence(eid) if event_commence else None
        if not eid or "/" in eid or ct is None:
            raise OA.OddsGuardViolation(f"GAME_ODDS_GUARD: event {eid} not in an acquired 2026-27 listing")
        ct = OA._utc(ct)
        t = OA._utc(now)
        if not (a <= ct <= b) or t >= ct or (ct - t).total_seconds() / 3600 > PC.ODDS_PROP_WINDOW_HOURS:
            raise OA.OddsGuardViolation(f"GAME_ODDS_GUARD: event {eid} not pregame inside the window")

    def _check_response(self, endpoint, body, params):
        if endpoint == "events":
            return OA.check_response(endpoint, body, params)
        evs = body if endpoint == "game_odds" else [body]
        if endpoint == "game_odds" and not isinstance(body, list):
            raise OA.OddsGuardViolation("GAME_ODDS_GUARD: /odds response is not a list")
        if endpoint == "game_event_odds" and (not isinstance(body, dict) or str(body.get("id")) != str(params["eventId"])):
            raise OA.OddsGuardViolation("GAME_ODDS_GUARD: event odds response for a different event")
        a, b = OA._season_window()
        for e in evs:
            if e.get("sport_key") not in (None, SPORT):
                raise OA.OddsGuardViolation(f"GAME_ODDS_GUARD: sport_key {e.get('sport_key')!r}")
            if e.get("commence_time") and not (a <= OA._utc(e["commence_time"]) <= b):
                raise OA.OddsGuardViolation("GAME_ODDS_GUARD: event outside 2026-27")


def books_param() -> dict:
    return {"bookmakers": ",".join(PC.ODDS_BOOKMAKERS[:PC.ODDS_BOOKMAKERS_PER_REGION])}


# ============================================================================ parse
def parse(body_events: list, games: dict, tidx: dict, received_at, capture_id: str) -> pd.DataFrame:
    """Normalise Odds API events into game-quote rows. `games` = {provider_event_id: (game_id, home_team_id,
    away_team_id)} for matched events only; outcome team names resolve through the committed team map, so a neutral
    site listed home/away-swapped still prices the right canonical side."""
    from cbb_platform.prod import matching as MT
    rows = []
    for ev in body_events:
        eid = str(ev.get("id"))
        if eid not in games:
            continue
        gid, home, away = games[eid]
        side_of = lambda name: (lambda t: "HOME" if t == home else "AWAY" if t == away else None)(  # noqa: E731
            MT.resolve_team(name, tidx)[0])
        for bk in ev.get("bookmakers") or []:
            for m in bk.get("markets") or []:
                key = m.get("key")
                for o in m.get("outcomes") or []:
                    name, price, point = o.get("name"), o.get("price"), o.get("point")
                    if key in ("h2h", "spreads", "alternate_spreads"):
                        side = side_of(name)
                        team = home if side == "HOME" else away if side == "AWAY" else None
                        mtype = KEY_TYPE[key]
                    elif key in ("totals", "alternate_totals"):
                        side, team, mtype = str(name).upper(), None, KEY_TYPE[key]
                    elif key in ("team_totals", "alternate_team_totals"):
                        who = side_of(o.get("description"))
                        side, team = str(name).upper(), home if who == "HOME" else away if who == "AWAY" else None
                        mtype = (("ALT_" if key.startswith("alternate") else "") + f"{who}_TEAM_TOTAL") if who else None
                    else:
                        continue
                    if side not in ("HOME", "AWAY", "OVER", "UNDER") or mtype is None:
                        continue
                    dec = american_to_decimal(price)
                    if dec is None or (key != "h2h" and point is None):
                        continue
                    rows.append({"capture_id": capture_id, "game_id": gid, "provider_event_id": eid,
                                 "book": bk.get("key"), "market_key": key, "market_type": mtype, "side": side,
                                 "team_id": team, "point": None if key == "h2h" else float(point),
                                 "american": float(price), "decimal": dec,
                                 "market_last_update": m.get("last_update") or bk.get("last_update"),
                                 "collector_received_at": pd.Timestamp(received_at).isoformat(),
                                 # team totals carry HOME/AWAY in the id: both teams' OVER 76.5 from one book in one
                                 # capture are two quotes (the 2026-11-21 audit found 40,170 shared ids)
                                 "quote_id": f"{eid}|{bk.get('key')}|{key}{':' + who if mtype.endswith('TEAM_TOTAL') else ''}"
                                             f"|{side}|{point}|{received_at}"})
    return pd.DataFrame(rows, columns=COLS)


# ============================================================================ capture
def capture(session: GameSession, now, slate: pd.DataFrame, tidx: dict, out_root: Path, extras: bool = True) -> dict:
    """One capture: free events listing, one bulk /odds call (core markets, every listed game), then the event call
    for team totals + alternates of every matched game still pregame. `slate` needs canonical_game_id, home_team_id,
    away_team_id, start_time (tip), neutral_site."""
    from cbb_platform.prod import odds_feed as OF
    now = pd.Timestamp(now)
    cid = "GQ" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    day = now.tz_convert("America/New_York").date().isoformat()
    out = Path(out_root) / day
    out.mkdir(parents=True, exist_ok=True)
    rec = {"version": VERSION, "capture_id": cid, "at": now.isoformat(), "status": "STARTED"}
    lo, hi = now, now + pd.Timedelta(hours=PC.ODDS_PROP_WINDOW_HOURS)
    try:
        # live (0 credits) every capture: it also reports the account quota every paid call is checked against
        lst = session.get("events", OF.events_params(lo, hi), max_age_seconds=0, purpose="events listing (0 credits)")
        session.event_commence = OF.event_commence_resolver(session.cache_root, now)
        ev = pd.DataFrame([{"provider_event_id": e.get("id"), "provider_home_team": e.get("home_team"),
                            "provider_away_team": e.get("away_team"),
                            "provider_commence_time": OA._utc(e.get("commence_time"))} for e in lst["body"]],
                          columns=OF.EVENT_COLS)
        mm = OF.match_events(ev, slate, tidx)
        matched = mm.dropna(subset=["canonical_game_id"])
        gmap = {}
        sl = slate.set_index("canonical_game_id")
        for r in matched.itertuples(index=False):
            g = sl.loc[r.canonical_game_id]
            gmap[str(r.provider_event_id)] = (r.canonical_game_id, g["home_team_id"], g["away_team_id"])
        p = {**books_param(), "markets": ",".join(CORE_KEYS), "oddsFormat": "american", "dateFormat": "iso",
             "commenceTimeFrom": OF._iso(lo.floor("h")), "commenceTimeTo": OF._iso(hi.ceil("h"))}
        core = session.get("game_odds", p, max_age_seconds=300, purpose="game lines: h2h/spreads/totals (bulk)")
        frames = [parse(core["body"], gmap, tidx, core["received_at"], cid)]
        n_ev = 0
        if extras:
            for eid, (gid, _, _) in sorted(gmap.items()):
                ct = session.event_commence(eid)
                if ct is None or OA._utc(ct) <= now.to_pydatetime():
                    continue
                pe = {"eventId": eid, **books_param(), "markets": ",".join(EVENT_KEYS), "oddsFormat": "american",
                      "dateFormat": "iso"}
                try:
                    r = session.get("game_event_odds", pe, max_age_seconds=600,
                                    purpose="game lines: team totals + alternates")
                except OA.OddsBudgetExceeded:
                    rec["extras_stopped"] = "BUDGET"
                    break
                except OA.OddsProviderFailure as e:
                    rec.setdefault("extras_failed", []).append({"event": eid, "error": str(e)[:200]})
                    continue
                frames.append(parse([r["body"]], gmap, tidx, r["received_at"], cid))
                n_ev += 1
        q = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else \
            pd.DataFrame(columns=COLS)
        q.to_parquet(out / f"{cid}.parquet", index=False)
        rec.update(status="COMPLETE", events_listed=int(len(ev)), events_matched=int(len(gmap)),
                   unmatched=mm[mm["canonical_game_id"].isna()]["match_reason"].value_counts().to_dict(),
                   event_calls=n_ev, rows=int(len(q)), games_with_quotes=int(q["game_id"].nunique()) if len(q) else 0,
                   by_market=q["market_type"].value_counts().to_dict() if len(q) else {}, session=session.summary())
    except (OA.OddsGuardViolation, OA.OddsBudgetExceeded, OA.OddsProviderFailure) as e:
        rec.update(status="FAILED", error=f"{type(e).__name__}: {str(e)[:300]}", session=session.summary())
    (out / f"{cid}.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


# ============================================================================ quote state
def load(out_root: Path, day: str) -> pd.DataFrame:
    fs = sorted((Path(out_root) / day).glob("GQ*.parquet"))
    if not fs:
        return pd.DataFrame(columns=COLS)
    q = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    q["collector_received_at"] = pd.to_datetime(q["collector_received_at"], utc=True)
    return q


def as_of(q: pd.DataFrame, t) -> pd.DataFrame:
    """Latest row per (game, book, market_type, side, point) received at or before `t`."""
    if not len(q):
        return q
    t = pd.Timestamp(t)
    x = q[q["collector_received_at"] <= t].copy()
    x["_pt"] = x["point"].fillna(-9999.0)
    x = x.sort_values("collector_received_at").drop_duplicates(["game_id", "book", "market_type", "side", "_pt"],
                                                               keep="last")
    return x.drop(columns="_pt").reset_index(drop=True)


def closing(q: pd.DataFrame, tips: dict) -> pd.DataFrame:
    """The closing quote per key: the last row received strictly before the tip and within CLOSE_WINDOW_MIN of it."""
    if not len(q):
        return q
    tip = q["game_id"].map(lambda g: pd.Timestamp(tips[g]) if g in tips else pd.NaT)
    x = q[(q["collector_received_at"] < tip) & (tip - q["collector_received_at"] <= pd.Timedelta(minutes=CLOSE_WINDOW_MIN))]
    x = x.assign(_pt=x["point"].fillna(-9999.0)).sort_values("collector_received_at")
    return x.drop_duplicates(["game_id", "book", "market_type", "side", "_pt"], keep="last").drop(columns="_pt")
