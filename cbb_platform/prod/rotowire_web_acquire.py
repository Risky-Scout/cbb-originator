"""PRODUCTION-ONLY RotoWire CBB availability from the AUTHENTICATED SUBSCRIBER WEB PAGES (ROTOWIRE_SOURCE=WEB).

ACQUISITION LAYER ONLY. This is not the RotoWire B2B API and never pretends to be: every event it writes carries
acquisition_source=ROTOWIRE_WEB and the web source URL. It parses the two subscriber pages into the extract() field
layout of prod/rotowire_feed and hands them to the ONE existing reconciliation path (rotowire_feed.ingest_parsed ->
player_state -> availability_frame -> inputs.apply_availability). Nothing here touches JM1, P(play), minutes, PMFs,
pricing, the walk-forward or any frozen artifact.

Sources (nothing else is requested; three GETs per refresh, only at the fixed lifecycle refresh times):
  snapshot  https://www.rotowire.com/cbasketball/injury-report.php           (page: login state)
            https://www.rotowire.com/cbasketball/tables/injury-report.php?team=ALL&pos=ALL&conf=ALL
                 the page's own structured table data -- the exact rows its CSV / Excel export is built from
  news      https://www.rotowire.com/cbasketball/news.php?view=injuries     (append-only injury-news items)

Authentication: a normal subscriber session, nothing more. The subscriber logs in with an ordinary browser; the session
cookie is placed in a protected file on the Droplet (env ROTOWIRE_WEB_COOKIE_FILE; mode 0600/0640, never in the repo,
never logged). Plain requests.Session with an honest User-Agent. No password automation, no CAPTCHA / WAF / anti-bot
handling of any kind: a challenge, a login wall or a logged-out page fails the acquisition closed.

Timestamps (directive §6): the news page shows a DATE only ("November 7, 2026"). Then provider_displayed_date is that
date, provider_timestamp is NULL and the event is knowable at collected_at_utc (rotowire_feed._event with no
source_datetime). A time of day is never invented. Only an explicit ISO-8601-with-offset (or epoch) attribute in the
HTML counts as a provider timestamp.

Identity (directive §9-10): provider player ID = the numeric suffix of /cbasketball/player/<slug>-<ID>. Production maps
it ONLY through the persisted tables <maps>/rotowire_web_player_map.csv and rotowire_web_team_map.csv. New rows are
added to those tables by extend_maps() with exactly one deterministic rule (exact normalized team name; exact normalized
full name on that team's current roster, unique; or the curated exact alias in prod/rotowire_web_team_aliases.csv;
a position disagreement is an audited discrepancy, never a silent accept) -- every addition is persisted with its method
and time, never overwritten, and anything else is left unmapped -> AVAILABILITY_IDENTITY_UNRESOLVED (fail closed).

News continuity: the injury-news page exposes only the latest 25 items (no pagination / archive / cursor). Each window
is persisted (news_windows.jsonl); a window that shares no item with the previous one is ROTOWIRE_WEB_NEWS_COVERAGE_GAP:
no news event, no PROCESSED entry, and rotowire_feed.freshness fails every game closed until a later window overlaps.

    .venv/bin/python -m cbb_platform.prod.rotowire_web_acquire accept      ONE live acquisition: evidence + report only
    .venv/bin/python -m cbb_platform.prod.rotowire_web_acquire build-maps  persist exact deterministic map additions
    .venv/bin/python -m cbb_platform.prod.rotowire_web_acquire acquire     acquire + ingest (what the refresh runs)
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import requests

from cbb_platform.prod import config as PC, matching as MT, ops_store as OS, rotowire_feed as RW

PROVIDER = "rotowire-web"
ACQUISITION_SOURCE = "ROTOWIRE_WEB"
PARSER_VERSION = "rotowire-web-parser-1.0.0"
SITE = "https://www.rotowire.com"
SNAPSHOT_PAGE_URL = SITE + "/cbasketball/injury-report.php"
SNAPSHOT_TABLE_URL = SITE + "/cbasketball/tables/injury-report.php?team=ALL&pos=ALL&conf=ALL"
NEWS_URL = SITE + "/cbasketball/news.php?view=injuries"
ALLOWED_URLS = (SNAPSHOT_PAGE_URL, SNAPSHOT_TABLE_URL, NEWS_URL)
USER_AGENT = "cbb-originator-availability/1.0 (authenticated subscriber session; python-requests)"
TIMEOUT = 30
TEAM_MAP_FILE = "rotowire_web_team_map.csv"
PLAYER_MAP_FILE = "rotowire_web_player_map.csv"
ACCEPTANCE_MARKER = "ROTOWIRE_WEB_ACCEPTANCE.json"
PAYWALL_TEASER_MAX_ROWS = 10        # the logged-out report shows "a handful" of rows (5 observed 2026-11-08)
MATERIAL_ID_MISSING_SHARE = 0.02    # more rows than this without a provider ID: the page schema is not trusted
SCHEMA_MISSING_SHARE = 0.10         # more news items than this missing a required element: SCHEMA_CHANGED
COLLAPSE_MIN_PRIOR = 20             # a row-count collapse is judged only against a prior median at least this large
COLLAPSE_RATIO = 0.25

# ---- fail-closed reason codes (directive §13)
R_SESSION_MISSING = "ROTOWIRE_WEB_SESSION_MISSING"
R_SESSION_INSECURE = "ROTOWIRE_WEB_SESSION_FILE_INSECURE"
R_SESSION_EXPIRED = "ROTOWIRE_WEB_SESSION_EXPIRED"
R_LOGIN_REDIRECT = "ROTOWIRE_WEB_LOGIN_REDIRECT"
R_SUBSCRIPTION_WALL = "ROTOWIRE_WEB_SUBSCRIPTION_WALL"
R_WAF = "ROTOWIRE_WEB_CHALLENGE_PAGE"
R_HTTP = "ROTOWIRE_WEB_HTTP_ERROR"
R_NETWORK = "ROTOWIRE_WEB_NETWORK_ERROR"
R_EMPTY = "ROTOWIRE_WEB_EMPTY_RESPONSE"
R_TABLE_MISSING = "ROTOWIRE_WEB_INJURY_TABLE_MISSING"
R_NEWS_MISSING = "ROTOWIRE_WEB_NEWS_CONTAINER_MISSING"
R_SCHEMA = "ROTOWIRE_WEB_SCHEMA_CHANGED"
R_ID_MISSING = "ROTOWIRE_WEB_PROVIDER_ID_MISSING"
R_COLLAPSE = "ROTOWIRE_WEB_ROW_COUNT_COLLAPSE"
R_AUTH_MARKER = "ROTOWIRE_WEB_AUTH_MARKER_MISSING"
R_NEWS_GAP = RW.NEWS_COVERAGE_GAP   # this news window does not overlap the previous one: items may have been missed
NEWS_WINDOW_FILE = "news_windows.jsonl"      # every observed news window: item keys + RotoWire player IDs (append-only)


class WebAcquisitionFailure(RuntimeError):
    def __init__(self, reason: str, detail: str = "", meta: dict | None = None):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail, self.meta = reason, detail, meta or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


def _clean(s) -> str | None:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(s)))).strip()
    return s or None


# ============================================================================ identity primitives
PLAYER_URL_RE = re.compile(r"/cbasketball/player/([a-z0-9][a-z0-9\-]*?)-(\d+)/?(?:[?#].*)?$", re.I)


def player_id_from_url(url) -> str | None:
    """/cbasketball/player/<slug>-<ROTOWIRE_PLAYER_ID> -> the numeric ID (absolute or relative URL). Else None."""
    if not url:
        return None
    m = PLAYER_URL_RE.search(str(url).strip().replace("\\/", "/"))
    return m.group(2) if m else None


def position_of(raw) -> str | None:
    s = (_clean(raw) or "").upper()
    return s if re.fullmatch(r"[GFC](?:[-/][GFC])*", s) else (s or None)


# ============================================================================ status normalization (directive §7)
# Web labels -> the EXISTING normalized vocabulary of rotowire_feed (OUT_LIKE / UNRESOLVED). Never a P(play).
WEB_STATUS = {
    "OUT": "OUT", "RULED OUT": "OUT", "REMAINS OUT": "OUT",
    "OUT FOR SEASON": "LONG_TERM_OUT", "OUT FOR THE SEASON": "LONG_TERM_OUT", "SEASON": "LONG_TERM_OUT",
    "OUT INDEFINITELY": "LONG_TERM_OUT", "SHUT DOWN FOR SEASON": "LONG_TERM_OUT", "REDSHIRT": "LONG_TERM_OUT",
    "SUSPENSION": "SUSPENDED", "SUSPENDED": "SUSPENDED",
    "INACTIVE": "UNAVAILABLE", "INELIGIBLE": "UNAVAILABLE", "NOT WITH TEAM": "UNAVAILABLE",
    "GAME TIME DECISION": "GTD", "GAME-TIME DECISION": "GTD", "GTD": "GTD",
    "QUESTIONABLE": "QUESTIONABLE", "DAY-TO-DAY": "QUESTIONABLE", "DAY TO DAY": "QUESTIONABLE",
    "PROBABLE": "QUESTIONABLE", "STATUS UNKNOWN": "QUESTIONABLE", "UNKNOWN": "QUESTIONABLE",
    "DOUBTFUL": "DOUBTFUL",
    "COULD RETURN": "RETURN_DESIGNATION", "MAY RETURN": "RETURN_DESIGNATION",
}


def normalize_web_status(raw) -> str:
    """Snapshot 'Status' label -> existing normalized status. Exact (case / space insensitive) labels only, then the
    existing provider-code map; anything else is UNKNOWN (AVAILABILITY_STATUS_UNRECOGNIZED: suppressed, fail closed)."""
    s = _clean(raw)
    if s is None:
        return "UNKNOWN"
    k = re.sub(r"\s+", " ", s.upper())
    return WEB_STATUS.get(k) or RW.normalize_status(k)


# ============================================================================ news text classification (directive §8)
# Deterministic, explicit availability language only. Forward-looking OUT / UNRESOLVED / explicit ACTIVE language wins
# over postgame language ("didn't play Saturday" alone is POSTGAME_ONLY: never pregame evidence). Language from more
# than one group is ambiguous -> UNKNOWN (fail closed). No availability language -> NO_STATUS (no event).
_SCRUB = re.compile(r"\bout of (?:a |the |his |her )?(?:walking )?(?:boot|protocol|concussion protocol|transfer portal)\b"
                    r"|\b(?:worked|working|works|came|comes|checked) out\b")
RULES = (  # (group, normalized, rule id, pattern) -- order inside a group = precedence
    ("OUT", "LONG_TERM_OUT", "OUT_FOR_SEASON", r"\bout for (?:the )?(?:rest of the |remainder of the )?(?:season|year|campaign)\b"
                                               r"|\bout for (?:the )?(?:\d{4}-\d{2}|20\d\d) (?:season|campaign)\b"
                                               r"|\bseason[- ]ending\b|\bshut down for (?:the )?(?:season|year|\d{4}-\d{2})\b"
                                               r"|\bmiss (?:the )?(?:rest|remainder) of the (?:season|year|campaign)\b"),
    ("OUT", "LONG_TERM_OUT", "OUT_INDEFINITELY", r"\bout indefinitely\b|\bsidelined indefinitely\b"),
    ("OUT", "LONG_TERM_OUT", "MULTI_WEEK_ABSENCE", r"\bwill miss (?:at least |the next )?(?:several|multiple|a few|a couple of|"
                                                   r"\d+|two|three|four|five|six|seven|eight|nine|ten) (?:weeks|months)\b"),
    ("OUT", "LONG_TERM_OUT", "REDSHIRT", r"\b(?:will|to|plans to|expected to) redshirt\b|\bis redshirting\b"),
    ("OUT", "SUSPENDED", "SUSPENSION", r"\b(?:is|has been|was|remains|will be) suspended\b|\bserving (?:a |his |her )?suspension\b"
                                       r"|\bsuspended (?:for|indefinitely)\b"),
    ("OUT", "UNAVAILABLE", "INELIGIBLE", r"\b(?:is|remains|was ruled|ruled) ineligible\b|\bnot (?:yet )?eligible\b"),
    ("OUT", "UNAVAILABLE", "INACTIVE", r"\b(?:is|will be|was listed as|listed as) inactive\b"),
    ("OUT", "OUT", "RULED_OUT", r"\bruled out\b"),
    ("OUT", "OUT", "IS_OUT", r"\b(?:is|are|will be|remains|remain|still|stays|has been) out\b|^(?:remains )?out\b"),
    ("OUT", "OUT", "WONT_PLAY", r"\b(?:won't|will not|won't be able to|will not be able to) (?:play|suit up|dress|travel|appear|go)\b"
                                r"|\b(?:won't|will not) be available\b|\b(?:is|will be|remains) unavailable\b"),
    ("OUT", "OUT", "WILL_MISS", r"\bwill miss\b|\bset to miss\b|\bslated to miss\b"),
    ("OUT", "OUT", "HELD_OUT", r"\b(?:is being|will be) held out\b|\bwill sit(?: out)?\b|\b(?:is|remains|will be) sidelined\b"),
    ("UNC", "DOUBTFUL", "DOUBTFUL", r"\bdoubtful\b|\b(?:not expected|unlikely) to (?:play|suit up|be available)\b"),
    ("UNC", "GTD", "GAME_TIME_DECISION", r"\bgame[- ]time decision\b|\bgtd\b"),
    ("UNC", "QUESTIONABLE", "QUESTIONABLE", r"\bquestionable\b|\bday[- ]to[- ]day\b|\bprobable\b"
                                            r"|\bstatus (?:is |remains )?(?:unknown|unclear|uncertain|up in the air)\b"
                                            r"|\b(?:may|might|could) (?:play|suit up)\b|\b(?:expected|likely) to play\b"
                                            r"|\bfighting for eligibility\b|\beligibility (?:is |remains )?(?:pending|in question|"
                                            r"uncertain|unresolved|up in the air)\b|\bawaiting (?:an? )?(?:ncaa )?(?:waiver|eligibility)\b"),
    ("UNC", "RETURN_DESIGNATION", "COULD_RETURN", r"\b(?:could|may|might) (?:return|be available|make (?:his|her) (?:return|debut))\b"
                                                  r"|\b(?:hopes|aims|targeting|expected|hoping) to return\b|\bnearing (?:a )?return\b"),
    ("ACT", "ACTIVE", "EXPLICIT_AVAILABLE", r"\bwill play\b|\b(?:is|will be) available\b|\bcleared to (?:play|return)\b"
                                           r"|\b(?:is|will be) in the starting lineup\b|\bwill start\b|\b(?:is|will be) active\b"),
    ("POST", "POSTGAME_ONLY", "DID_NOT_PLAY", r"\b(?:didn't|did not|doesn't) (?:play|suit up|dress|appear|travel|see the court|take the court)\b"
                                              r"|\bsat out\b|\bmissed (?:his |her |the |\w+'s )?(?:game|contest|matchup|tilt)\b"
                                              r"|\bwas (?:held out|sidelined|inactive|unavailable)\b|\bdnp\b"),
    ("POST", "POSTGAME_ONLY", "IN_GAME_EXIT", r"\b(?:left|exited) (?:the |\w+'s )?(?:game|contest|matchup)\b|\b(?:won't|didn't|did not) return\b"),
    ("POST", "POSTGAME_ONLY", "GAME_PERFORMANCE", r"\b(?:scored|finished with|posted|recorded|tallied|totaled|went for)\b"),
)
_COMPILED = [(g, n, rid, re.compile(p)) for g, n, rid, p in RULES]
UNC_PRECEDENCE = ("DOUBTFUL", "GTD", "QUESTIONABLE", "RETURN_DESIGNATION")
OUT_PRECEDENCE = ("LONG_TERM_OUT", "SUSPENDED", "UNAVAILABLE", "OUT")


def classify_news(headline, summary) -> dict:
    """{normalized_status, rule, matched_text}. normalized_status in the existing vocabulary, or 'NO_STATUS'."""
    text = " || ".join(t for t in (_clean(headline), _clean(summary)) if t).lower().replace("’", "'")
    text = _SCRUB.sub(" ", text)
    segs = [s.strip() for s in text.split("||")]
    hits = {}
    for g, n, rid, rx in _COMPILED:
        for seg in segs:
            m = rx.search(seg)
            if m:
                hits.setdefault(g, []).append((n, rid, m.group(0)))
                break
    fwd = [g for g in ("OUT", "UNC", "ACT") if g in hits]
    if len(fwd) > 1:
        return {"normalized_status": "UNKNOWN", "rule": "AMBIGUOUS:" + "+".join(fwd),
                "matched_text": "; ".join(h[2] for g in fwd for h in hits[g][:1])}
    if fwd:
        g = fwd[0]
        order = OUT_PRECEDENCE if g == "OUT" else UNC_PRECEDENCE if g == "UNC" else ("ACTIVE",)
        n, rid, txt = sorted(hits[g], key=lambda h: order.index(h[0]))[0]
        return {"normalized_status": n, "rule": rid, "matched_text": txt}
    if "POST" in hits:
        n, rid, txt = hits["POST"][0]
        return {"normalized_status": n, "rule": rid, "matched_text": txt}
    return {"normalized_status": "NO_STATUS", "rule": "NO_AVAILABILITY_LANGUAGE", "matched_text": None}


# ============================================================================ HTML tree (stdlib only)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs), [], parent

    @property
    def classes(self) -> set:
        return set((self.attrs.get("class") or "").split())

    def iter(self):
        for c in self.children:
            if isinstance(c, _Node):
                yield c
                yield from c.iter()

    def find_all(self, cls: str) -> list:
        return [n for n in self.iter() if cls in n.classes]

    def find(self, cls: str):
        return next((n for n in self.iter() if cls in n.classes), None)

    def text(self) -> str:
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text())
        return "".join(out)

    def own_text(self) -> str:
        return "".join(c for c in self.children if isinstance(c, str))


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = _Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(_Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse_html(html: str) -> _Node:
    t = _Tree()
    t.feed(html)
    t.close()
    return t.root


# ============================================================================ page state checks (directive §13)
LOGGED_IN_RE = re.compile(r"window\.rwUserLoggedIn\s*=\s*(true|false)\s*;")
CHALLENGE_RE = re.compile(r"<title>\s*(?:just a moment|attention required|access denied)|cf-chl-|challenge-form|cf_chl_opt", re.I)


def page_state(resp_meta: dict, html: str) -> dict:
    """Login / wall / challenge state of one HTML page. Raises WebAcquisitionFailure (fail closed)."""
    final = resp_meta.get("final_url") or ""
    if "/subscribe/login" in final or "/login" in final.split("?")[0]:
        raise WebAcquisitionFailure(R_LOGIN_REDIRECT, "redirected to the login page", resp_meta)
    if CHALLENGE_RE.search(html[:20000]):
        raise WebAcquisitionFailure(R_WAF, "challenge / access-denied page: not bypassed", resp_meta)
    m = LOGGED_IN_RE.search(html)
    if m is None:
        raise WebAcquisitionFailure(R_AUTH_MARKER, "login-state marker absent (page schema changed)", resp_meta)
    if m.group(1) != "true":
        raise WebAcquisitionFailure(R_SESSION_EXPIRED, "page served logged out (session expired or invalid)", resp_meta)
    return {"logged_in": True}


# ============================================================================ snapshot parser (directive §4)
SNAPSHOT_REQUIRED = ("team", "status")


def parse_snapshot_table(content: bytes, collected_at: str, source_sha: str) -> list[dict]:
    """The injury-report table data (JSON list of row objects; the CSV / Excel export is generated from these rows)."""
    if not content or not content.strip():
        raise WebAcquisitionFailure(R_EMPTY, "snapshot table body is empty")
    try:
        body = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise WebAcquisitionFailure(R_SCHEMA, "snapshot table is not JSON") from None
    if isinstance(body, dict):
        lists = [v for v in body.values() if isinstance(v, list)]
        if len(lists) != 1:
            raise WebAcquisitionFailure(R_TABLE_MISSING, "no single row list in the snapshot table")
        body = lists[0]
    if not isinstance(body, list):
        raise WebAcquisitionFailure(R_TABLE_MISSING, "snapshot table is not a row list")
    if not body:
        raise WebAcquisitionFailure(R_EMPTY, "snapshot table has no rows (never read as 'everyone healthy')")
    if not all(isinstance(r, dict) for r in body) or not all(any(k in r for k in ("ID", "playerURL")) for r in body) \
            or not all(all(k in r for k in SNAPSHOT_REQUIRED) for r in body):
        raise WebAcquisitionFailure(R_SCHEMA, "snapshot rows lack ID/playerURL/team/status")
    rows = []
    for r in body:
        url = _clean(r.get("playerURL"))
        uid, fid = player_id_from_url(url), _clean(r.get("ID"))
        pid = uid or (fid if fid and fid.isdigit() else None)
        if uid and fid and uid != fid:
            pid = None                                                    # inconsistent identity: not trusted
        ret = _clean(r.get("rDate"))
        locked = bool(ret and re.search(r"subscribers? only", ret, re.I))
        rows.append({
            "provider_player_id": pid, "player_name": _clean(r.get("player")) or " ".join(
                x for x in (_clean(r.get("firstname")), _clean(r.get("lastname"))) if x) or None,
            "first_name": _clean(r.get("firstname")), "last_name": _clean(r.get("lastname")),
            "team_name": _clean(r.get("team")), "position": position_of(r.get("position")),
            "injury": _clean(r.get("injury")), "status": _clean(r.get("status")),
            "estimated_return": None if locked else ret, "estimated_return_locked": locked,
            "player_url": (SITE + url) if url and url.startswith("/") else url,
            "collected_at_utc": collected_at, "source_url": SNAPSHOT_TABLE_URL, "source_content_sha256": source_sha,
            "record_sha256": hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()})
    return rows


def snapshot_checks(rows: list[dict], prior_counts: list[int]) -> list[str]:
    reasons = []
    missing = sum(1 for r in rows if not r["provider_player_id"])
    if missing > MATERIAL_ID_MISSING_SHARE * len(rows):
        reasons.append(R_ID_MISSING)
    if len(rows) <= PAYWALL_TEASER_MAX_ROWS and rows and all(r["estimated_return_locked"] for r in rows):
        reasons.append(R_SUBSCRIPTION_WALL)
    if _collapsed(len(rows), prior_counts):
        reasons.append(R_COLLAPSE)
    return reasons


def _collapsed(n: int, prior: list[int]) -> bool:
    prior = [p for p in prior if p][-5:]
    if not prior:
        return False
    med = sorted(prior)[len(prior) // 2]
    return med >= COLLAPSE_MIN_PRIOR and n < COLLAPSE_RATIO * med


# ============================================================================ news parser (directive §5-6)
DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%m/%d/%Y")


def parse_displayed_date(raw) -> str | None:
    s = _clean(raw)
    if not s:
        return None
    m = re.match(r"([A-Za-z]+\.? \d{1,2}, \d{4}|\d{1,2}/\d{1,2}/\d{4})", s)
    if not m:
        return None
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(m.group(1), f).date().isoformat()
        except ValueError:
            continue
    return None


def provider_timestamp(node) -> tuple[str | None, str]:
    """An EXACT provider timestamp only: an ISO-8601 attribute with an explicit offset, or an epoch attribute, on the
    timestamp element or a <time> inside it. Display text alone never yields one (-> (None, 'DATE_ONLY'))."""
    cands = [node] + [n for n in node.iter() if n.tag == "time"] if node is not None else []
    for n in cands:
        for a in ("datetime", "data-datetime", "data-timestamp", "data-time", "data-date"):
            v = n.attrs.get(a)
            if not v:
                continue
            v = v.strip()
            if re.fullmatch(r"\d{10}(?:\d{3})?", v):
                return datetime.fromtimestamp(int(v[:10]), tz=timezone.utc).isoformat(), "OK"
            ts, how = RW.parse_ts(v)
            if ts:
                return ts, "OK"
    return None, "DATE_ONLY"


def item_key(pid, displayed_raw, headline, summary) -> str:
    return hashlib.sha256(json.dumps([pid, displayed_raw, headline, summary]).encode()).hexdigest()


def parse_news_html(html: str, collected_at: str, source_sha: str) -> tuple[list[dict], dict]:
    """Every injury-news item in page order (newest first as served). Returns (items, schema stats)."""
    if not html or not html.strip():
        raise WebAcquisitionFailure(R_EMPTY, "news page body is empty")
    root = parse_html(html)
    blocks = [n for n in root.iter() if "news-update" in n.classes and n.tag == "div"]
    if not blocks:
        raise WebAcquisitionFailure(R_NEWS_MISSING, "no news-update items on the injury-news page")
    items, missing = [], 0
    for b in blocks:
        link, ts = b.find("news-update__player-link"), b.find("news-update__timestamp")
        head, news = b.find("news-update__headline"), b.find("news-update__news")
        meta, inj, posn = b.find("news-update__meta"), b.find("news-update__inj"), b.find("news-update__pos")
        ana = b.find("news-update__analysis")
        if link is None or ts is None or news is None or meta is None:
            missing += 1
            continue
        url = link.attrs.get("href")
        team = None
        if posn is not None and posn.parent is not None:
            team = _clean(posn.parent.own_text())
        if team is None:
            logo = b.find("news-update__logo")
            team = _clean(logo.attrs.get("alt")) if logo is not None else None
        disp_raw = _clean(ts.text())
        exact, ts_status = provider_timestamp(ts)
        headline, summary = _clean(head.text()) if head is not None else None, _clean(news.text())
        analysis, locked = None, False
        if ana is not None:
            a = _clean(ana.text())
            a = re.sub(r"^ANALYSIS\s*", "", a or "", flags=re.I) or None
            locked = bool(a and re.search(r"subscribe now", a, re.I)) and any(
                n.tag == "a" and "/subscribe" in (n.attrs.get("href") or "") for n in ana.iter())
            analysis = None if locked else a
        pid = player_id_from_url(url)
        cls = classify_news(headline, summary)
        name = _clean(link.text())
        items.append({
            "provider_player_id": pid, "player_name": name, "team_name": team,
            "position": position_of(posn.text()) if posn is not None else None,
            "injury": _clean(inj.text()) if inj is not None else None, "headline": headline,
            "provider_displayed_text": disp_raw, "provider_displayed_date": parse_displayed_date(disp_raw),
            "provider_timestamp": exact, "provider_timestamp_status": ts_status,
            "effective_known_at": max(collected_at, exact) if exact else collected_at,   # never earlier than collection
            "news_summary": summary, "subscriber_analysis": analysis, "subscriber_analysis_locked": locked,
            "player_url": (SITE + url) if url and url.startswith("/") else url, "source_url": NEWS_URL,
            "collected_at_utc": collected_at, "source_content_sha256": source_sha,
            "item_key": item_key(pid, disp_raw, headline, summary), **{f"classification_{k}": v for k, v in cls.items()}})
    stats = {"blocks": len(blocks), "items": len(items), "items_missing_elements": missing,
             "items_without_provider_id": sum(1 for i in items if not i["provider_player_id"]),
             "items_without_displayed_date": sum(1 for i in items if not i["provider_displayed_date"])}
    return items, stats


def news_checks(items: list[dict], stats: dict, prior_counts: list[int]) -> list[str]:
    reasons = []
    if not items or stats["items_missing_elements"] > SCHEMA_MISSING_SHARE * stats["blocks"]:
        reasons.append(R_SCHEMA)
    if items and stats["items_without_provider_id"] > MATERIAL_ID_MISSING_SHARE * len(items):
        reasons.append(R_ID_MISSING)
    if _collapsed(len(items), prior_counts):
        reasons.append(R_COLLAPSE)
    return reasons


# ============================================================================ rows -> rotowire_feed.extract() layout
def _split_name(name):
    parts = (name or "").split(" ", 1)
    return (parts[0] or None, parts[1] if len(parts) > 1 else None)


def snapshot_fields(r: dict) -> dict:
    first, last = (r["first_name"], r["last_name"]) if r["first_name"] or r["last_name"] else _split_name(r["player_name"])
    return {"rotowire_player_id": r["provider_player_id"], "sportdataio_player_id": None, "first_name": first,
            "last_name": last, "position": r["position"], "rotowire_team_id": None, "team_name": r["team_name"],
            "team_code": None, "raw_status": r["status"], "injury_type": r["injury"], "injury_location": None,
            "injury_detail": None, "injury_side": None, "return_date": r["estimated_return"], "priority": None,
            "source_datetime": None, "source_datetime_status": "SNAPSHOT_COLLECTED_AT", "source_last_edited": None,
            "source_last_edited_status": "MISSING", "normalized_status": normalize_web_status(r["status"]),
            "record_sha256": r["record_sha256"]}


def news_fields(i: dict) -> dict:
    first, last = _split_name(i["player_name"])
    return {"rotowire_update_id": "WEB-" + i["item_key"][:24],          # content key, NOT a provider update ID
            "rotowire_player_id": i["provider_player_id"], "sportdataio_player_id": None, "first_name": first,
            "last_name": last, "position": i["position"], "rotowire_team_id": None, "team_name": i["team_name"],
            "team_code": None, "raw_status": i["classification_matched_text"], "injury_type": i["injury"],
            "injury_location": None, "injury_detail": None, "injury_side": None, "return_date": None, "priority": None,
            "source_datetime": i["provider_timestamp"], "source_datetime_status": i["provider_timestamp_status"],
            "source_last_edited": None, "source_last_edited_status": "MISSING",
            "normalized_status": i["classification_normalized_status"], "record_sha256": i["item_key"],
            "provider_displayed_date": i["provider_displayed_date"], "classification_rule": i["classification_rule"]}


# ============================================================================ strict identity (maps only)
def _read_map(path: Path, cols: list[str]) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str) if Path(path).exists() else pd.DataFrame(columns=cols)


TEAM_MAP_COLS = ["rotowire_team_name", "canonical_team_id", "method", "added_at", "parser_version"]
PLAYER_MAP_COLS = ["rotowire_player_id", "canonical_player_id", "canonical_team_id", "rotowire_player_name",
                   "rotowire_team_name", "method", "added_at", "parser_version"]


class WebIdentity:
    """Provider ID -> canonical player ONLY through the persisted map; team name -> canonical team ONLY through the
    persisted translation map. The mapped player must be on the mapped team's current roster. No name matching here."""

    def __init__(self, ctx: RW.IdentityContext, map_root: Path = PC.ROTOWIRE_MAP_ROOT):
        self.ctx, self.map_root = ctx, Path(map_root)
        self.reload()

    def reload(self):
        tm = _read_map(self.map_root / TEAM_MAP_FILE, TEAM_MAP_COLS)
        pm = _read_map(self.map_root / PLAYER_MAP_FILE, PLAYER_MAP_COLS)
        self.team = {}
        for n, t in zip(tm["rotowire_team_name"], tm["canonical_team_id"]):
            self.team.setdefault(n, set()).add(t)
        self.player = {}
        for p, c in zip(pm["rotowire_player_id"].astype(str), pm["canonical_player_id"]):
            self.player.setdefault(p, set()).add(c)
        r = self.ctx._roster
        self.on_roster = set(zip(r["canonical_player_id"], r["canonical_team_id"]))

    def resolve_team(self, name) -> tuple[str | None, str]:
        ids = self.team.get(name or "", set())
        if len(ids) == 1:
            return next(iter(ids)), "EXPLICIT_WEB_TEAM_MAP"
        return None, "AMBIGUOUS" if len(ids) > 1 else "TEAM_UNMAPPED"

    def resolve(self, f: dict) -> dict:
        tid, thow = self.resolve_team(f.get("team_name"))
        out = {"canonical_team_id": tid, "team_resolution": thow, "canonical_player_id": None}
        if tid is None:
            return {**out, "identity_resolution_status": "AMBIGUOUS" if thow == "AMBIGUOUS" else "UNMATCHED",
                    "identity_method": thow}
        ids = self.player.get(str(f.get("rotowire_player_id")), set()) if f.get("rotowire_player_id") else set()
        if len(ids) > 1:
            return {**out, "identity_resolution_status": "AMBIGUOUS", "identity_method": "EXPLICIT_MAP_CONFLICT"}
        if not ids:
            return {**out, "identity_resolution_status": "UNMATCHED", "identity_method": "PROVIDER_ID_UNMAPPED"}
        pid = next(iter(ids))
        if (pid, tid) not in self.on_roster:
            return {**out, "identity_resolution_status": "UNMATCHED", "identity_method": "EXPLICIT_MAP_NOT_ON_ROSTER"}
        return {**out, "canonical_player_id": pid, "identity_resolution_status": "RESOLVED",
                "identity_method": "EXPLICIT_WEB_PLAYER_MAP"}


