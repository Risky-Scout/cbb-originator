"""Stage 2L -- systematic semantic audit of CBBD PBP `wallclock` (offline; reads the built database).

    .venv/bin/python -m cbb_platform.wallclock_audit

For every game with PBP wallclock and an EXACT scheduled tip (games.csv, BDL-verified naive UTC), the first
wallclock in play order is read as a NAIVE clock reading W (its trailing 'Z' is ignored on purpose) and
compared with the tip T under three hypotheses:

    UTC                              W - T            in [-0.25h, +1.0h]  (first event shortly after tip)
    EASTERN_TIME_MISLABELED_Z        W - ET(T)        in the same window  (ET(T) = tip as US/Eastern wall time)
    LOCAL_VENUE_TIME_MISLABELED_Z    W - LOCAL(T)     in the same window  (venue timezone from venue state)

Eastern venues cannot separate EASTERN from LOCAL, so the decision rests on Central/Mountain/Pacific venues.
Venues in states split across time zones, or with no state/timezone, are excluded from the decision.
A hypothesis is ACCEPTED for a timezone group when >= 95% of that group's games are consistent with it and
the rival hypothesis is <= 5%. Overall class: one hypothesis accepted in every decisive group ->
that class; groups disagreeing -> MIXED; too little evidence -> UNRESOLVED.

Raw wallclock is never converted or overwritten. The result only states whether a conversion rule is
supported; until an architect approves it, wallclock stays prohibited for PIT / live latency.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import duckdb

from cbb_platform import config as C

OUT_JSON = C.QA / "cbbd_wallclock_semantics.json"
OUT_MD = C.PROJECT_ROOT / "docs" / "CBBD_WALLCLOCK_SEMANTICS.md"
WINDOW = (-0.25, 1.0)
ACCEPT, REJECT = 0.95, 0.05
EASTERN = ZoneInfo("America/New_York")

# State -> IANA zone. States split across zones are deliberately absent (excluded from the decision).
STATE_TZ = {
    **dict.fromkeys("CT DE DC GA ME MD MA NH NJ NY NC OH PA RI SC VT VA WV".split(), "America/New_York"),
    "MI": "America/Detroit",
    **dict.fromkeys("AL AR IL IA LA MN MS MO OK WI".split(), "America/Chicago"),
    **dict.fromkeys("CO MT NM UT WY".split(), "America/Denver"),
    "AZ": "America/Phoenix",
    **dict.fromkeys("CA NV WA".split(), "America/Los_Angeles"),
    "HI": "Pacific/Honolulu", "AK": "America/Anchorage", "Puerto Rico": "America/Puerto_Rico",
}
SPLIT_STATES = {"TX", "FL", "TN", "KY", "IN", "KS", "NE", "SD", "ND", "ID", "OR"}
GROUP = {"America/New_York": "EASTERN", "America/Detroit": "EASTERN", "America/Chicago": "CENTRAL",
         "America/Denver": "MOUNTAIN", "America/Phoenix": "MOUNTAIN_NO_DST", "America/Los_Angeles": "PACIFIC",
         "Pacific/Honolulu": "HAWAII", "America/Anchorage": "ALASKA", "America/Puerto_Rico": "ATLANTIC"}


def naive_wall(raw: str) -> datetime:
    """The clock reading of a raw wallclock string, ignoring its zone suffix (it is under test)."""
    return datetime.fromisoformat(raw.replace("Z", "")[:19])


def residuals(raw_first: str, tip_utc: datetime, venue_tz: str | None) -> dict:
    """Hours between the first wallclock reading and the tip expressed under each hypothesis (pure)."""
    w = naive_wall(raw_first)
    t = tip_utc.astimezone(timezone.utc)
    out = {"UTC": (w - t.replace(tzinfo=None)).total_seconds() / 3600,
           "EASTERN_TIME_MISLABELED_Z": (w - t.astimezone(EASTERN).replace(tzinfo=None)).total_seconds() / 3600}
    if venue_tz:
        out["LOCAL_VENUE_TIME_MISLABELED_Z"] = (w - t.astimezone(ZoneInfo(venue_tz)).replace(tzinfo=None)).total_seconds() / 3600
    return out


def consistent(res: dict) -> set:
    return {h for h, r in res.items() if WINDOW[0] <= r <= WINDOW[1]}


def decide(groups: dict) -> tuple[str, dict]:
    """groups: group -> Counter of consistent-hypothesis frozensets. Pure; unit tested."""
    verdicts = {}
    for g, cnt in groups.items():
        n = sum(cnt.values())
        share = {h: sum(v for k, v in cnt.items() if h in k) / n for h in
                 ("UTC", "EASTERN_TIME_MISLABELED_Z", "LOCAL_VENUE_TIME_MISLABELED_Z")} if n else {}
        if n < 30:
            verdicts[g] = {"games": n, "share_consistent": share, "verdict": "INSUFFICIENT"}
            continue
        acc = [h for h, s in share.items() if s >= ACCEPT]
        if g == "EASTERN":   # EASTERN and LOCAL coincide here: only UTC vs Eastern-like is decidable
            v = "UTC" if "UTC" in acc and share["EASTERN_TIME_MISLABELED_Z"] <= REJECT else \
                "EASTERN_OR_LOCAL" if "EASTERN_TIME_MISLABELED_Z" in acc and share["UTC"] <= REJECT else "MIXED"
        else:
            ok = [h for h in acc if all(s <= REJECT for k, s in share.items() if k != h)]
            v = ok[0] if len(ok) == 1 else "MIXED"
        verdicts[g] = {"games": n, "share_consistent": {k: round(x, 4) for k, x in share.items()}, "verdict": v}
    decisive = {g: v["verdict"] for g, v in verdicts.items() if g != "EASTERN" and v["verdict"] != "INSUFFICIENT"}
    if not decisive:
        return "UNRESOLVED", verdicts
    vals = set(decisive.values())
    if len(vals) == 1 and "MIXED" not in vals:
        cls = vals.pop()
        e = verdicts.get("EASTERN", {}).get("verdict")
        if cls == "UTC" and e not in (None, "UTC", "INSUFFICIENT"):
            return "MIXED", verdicts
        if cls != "UTC" and e not in (None, "EASTERN_OR_LOCAL", "INSUFFICIENT"):
            return "MIXED", verdicts
        return cls, verdicts
    return "MIXED", verdicts


def run(db_path=None) -> dict:
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    rows = con.execute("""
        WITH w AS (SELECT canonical_game_id, arg_min(raw_wallclock, play_sequence) AS first_raw,
                          arg_max(raw_wallclock, play_sequence) AS last_raw, count(*) AS n
                   FROM fact.plays WHERE raw_wallclock IS NOT NULL GROUP BY 1)
        SELECT w.canonical_game_id, g.season, g.scheduled_tip_utc, g.neutral_site, v.state, v.country,
               w.first_raw, w.last_raw, w.n
        FROM w JOIN core.games g USING (canonical_game_id) LEFT JOIN core.venues v USING (canonical_venue_id)
        WHERE g.tip_time_quality = 'EXACT'""").fetchall()
    con.close()
    groups = defaultdict(Counter)
    by_season = defaultdict(Counter)
    neutral = Counter()
    excluded = Counter()
    samples = defaultdict(list)
    spans = []
    for gid, season, tip, ns, state, country, first, last, n in rows:
        tz = STATE_TZ.get(state) if country in ("United States", None) or state == "Puerto Rico" else None
        if state in SPLIT_STATES:
            excluded["SPLIT_TIMEZONE_STATE"] += 1
            tz = None
        elif tz is None:
            excluded["NO_VENUE_TIMEZONE"] += 1
        res = residuals(first, tip, tz)
        k = frozenset(consistent(res))
        spans.append((naive_wall(last) - naive_wall(first)).total_seconds() / 60)
        if tz is None:
            continue
        g = GROUP[tz]
        groups[g][k] += 1
        by_season[(season, g)][k] += 1
        if ns:
            neutral[k] += 1
        if len(samples[g]) < 5:
            samples[g].append({"game": gid, "season": season, "tip_utc": str(tip), "venue_state": state,
                               "first_wallclock_raw": first, "residual_hours": {h: round(r, 2) for h, r in res.items()}})
    cls, verdicts = decide(groups)
    season_cls = {}
    for (s, g), cnt in sorted(by_season.items()):
        season_cls.setdefault(str(s), {})[g] = decide({g: cnt})[1][g]["verdict"] if g != "EASTERN" else \
            decide({g: cnt})[1][g]["verdict"]
    fmt = lambda c: {"+".join(sorted(k)) or "NONE": v for k, v in c.most_common()}
    spans.sort()
    result = {
        "classification": cls,
        "approved_for_pit_or_latency": False,
        "rule": "Raw wallclock is preserved unchanged and remains PROHIBITED for PIT cutoffs and live latency until "
                "the architect approves a conversion rule backed by this classification.",
        "games_with_wallclock_and_exact_tip": len(rows), "excluded_from_decision": dict(excluded),
        "window_hours": WINDOW, "accept_share": ACCEPT, "reject_share": REJECT,
        "groups": {g: {**verdicts[g], "consistency_patterns": fmt(groups[g])} for g in sorted(groups)},
        "neutral_site_patterns": fmt(neutral), "by_season": season_cls,
        "first_to_last_wallclock_minutes": {"p10": round(spans[len(spans) // 10], 1), "median": round(spans[len(spans) // 2], 1),
                                            "p90": round(spans[9 * len(spans) // 10], 1)} if spans else None,
        "examples": dict(samples)}
    OUT_JSON.write_text(json.dumps(result, indent=1, default=str))
    OUT_MD.write_text(render(result))
    return result


def render(r: dict) -> str:
    L = ["# CBBD PBP wallclock semantics (Stage 2L)", "",
         "Rendered from `data/qa/cbbd_wallclock_semantics.json` by `cbb_platform/wallclock_audit.py`. Do not edit by hand.", "",
         f"## Classification: **{r['classification']}**", "",
         f"**Approved for PIT / live latency: {r['approved_for_pit_or_latency']}.** {r['rule']}", "",
         f"Games with wallclock and an exact tip: {r['games_with_wallclock_and_exact_tip']}. Excluded from the decision: "
         f"{r['excluded_from_decision']} (venues in split-timezone states or without a timezone).", "",
         "Method: the first wallclock of each game is read as a naive clock reading and compared with the BDL-verified "
         f"scheduled tip under each hypothesis; a game is consistent with a hypothesis when the residual lies in "
         f"{r['window_hours']} hours. A hypothesis is accepted for a timezone group at >= {r['accept_share']:.0%} consistency "
         f"with every rival <= {r['reject_share']:.0%}. Eastern venues cannot separate Eastern-labelled from venue-local time.", "",
         "| venue timezone group | games | UTC | EASTERN_TIME_MISLABELED_Z | LOCAL_VENUE_TIME_MISLABELED_Z | verdict |",
         "|---|---|---|---|---|---|"]
    for g, v in r["groups"].items():
        s = v["share_consistent"]
        L.append(f"| {g} | {v['games']} | {s.get('UTC', 0):.3f} | {s.get('EASTERN_TIME_MISLABELED_Z', 0):.3f} | "
                 f"{s.get('LOCAL_VENUE_TIME_MISLABELED_Z', 0):.3f} | {v['verdict']} |")
    L += ["", "## By season (verdict per timezone group)", "", "| season | " + " | ".join(sorted(r["groups"])) + " |",
          "|---|" + "---|" * len(r["groups"])]
    for s, gv in r["by_season"].items():
        L.append(f"| {s} | " + " | ".join(gv.get(g, "-") for g in sorted(r["groups"])) + " |")
    L += ["", "## Consistency patterns", "", "```json", json.dumps({g: v["consistency_patterns"] for g, v in r["groups"].items()}, indent=1),
          "```", "", f"Neutral-site games: {r['neutral_site_patterns']}", "",
          f"First-to-last wallclock span (minutes): {r['first_to_last_wallclock_minutes']}", "",
          "## Examples", "", "```json", json.dumps(r["examples"], indent=1), "```", ""]
    return "\n".join(L)


if __name__ == "__main__":
    r = run()
    print(r["classification"], json.dumps({g: (v["games"], v["verdict"]) for g, v in r["groups"].items()}))