ALIAS_COLS = ["rotowire_team_name", "canonical_team_id", "canonical_display_name", "verified_on", "evidence"]
REVIEW_FILE = "rotowire_web_map_review.jsonl"
DISCREPANCY_FILE = "rotowire_web_identity_discrepancies.jsonl"
M_TEAM_EXACT = "EXACT_NORMALIZED_TEAM_NAME"
M_TEAM_ALIAS = "CURATED_EXACT_ALIAS"
M_PLAYER_EXACT = "EXACT_NAME_ON_MAPPED_TEAM_ROSTER"
M_PLAYER_POS = "EXACT_NAME_ON_MAPPED_TEAM_ROSTER_POSITION_DISCREPANCY"
M_PLAYER_ALIAS = "CURATED_PLAYER_ALIAS"
M_PLAYER_OMISSION = "EXACT_NAME_ON_VERIFIED_CBBD_ROSTER_OMISSION"   # rank-1/2 evidence row (prod/roster_omissions)
PLAYER_ALIAS_COLS = ["rotowire_player_id", "rotowire_player_name", "rotowire_team_name", "canonical_player_id",
                     "canonical_player_name", "canonical_team_id", "alias_type", "verified_on", "evidence"]
PLAYER_ALIAS_TYPES = ("SHORT_FORM_FIRST_NAME", "PUNCTUATION_OR_SUFFIX", "DOCUMENTED_NICKNAME", "DOCUMENTED_FULL_NAME_FORM")


def team_aliases(path: Path | None = None) -> pd.DataFrame:
    """The curated, committed RotoWire-web team aliases (prod/rotowire_web_team_aliases.csv): one row per observed
    RotoWire team spelling that is not an exact normalized canonical name. Each row names exactly one canonical team,
    with the date and evidence of its verification. Matched by EXACT string only -- never normalized, never fuzzy."""
    m = pd.read_csv(path or PC.ROTOWIRE_WEB_TEAM_ALIASES, dtype=str, keep_default_na=False)
    if list(m.columns) != ALIAS_COLS:
        raise ValueError(f"rotowire web team aliases: columns must be {ALIAS_COLS}")
    if (m[ALIAS_COLS] == "").any().any():
        raise ValueError("rotowire web team aliases: every field (incl. verified_on, evidence) is required")
    dup = m[m["rotowire_team_name"].duplicated(keep=False)]
    if len(dup):
        raise ValueError(f"rotowire web team aliases: duplicate names {sorted(dup['rotowire_team_name'])}")
    return m


def player_aliases(path: Path | None = None) -> pd.DataFrame:
    """The curated, committed RotoWire-web player aliases (prod/rotowire_web_player_aliases.csv), keyed by the
    provider-native RotoWire player ID. Each row was verified by a human with written evidence (alias_type says which
    permitted variant). Applied only when the observed RotoWire name AND team equal the row exactly, the canonical
    player is on that team's current roster, and no other RotoWire ID holds him. Never inferred at run time."""
    m = pd.read_csv(path or PC.ROTOWIRE_WEB_PLAYER_ALIASES, dtype=str, keep_default_na=False)
    if list(m.columns) != PLAYER_ALIAS_COLS:
        raise ValueError(f"rotowire web player aliases: columns must be {PLAYER_ALIAS_COLS}")
    if (m[PLAYER_ALIAS_COLS] == "").any().any():
        raise ValueError("rotowire web player aliases: every field (incl. verified_on, evidence) is required")
    if not m["alias_type"].isin(PLAYER_ALIAS_TYPES).all():
        raise ValueError(f"rotowire web player aliases: alias_type must be one of {PLAYER_ALIAS_TYPES}")
    for c in ("rotowire_player_id", "canonical_player_id"):
        dup = m[m[c].duplicated(keep=False)]
        if len(dup):
            raise ValueError(f"rotowire web player aliases: duplicate {c} {sorted(dup[c])}")
    return m


def _review_player(ctx: RW.IdentityContext, f: dict, r: dict) -> dict:
    """Exact reason a player was not mapped, with the roster rows a human would look at (never used to map)."""
    name = " ".join(x for x in (f.get("first_name"), f.get("last_name")) if x)
    out = {"kind": "PLAYER", "rotowire_player_id": str(f.get("rotowire_player_id")), "rotowire_player_name": name,
           "rotowire_team_name": f.get("team_name"), "rotowire_position": f.get("position"),
           "canonical_team_id": r.get("canonical_team_id"), "reason": r["identity_method"], "candidates": []}
    tid, k = r.get("canonical_team_id"), MT.norm(name)
    if r["identity_method"] == "EXACT_NAME_NONE" and tid:
        team = ctx._by_team.get(tid)
        other = ctx._roster[(ctx._roster["_n"] == k) & (ctx._roster["canonical_team_id"] != tid)] if k else ()
        toks = k.split()
        near = team[team["_n"].map(lambda n: bool(toks) and (n.split()[-1:] == toks[-1:] or n.split()[:1] == toks[:1]))] \
            if team is not None and toks else pd.DataFrame()
        if len(other):
            out["reason"] = "EXACT_NAME_ONLY_ON_OTHER_TEAM_ROSTER"
            out["candidates"] = [{"canonical_player_id": x.canonical_player_id, "canonical_team_id": x.canonical_team_id,
                                  "roster_name": x.player_name} for x in other.itertuples()]
        elif len(near):
            out["reason"] = "NO_EXACT_NAME_ON_TEAM_ROSTER_NEAR_NAME_NOT_MAPPED"
            out["candidates"] = [{"canonical_player_id": x.canonical_player_id, "roster_name": x.player_name,
                                  "roster_position": x.position} for x in near.itertuples()]
        else:
            out["reason"] = "NOT_ON_CURRENT_TEAM_ROSTER"
        out["team_roster_size"] = 0 if team is None else int(len(team))
    return out


def extend_maps(ctx: RW.IdentityContext, fields: list[dict], map_root: Path = PC.ROTOWIRE_MAP_ROOT, now=None,
                write: bool = True, aliases: pd.DataFrame | None = None,
                player_alias: pd.DataFrame | None = None) -> dict:
    """Add persisted map rows; never overwrite or remove a row. Teams: exact normalized canonical name, else the curated
    exact alias (team_aliases). Players: exact normalized full name on the mapped team's current roster, unique; a
    position disagreement does NOT reject a unique exact team + name match but is recorded as an audited discrepancy.
    A canonical player already held by another RotoWire ID is never mapped twice. Everything else -> review rows with
    the exact reason. Returns {'teams_added', 'players_added', 'discrepancies', 'review'}; write=False only computes."""
    now = _iso(now or _now())
    root = Path(map_root)
    aliases = team_aliases() if aliases is None else aliases
    alias = dict(zip(aliases["rotowire_team_name"], aliases["canonical_team_id"]))
    palias = {r["rotowire_player_id"]: r for r in (player_aliases() if player_alias is None else player_alias
                                                    ).to_dict("records")}
    tm, pm = _read_map(root / TEAM_MAP_FILE, TEAM_MAP_COLS), _read_map(root / PLAYER_MAP_FILE, PLAYER_MAP_COLS)
    have_t, have_p = set(tm["rotowire_team_name"]), set(pm["rotowire_player_id"].astype(str))
    roster_teams = set(ctx._roster["canonical_team_id"])
    src = ctx._roster["roster_source"] if "roster_source" in ctx._roster else pd.Series("CBBD", index=ctx._roster.index)
    om = ctx._roster[src != "CBBD"]
    omitted = set(zip(om["canonical_player_id"], om["canonical_team_id"]))
    new_t, review = [], []
    names = {f["team_name"] for f in fields if f.get("team_name")}
    for name in sorted(names & have_t):                    # a persisted row is never overwritten: disagreement -> review
        held = set(tm.loc[tm["rotowire_team_name"] == name, "canonical_team_id"])
        if name in alias and held != {alias[name]}:
            review.append({"kind": "TEAM", "rotowire_team_name": name, "reason": "CURATED_ALIAS_CONFLICTS_WITH_PERSISTED_MAP",
                           "persisted": sorted(held), "alias": alias[name]})
    for name in sorted(names - have_t):
        tid, how = MT.resolve_team(name, ctx._tidx)
        if tid and name in alias and alias[name] != tid:
            review.append({"kind": "TEAM", "rotowire_team_name": name, "reason": "CURATED_ALIAS_CONFLICTS_WITH_EXACT_NAME",
                           "exact": tid, "alias": alias[name]})
            continue
        method = M_TEAM_EXACT if tid else None
        if not tid and name in alias:
            tid, method = alias[name], M_TEAM_ALIAS
            if tid not in roster_teams:
                review.append({"kind": "TEAM", "rotowire_team_name": name, "reason": "CURATED_ALIAS_TEAM_NOT_ON_CURRENT_ROSTER",
                               "alias": tid})
                continue
        if tid:
            new_t.append({"rotowire_team_name": name, "canonical_team_id": tid, "method": method,
                          "added_at": now, "parser_version": PARSER_VERSION})
        else:
            review.append({"kind": "TEAM", "rotowire_team_name": name, "reason": how})
    team_rows = pd.concat([tm, pd.DataFrame(new_t, columns=TEAM_MAP_COLS)], ignore_index=True)
    team_rows = team_rows[team_rows.groupby("rotowire_team_name")["canonical_team_id"].transform("nunique") == 1]
    tmap = team_rows.drop_duplicates("rotowire_team_name").rename(columns={"rotowire_team_name": "rotowire_team_id"})
    exact = RW.IdentityContext(ctx.teams, ctx.roster, team_map=tmap[["rotowire_team_id", "canonical_team_id"]])
    cand, seen = [], set()
    for f in fields:
        pid = str(f.get("rotowire_player_id") or "")
        if not pid or pid in have_p or pid in seen:
            continue
        seen.add(pid)
        r = exact.resolve({**f, "rotowire_team_id": f.get("team_name"), "team_name": None, "sportdataio_player_id": None})
        name = " ".join(x for x in (f.get("first_name"), f.get("last_name")) if x)
        row = {"rotowire_player_id": pid, "canonical_team_id": r["canonical_team_id"], "rotowire_player_name": name,
               "rotowire_team_name": f.get("team_name"), "added_at": now, "parser_version": PARSER_VERSION}
        pa = palias.get(pid)
        if pa is not None and r["team_resolution"] == "EXPLICIT_MAP" and r["identity_resolution_status"] != "RESOLVED":
            ok = (pa["rotowire_player_name"] == name and pa["rotowire_team_name"] == f.get("team_name")
                  and pa["canonical_team_id"] == r["canonical_team_id"]
                  and (pa["canonical_player_id"], pa["canonical_team_id"]) in set(
                      zip(ctx._roster["canonical_player_id"], ctx._roster["canonical_team_id"])))
            if ok:
                cand.append(({**row, "canonical_player_id": pa["canonical_player_id"], "method": M_PLAYER_ALIAS}, None, f))
            else:
                review.append(_review_player(ctx, f, {**r, "identity_method": "CURATED_PLAYER_ALIAS_MISMATCH"}))
            continue
        if pa is not None and r["identity_resolution_status"] == "RESOLVED" and r["canonical_player_id"] != pa["canonical_player_id"]:
            review.append(_review_player(ctx, f, {**r, "identity_method": "CURATED_PLAYER_ALIAS_CONFLICTS_WITH_EXACT_NAME"}))
            continue
        if r["team_resolution"] != "EXPLICIT_MAP":
            review.append(_review_player(ctx, f, {**r, "identity_method": "TEAM_UNMATCHED"}))
        elif r["identity_resolution_status"] == "RESOLVED":
            how = M_PLAYER_OMISSION if (r["canonical_player_id"], r["canonical_team_id"]) in omitted else M_PLAYER_EXACT
            cand.append(({**row, "canonical_player_id": r["canonical_player_id"], "method": how}, None, f))
        elif r["identity_method"] == "EXACT_NAME_POSITION_CONFLICT":
            team = exact._by_team[r["canonical_team_id"]]
            hit = team[team["_n"] == MT.norm(name)]
            if len(hit) != 1:                              # resolve() already guarantees one; never assume it
                review.append(_review_player(ctx, f, {**r, "identity_method": "EXACT_NAME_MULTIPLE"}))
                continue
            h = hit.iloc[0]
            disc = {"kind": "POSITION_DISCREPANCY", "rotowire_player_id": pid, "rotowire_player_name": name,
                    "rotowire_team_name": f.get("team_name"), "canonical_player_id": h["canonical_player_id"],
                    "canonical_team_id": r["canonical_team_id"], "roster_player_name": h["player_name"],
                    "rotowire_position": f.get("position"), "roster_position": h["position"],
                    "basis": "exact canonical team + exact full name + the only such roster row", "at": now}
            cand.append(({**row, "canonical_player_id": h["canonical_player_id"], "method": M_PLAYER_POS}, disc, f))
        else:
            review.append(_review_player(ctx, f, r))
    held_c = pm["canonical_player_id"].astype(str).value_counts().to_dict()
    for c in [x[0]["canonical_player_id"] for x in cand]:
        held_c[c] = held_c.get(c, 0) + 1
    new_p, disc = [], []
    for rowd, d, f in cand:
        if held_c[rowd["canonical_player_id"]] > 1:        # two RotoWire IDs -> one canonical player: neither is mapped
            review.append({**_review_player(ctx, f, {"canonical_team_id": rowd["canonical_team_id"],
                                                     "identity_method": "CANONICAL_PLAYER_ALREADY_MAPPED"}),
                           "candidates": [{"canonical_player_id": rowd["canonical_player_id"]}]})
            continue
        new_p.append(rowd)
        if d:
            disc.append(d)
    if write and (new_t or new_p):
        root.mkdir(parents=True, exist_ok=True)
        _append_csv(root / TEAM_MAP_FILE, new_t, TEAM_MAP_COLS)
        _append_csv(root / PLAYER_MAP_FILE, new_p, PLAYER_MAP_COLS)
    if write and disc:
        OS.append_jsonl(root / DISCREPANCY_FILE, disc)
    if write and review:
        root.mkdir(parents=True, exist_ok=True)
        OS.append_jsonl(root / REVIEW_FILE, [{**x, "at": now} for x in review])
    return {"teams_added": len(new_t), "players_added": len(new_p), "discrepancies": disc, "review": review,
            "team_rows": new_t, "player_rows": new_p}


def _append_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    if not rows:
        return
    new = not Path(path).exists()
    pd.DataFrame(rows, columns=cols).to_csv(path, mode="a", header=new, index=False)


# ============================================================================ session (cookie file; never logged)
def _load_cookie_file(path: Path) -> list[tuple]:
    """Netscape cookies.txt or a single 'Cookie:' header line -> [(name, value, domain, path, secure, expires)].
    Only rotowire.com cookies are kept."""
    out = []
    txt = Path(path).read_text().strip()
    lines = [l for l in txt.splitlines() if l.strip()]
    if lines and all("\t" not in l for l in lines) and len(lines) == 1:
        hdr = re.sub(r"^cookie:\s*", "", lines[0], flags=re.I)
        for part in hdr.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out.append((k.strip(), v.strip(), ".rotowire.com", "/", True, None))
        return out
    for l in lines:
        if l.startswith("#") and not l.startswith("#HttpOnly_"):
            continue
        p = l.split("\t")
        if len(p) != 7:
            continue
        dom = p[0].replace("#HttpOnly_", "")
        if not dom.lstrip(".").endswith("rotowire.com"):
            continue
        out.append((p[5], p[6], dom, p[2], p[3].upper() == "TRUE", int(p[4]) if p[4].isdigit() and p[4] != "0" else None))
    return out


def _dump_cookie_file(path: Path, jar) -> None:
    lines = ["# Netscape HTTP Cookie File (cbb-originator rotowire web session; protected; never commit)"]
    for c in jar:
        if not (c.domain or "").lstrip(".").endswith("rotowire.com"):
            continue
        dom = c.domain if c.domain.startswith(".") else c.domain
        lines.append("\t".join([dom, "TRUE" if dom.startswith(".") else "FALSE", c.path or "/",
                                "TRUE" if c.secure else "FALSE", str(int(c.expires or 0)), c.name, c.value or ""]))
    tmp = Path(path).with_name(Path(path).name + f".tmp-{uuid.uuid4().hex[:6]}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


class WebClient:
    """GETs of the three allowlisted URLs with the subscriber session. `get` is injectable (tests): url -> response
    with status_code, content, url (final), headers. Exceptions are reduced to their type name (no URL / cookie text)."""

    def __init__(self, cookie_file: str | Path | None = None, get=None, clock=None):
        self.clock = clock or _now
        self.cookie_file = Path(cookie_file) if cookie_file else (
            Path(os.environ[PC.ROTOWIRE_WEB_COOKIE_ENV]) if os.environ.get(PC.ROTOWIRE_WEB_COOKIE_ENV) else None)
        self._get = get
        self.session = None
        self._loaded = None

    def check(self) -> None:
        """Session material present and protected (raises WebAcquisitionFailure). Injected transports skip this."""
        if self._get is not None:
            return
        if self.cookie_file is None or not self.cookie_file.is_file():
            raise WebAcquisitionFailure(R_SESSION_MISSING, f"{PC.ROTOWIRE_WEB_COOKIE_ENV} is not set or not a file")
        mode = self.cookie_file.stat().st_mode
        if mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IWGRP):
            raise WebAcquisitionFailure(R_SESSION_INSECURE, "session file must not be world-readable or group-writable")
        cookies = _load_cookie_file(self.cookie_file)
        if not cookies:
            raise WebAcquisitionFailure(R_SESSION_MISSING, "session file holds no rotowire.com cookie")
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        for name, value, dom, path, secure, exp in cookies:
            s.cookies.set(name, value, domain=dom, path=path, secure=secure, expires=exp)
        self.session = s
        self._loaded = sorted((c.name, c.value) for c in s.cookies)

    def fetch(self, url: str, referer: str | None = None) -> dict:
        if url not in ALLOWED_URLS:
            raise WebAcquisitionFailure(R_HTTP, "URL is not on the RotoWire web allowlist")
        requested = self.clock()
        try:
            if self._get is not None:
                resp = self._get(url)
            else:
                resp = self.session.get(url, timeout=TIMEOUT, allow_redirects=True,
                                        headers={"Referer": referer} if referer else None)
        except requests.RequestException as exc:
            raise WebAcquisitionFailure(R_NETWORK, type(exc).__name__) from None
        collected = self.clock()
        meta = {"url": url, "final_url": str(getattr(resp, "url", url) or url), "http_status": resp.status_code,
                "requested_at": _iso(requested), "collected_at": _iso(collected),
                "redirects": len(getattr(resp, "history", []) or []),
                "content_type": (getattr(resp, "headers", {}) or {}).get("Content-Type")}
        return {**meta, "content": resp.content or b""}

    def persist_rotation(self) -> bool:
        """After a VERIFIED logged-in acquisition: write back rotated session cookies (0600, atomic). Never logs them."""
        if self.session is None or self.cookie_file is None:
            return False
        now = sorted((c.name, c.value) for c in self.session.cookies)
        if now == self._loaded or not os.access(self.cookie_file.parent, os.W_OK):
            return False
        _dump_cookie_file(self.cookie_file, self.session.cookies)
        self._loaded = now
        return True


# ============================================================================ immutable raw evidence (directive §12)
class Evidence:
    def __init__(self, root: Path = PC.ROTOWIRE_WEB_ROOT):
        self.root = Path(root)

    def store(self, acq_id: str, page: str, fetch: dict, ext: str) -> dict:
        content = fetch["content"]
        sha = hashlib.sha256(content).hexdigest()
        day = fetch["collected_at"][:10]
        d = self.root / "raw" / day
        d.mkdir(parents=True, exist_ok=True)
        payload = d / f"{sha}.{ext}.gz"
        created = False
        try:
            fd = os.open(payload, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        except FileExistsError:
            pass                                          # identical content already held: dedupe, never overwrite
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(gzip.compress(content, mtime=0))
            created = True
        meta = {"acquisition_id": acq_id, "page": page, "url": fetch["url"], "final_url": fetch["final_url"],
                "http_status": fetch["http_status"], "requested_at": fetch["requested_at"],
                "collected_at": fetch["collected_at"], "redirects": fetch["redirects"],
                "content_type": fetch["content_type"], "size_bytes": len(content), "sha256": sha,
                "payload_file": payload.name, "payload_created": created, "parser_version": PARSER_VERSION,
                "feed_version": PC.ROTOWIRE_WEB_FEED_VERSION, "acquisition_source": ACQUISITION_SOURCE}
        OS.write_once(d / f"{acq_id}__{page}.meta.json", meta)
        return meta


def read_payload(meta_path: Path) -> bytes:
    m = json.loads(Path(meta_path).read_text())
    return gzip.decompress((Path(meta_path).parent / m["payload_file"]).read_bytes())


# ============================================================================ ledger adapter (freshness gate)
class _Ledger:
    """rotowire_feed.ingest_parsed logs through `._log`: PROCESSED entries land in the SAME request ledger the existing
    freshness gate reads (rotowire_feed.freshness / rotowire_acquire.last_success), tagged with the web source."""

    def __init__(self, path: Path, stage: str):
        self.path, self.stage, self.entries = Path(path), stage, []

    def _log(self, entry: dict) -> dict:
        entry = {"provider": PROVIDER, "acquisition_source": ACQUISITION_SOURCE, "stage": self.stage,
                 "feed_version": PC.ROTOWIRE_WEB_FEED_VERSION, **entry}
        OS.append_jsonl(self.path, [entry])
        self.entries.append(entry)
        return entry


# ============================================================================ news continuity (25-item window)
def prior_news_window(root: Path) -> dict | None:
    """The latest news window observed by an INGEST acquisition (acceptance windows never set continuity)."""
    w = [r for r in OS.read_jsonl(Path(root) / NEWS_WINDOW_FILE) if r.get("mode") == "INGEST"]
    return w[-1] if w else None


def news_continuity(items: list[dict], prior: dict | None) -> tuple[str, int]:
    """The injury-news page shows only the latest N items, newest first, with no pagination / archive / cursor. If the
    new window shares an item with the previous one, nothing published in between can have been skipped (CONTINUOUS);
    if it shares none, items may have been missed (GAP: fail closed). No previous window: BASELINE."""
    if prior is None:
        return "BASELINE", 0
    n = len({i["item_key"] for i in items} & set(prior.get("item_keys") or []))
    return ("CONTINUOUS" if n else "GAP"), n


# ============================================================================ one acquisition
def _prior_counts(root: Path, key: str) -> list[int]:
    """Row counts of earlier successfully PROCESSED acquisitions (key: news_rows | snapshot_rows)."""
    st = key.replace("_rows", "_status")
    return [int(r[key]) for r in OS.read_jsonl(Path(root) / "acquisitions.jsonl")
            if r.get("mode") != "ACCEPTANCE" and r.get(st) == "PROCESSED" and r.get(key)]


def _map_counts(res: dict) -> dict:
    return {"teams_added": res["teams_added"], "players_added": res["players_added"],
            "position_discrepancies": len(res["discrepancies"]), "review": len(res["review"])}


def acquire(client: WebClient, ctx: RW.IdentityContext | None, *, store: RW.EventStore | None = None,
            root: Path = PC.ROTOWIRE_WEB_ROOT, ledger_path: Path = PC.ROTOWIRE_REQUEST_LEDGER,
            map_root: Path = PC.ROTOWIRE_MAP_ROOT, stage: str = "refresh", ingest: bool = True,
            automap: bool = True) -> dict:
    """News first (point-in-time stream), then the snapshot (current-state reconciliation). Every page fetched is kept
    as immutable evidence. A failed page is recorded with its reason code and contributes NO event and NO PROCESSED
    ledger entry, so the existing freshness gate fails the affected games closed. ingest=False (acceptance): evidence +
    report only -- no event, no map row, no ledger entry. Never raises for a provider problem."""
    root = Path(root)
    store = store if store is not None else RW.EventStore()
    acq_id = "W" + client.clock().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    ev = Evidence(root)
    led = _Ledger(ledger_path, stage)
    rep = {"acquisition_id": acq_id, "mode": "INGEST" if ingest else "ACCEPTANCE", "parser_version": PARSER_VERSION,
           "acquisition_source": ACQUISITION_SOURCE, "started_at": _iso(client.clock()), "reasons": [], "warnings": [],
           "auth": None}
    try:
        client.check()
    except WebAcquisitionFailure as e:
        rep.update(auth="FAIL", reasons=[e.reason], news_status="FAILED", snapshot_status="FAILED")
        for ep in ("news_injuries", "snapshot"):
            if ingest:
                led._log({"endpoint": ep, "status": "FAILED", "reason": e.reason, "requested_at": rep["started_at"]})
        OS.append_jsonl(root / "acquisitions.jsonl", [rep])
        return rep
    ident = WebIdentity(ctx, map_root) if ctx is not None else None
    auth_ok = []

    def page(url, name, ext, html_page, referer=None):
        f = client.fetch(url, referer)
        meta = ev.store(acq_id, name, f, ext)
        if not (200 <= f["http_status"] < 300):
            raise WebAcquisitionFailure(R_HTTP, f"HTTP {f['http_status']}", meta)
        if not f["content"].strip():
            raise WebAcquisitionFailure(R_EMPTY, f"{name} body is empty", meta)
        if html_page:
            page_state(meta, f["content"].decode("utf-8", "replace"))
            auth_ok.append(name)
        elif "/subscribe/login" in meta["final_url"]:
            raise WebAcquisitionFailure(R_LOGIN_REDIRECT, "redirected to the login page", meta)
        return f, meta

    # ---------------------------------------------------------------- news (delta / point-in-time stream)
    try:
        f, meta = page(NEWS_URL, "news", "html", True)
        items, stats = parse_news_html(f["content"].decode("utf-8", "replace"), meta["collected_at"], meta["sha256"])
        bad = news_checks(items, stats, _prior_counts(root, "news_rows"))
        rep.update(news_http=meta["http_status"], news_rows=len(items), news_schema=stats, news_sha256=meta["sha256"])
        if bad:
            raise WebAcquisitionFailure(bad[0], ",".join(bad), meta)
        prev_keys = {r["item_key"] for r in OS.read_jsonl(root / "news_items.jsonl")}
        prior = prior_news_window(root)
        cont, overlap = news_continuity(items, prior)
        rep.update(news_continuity=cont, news_window_overlap=overlap,
                   news_prior_window=prior.get("acquisition_id") if prior else None)
        OS.append_jsonl(root / NEWS_WINDOW_FILE, [{
            "acquisition_id": acq_id, "mode": rep["mode"], "collected_at": meta["collected_at"],
            "news_sha256": meta["sha256"], "window_size": len(items), "continuity": cont, "overlap": overlap,
            "prior_window": rep["news_prior_window"], "item_keys": [i["item_key"] for i in items],
            "rotowire_player_ids": [i["provider_player_id"] for i in items],
            "displayed_dates": [i["provider_displayed_date"] for i in items]}])
        rep["news_new_items"] = sum(1 for i in items if i["item_key"] not in prev_keys)
        rep["news_classification"] = pd.Series([i["classification_normalized_status"] for i in items]).value_counts().to_dict()
        rep["_news_items"] = items
        fields = [news_fields(i) for i in reversed(items)]          # oldest first: later items get the later seq
        status_fields = [x for x in fields if x["normalized_status"] != "NO_STATUS" and x["rotowire_player_id"]]
        rep["_news_fields"] = fields
        if ingest and cont == "GAP":
            # every item is still kept (evidence + continuity for the next poll), but NO news event and NO PROCESSED
            # entry: the COVERAGE_GAP ledger entry fails every game closed until a later window overlaps this one
            new = [{**i, "acquisition_id": acq_id} for i in reversed(items) if i["item_key"] not in prev_keys]
            OS.append_jsonl(root / "news_items.jsonl", new)
            led._log({"endpoint": "news_injuries", "status": RW.NEWS_GAP_STATUS, "reason": R_NEWS_GAP,
                      "collected_at": meta["collected_at"], "raw_payload_sha256": meta["sha256"],
                      "prior_window": rep["news_prior_window"], "records": len(items)})
            raise WebAcquisitionFailure(R_NEWS_GAP, "news window does not overlap the previous window", meta)
        if ingest:
            if automap and ctx is not None:
                rep["news_maps"] = _map_counts(extend_maps(ctx, status_fields, map_root, client.clock()))
                ident.reload()
            new = [{**i, "acquisition_id": acq_id} for i in reversed(items) if i["item_key"] not in prev_keys]
            OS.append_jsonl(root / "news_items.jsonl", new)          # append-only: every item, never collapsed
            out = RW.ingest_parsed(status_fields, len(items), stats["items_missing_elements"],
                                   {"collected_at": meta["collected_at"], "raw_payload_sha256": meta["sha256"]},
                                   "news_injuries", ident, store, led,
                                   extra={"source_endpoint": NEWS_URL, "acquisition_source": ACQUISITION_SOURCE,
                                          "acquisition_id": acq_id, "parser_version": PARSER_VERSION})
            rep["news_events_new"] = out["events_new"]
        rep["news_status"] = "PROCESSED" if ingest else "PARSED"
    except WebAcquisitionFailure as e:
        rep["news_status"] = "COVERAGE_GAP" if e.reason == R_NEWS_GAP else "FAILED"
        rep["reasons"].append(e.reason)
        rep["news_error"] = e.detail[:200]
        if ingest and e.reason != R_NEWS_GAP:
            led._log({"endpoint": "news_injuries", "status": "FAILED", "reason": e.reason, "detail": e.detail[:200],
                      "http_status": e.meta.get("http_status"), "raw_payload_sha256": e.meta.get("sha256")})

    # ---------------------------------------------------------------- snapshot (current-state reconciliation)
    try:
        _, pmeta = page(SNAPSHOT_PAGE_URL, "snapshot_page", "html", True)
        f, meta = page(SNAPSHOT_TABLE_URL, "snapshot_table", "json", False, referer=SNAPSHOT_PAGE_URL)
        rows = parse_snapshot_table(f["content"], meta["collected_at"], meta["sha256"])
        bad = snapshot_checks(rows, _prior_counts(root, "snapshot_rows"))
        rep.update(snapshot_http=pmeta["http_status"], snapshot_table_http=meta["http_status"], snapshot_rows=len(rows),
                   snapshot_sha256=meta["sha256"])
        if bad:
            raise WebAcquisitionFailure(bad[0], ",".join(bad), meta)
        rep["snapshot_status_labels"] = pd.Series([r["status"] for r in rows]).value_counts().to_dict()
        rep["snapshot_unrecognized_labels"] = sorted({r["status"] or "" for r in rows
                                                      if normalize_web_status(r["status"]) == "UNKNOWN"})
        rep["_snapshot_rows"] = rows
        fields = [snapshot_fields(r) for r in rows if r["provider_player_id"]]
        rep["_snapshot_fields"] = fields
        if ingest:
            if automap and ctx is not None:
                rep["snapshot_maps"] = _map_counts(extend_maps(ctx, fields, map_root, client.clock()))
                ident.reload()
            out = RW.ingest_parsed(fields, len(rows), len(rows) - len(fields),
                                   {"collected_at": meta["collected_at"], "raw_payload_sha256": meta["sha256"]},
                                   "snapshot", ident, store, led,
                                   extra={"source_endpoint": SNAPSHOT_TABLE_URL, "acquisition_source": ACQUISITION_SOURCE,
                                          "acquisition_id": acq_id, "parser_version": PARSER_VERSION})
            rep["snapshot_events_new"] = out["events_new"]
        rep["snapshot_status"] = "PROCESSED" if ingest else "PARSED"
    except WebAcquisitionFailure as e:
        rep["snapshot_status"] = "FAILED"
        rep["reasons"].append(e.reason)
        rep["snapshot_error"] = e.detail[:200]
        if ingest:
            led._log({"endpoint": "snapshot", "status": "FAILED", "reason": e.reason, "detail": e.detail[:200],
                      "http_status": e.meta.get("http_status"), "raw_payload_sha256": e.meta.get("sha256")})

    rep["auth"] = "PASS" if set(auth_ok) == {"news", "snapshot_page"} else "FAIL"
    if rep["auth"] == "PASS" and not [x for x in rep["reasons"] if x != R_NEWS_GAP]:
        try:
            rep["session_rotated"] = client.persist_rotation()
        except OSError as e:
            rep["warnings"].append(f"SESSION_WRITEBACK_FAILED:{type(e).__name__}")
    rep["finished_at"] = _iso(client.clock())
    OS.append_jsonl(root / "acquisitions.jsonl", [{k: v for k, v in rep.items() if not k.startswith("_")}])
    return rep


# ============================================================================ acceptance report (directive §17)
NEWS_COVERAGE_FINDING = "UNPROVEN_25_ITEM_WINDOW"   # latest-N page, no pagination / archive / cursor (2026-11-08)


def identity_report(rep: dict, ctx: RW.IdentityContext, map_root: Path = PC.ROTOWIRE_MAP_ROOT,
                    aliases: pd.DataFrame | None = None, player_alias: pd.DataFrame | None = None) -> dict:
    """Team / player resolution of every row this acquisition observed: through the PERSISTED maps (what production
    resolves with) and after the deterministic additions extend_maps would make (write=False)."""
    fields = (rep.get("_snapshot_fields") or []) + [x for x in rep.get("_news_fields") or [] if x["rotowire_player_id"]]
    names = sorted({f["team_name"] for f in fields if f.get("team_name")})
    persisted = WebIdentity(ctx, map_root)
    would = extend_maps(ctx, fields, map_root, write=False, aliases=aliases, player_alias=player_alias)
    bad_t = {x["rotowire_team_name"] for x in would["review"] if x["kind"] == "TEAM"}
    first = {}
    for f in fields:
        first.setdefault(str(f["rotowire_player_id"]), f)
    pres = {p: persisted.resolve(f) for p, f in first.items()}
    unresolved = {x["rotowire_player_id"]: x for x in would["review"] if x["kind"] == "PLAYER"}
    for p, r in pres.items():                               # persisted rows that no longer resolve (e.g. roster moved)
        if p not in unresolved and r["identity_resolution_status"] != "RESOLVED" and \
                r["identity_method"] not in ("PROVIDER_ID_UNMAPPED", "TEAM_UNMAPPED"):
            unresolved[p] = {"kind": "PLAYER", "rotowire_player_id": p, "rotowire_team_name": first[p].get("team_name"),
                             "reason": r["identity_method"], "candidates": []}
    disc = would["discrepancies"] + [d for d in OS.read_jsonl(Path(map_root) / DISCREPANCY_FILE)
                                     if d["rotowire_player_id"] in first]
    n = len(first)
    return {"TEAM_MAPPING_RATE": round((len(names) - len(bad_t)) / len(names), 4) if names else None,
            "TEAM_MAPPING_RATE_PERSISTED_MAP": round(sum(1 for x in names if persisted.resolve_team(x)[0]) / len(names), 4)
            if names else None,
            "PLAYER_MAPPING_RATE": round((n - len(unresolved)) / n, 4) if n else None,
            "PLAYER_MAPPING_RATE_PERSISTED_MAP": round(sum(1 for r in pres.values()
                                                           if r["identity_resolution_status"] == "RESOLVED") / n, 4) if n else None,
            "OBSERVED_TEAMS": len(names), "OBSERVED_PLAYERS": n,
            "UNRESOLVED_TEAMS": sorted(bad_t), "UNRESOLVED_PLAYERS": sorted(unresolved.values(), key=lambda x: (x["reason"], x["rotowire_player_id"])),
            "UNRESOLVED_PLAYER_REASONS": pd.Series([x["reason"] for x in unresolved.values()]).value_counts().to_dict(),
            "POSITION_DISCREPANCIES": disc,
            "PENDING_MAP_ADDITIONS": {"teams": would["teams_added"], "players": would["players_added"]}}


def acceptance_summary(rep: dict, ctx: RW.IdentityContext | None, map_root: Path = PC.ROTOWIRE_MAP_ROOT,
                       aliases: pd.DataFrame | None = None) -> dict:
    snap, news = rep.get("_snapshot_rows") or [], rep.get("_news_items") or []
    rows = snap + news
    out = {"ROTOWIRE_WEB_AUTH": rep.get("auth"), "SNAPSHOT_HTTP": rep.get("snapshot_http"),
           "SNAPSHOT_TABLE_HTTP": rep.get("snapshot_table_http"), "NEWS_HTTP": rep.get("news_http"),
           "SNAPSHOT_ROWS": rep.get("snapshot_rows", 0), "NEWS_ROWS": rep.get("news_rows", 0),
           "PLAYER_ID_EXTRACTION_RATE": round(sum(1 for r in rows if r["provider_player_id"]) / len(rows), 4) if rows else None,
           "REASONS": rep.get("reasons"), "WARNINGS": rep.get("warnings"),
           "NEWS_WINDOW_SIZE": rep.get("news_rows", 0), "NEWS_CONTINUITY": rep.get("news_continuity"),
           "NEWS_WINDOW_OVERLAP": rep.get("news_window_overlap"),
           "NEWS_DISPLAYED_DATES": pd.Series([i["provider_displayed_date"] for i in news]).value_counts().sort_index().to_dict(),
           "NEWS_COVERAGE_COMPLETE": NEWS_COVERAGE_FINDING,
           "NEWS_CLASSIFICATION": rep.get("news_classification"),
           "SNAPSHOT_STATUS_LABELS": rep.get("snapshot_status_labels"),
           "SNAPSHOT_UNRECOGNIZED_LABELS": rep.get("snapshot_unrecognized_labels"),
           "NEWS_TIMESTAMP_STATUS": pd.Series([i["provider_timestamp_status"] for i in news]).value_counts().to_dict(),
           "SUBSCRIBER_ANALYSIS_VISIBLE": sum(1 for i in news if i["subscriber_analysis"])}
    if ctx is not None and rows:
        out.update(identity_report(rep, ctx, map_root, aliases))
    else:
        out["TEAM_MAPPING_RATE"] = "NO_IDENTITY_CONTEXT"
    # one example per observed classification type, for the manual inspection (directive §17): short excerpts only
    ex = {}
    for i in news:
        k = i["classification_rule"]
        if k not in ex:
            ex[k] = {"status": i["classification_normalized_status"], "headline": (i["headline"] or "")[:90],
                     "matched": i["classification_matched_text"], "player_id": i["provider_player_id"],
                     "displayed_date": i["provider_displayed_date"]}
    out["NEWS_EXAMPLES_BY_RULE"] = ex
    ok = (out["ROTOWIRE_WEB_AUTH"] == "PASS" and out["SNAPSHOT_HTTP"] == 200 and out["NEWS_HTTP"] == 200
          and out["SNAPSHOT_ROWS"] > 0 and out["NEWS_ROWS"] > 0 and not rep.get("reasons")
          and out["TEAM_MAPPING_RATE"] == 1.0)                # every observed team resolves (maps or curated alias)
    out["ROTOWIRE_WEB_LIVE_ACCEPTANCE"] = "PASS" if ok else "FAIL"
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.rotowire_web_acquire")
    ap.add_argument("--ops-root", type=Path, default=PC.OPS_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accept")
    sub.add_parser("build-maps")
    sub.add_parser("acquire")
    a = ap.parse_args(argv)
    try:
        from cbb_platform.prod import history_extract as HX
        ctx = RW.load_identity_context(PC.STATE_ROOT, HX.resolve())
    except Exception as e:                                # noqa: BLE001 -- acceptance still reports parsing
        ctx = None
        print(json.dumps({"identity_context": f"unavailable ({type(e).__name__})"}))
    client = WebClient()
    if a.cmd == "accept":
        rep = acquire(client, ctx, ingest=False, stage="acceptance")
        summ = acceptance_summary(rep, ctx)
        OS.write_json(Path(a.ops_root) / "markers" / ACCEPTANCE_MARKER,
                      {"status": summ["ROTOWIRE_WEB_LIVE_ACCEPTANCE"], "acquisition_id": rep["acquisition_id"],
                       "at": rep.get("finished_at"), "parser_version": PARSER_VERSION,
                       **{k: summ.get(k) for k in ("SNAPSHOT_ROWS", "NEWS_ROWS", "PLAYER_ID_EXTRACTION_RATE",
                                                   "TEAM_MAPPING_RATE", "TEAM_MAPPING_RATE_PERSISTED_MAP",
                                                   "PLAYER_MAPPING_RATE", "PLAYER_MAPPING_RATE_PERSISTED_MAP",
                                                   "NEWS_COVERAGE_COMPLETE", "REASONS")},
                       "unresolved_teams": len(summ.get("UNRESOLVED_TEAMS") or []),
                       "unresolved_players": len(summ.get("UNRESOLVED_PLAYERS") or []),
                       "position_discrepancies": len(summ.get("POSITION_DISCREPANCIES") or [])})
        print(json.dumps(summ, indent=1, default=str))
        return
    if ctx is None:
        raise SystemExit("no identity context: refusing to map or ingest")
    if a.cmd == "build-maps":
        rep = acquire(client, ctx, ingest=False, stage="build-maps")
        fields = (rep.get("_snapshot_fields") or []) + [x for x in rep.get("_news_fields") or [] if x["rotowire_player_id"]]
        if rep["reasons"]:
            print(json.dumps({"refused": rep["reasons"]}))
            return
        res = extend_maps(ctx, fields)
        art = {"acquisition_id": rep["acquisition_id"], "at": rep.get("finished_at"), "parser_version": PARSER_VERSION,
               "team_rows_added": res["team_rows"], "player_rows_added": res["player_rows"],
               "position_discrepancies": res["discrepancies"],
               "unresolved": sorted(res["review"], key=lambda x: (x["kind"], x["reason"], str(x.get("rotowire_player_id"))))}
        OS.write_once(PC.ROTOWIRE_MAP_ROOT / "build_maps" / f"{rep['acquisition_id']}.json", art)   # immutable audit record
        print(json.dumps({"acquisition_id": rep["acquisition_id"], "teams_added": res["teams_added"],
                          "players_added": res["players_added"], "position_discrepancies": len(res["discrepancies"]),
                          "unresolved_teams": sum(1 for x in res["review"] if x["kind"] == "TEAM"),
                          "unresolved_players": sum(1 for x in res["review"] if x["kind"] == "PLAYER"),
                          "audit": str(PC.ROTOWIRE_MAP_ROOT / "build_maps" / f"{rep['acquisition_id']}.json")}, indent=1))
        return
    rep = acquire(client, ctx, store=RW.EventStore(), stage="manual-acquire")
    print(json.dumps({k: v for k, v in rep.items() if not k.startswith("_")}, indent=1, default=str))


if __name__ == "__main__":
    main()
