"""RotoWire WEB availability adapter (prod/rotowire_web_acquire.py). CI-safe: network-free SYNTHETIC fixtures in the
page structure observed 2026-11-08 (news-update blocks; injury-report table rows); no real session, no model change."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import (config as PC, inputs as IN, lifecycle as L, rotowire_feed as RW,
                               rotowire_web_acquire as W)

T0 = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
TIP = pd.Timestamp("2026-11-11T00:30:00Z")
CUT = TIP - pd.Timedelta(hours=1)
COOKIE_SECRET = "rw-session-SENTINEL-9f8e7d6c5b4a"
GAMES = pd.DataFrame({"canonical_game_id": ["G1"], "home_team_id": ["T1"], "away_team_id": ["T2"],
                      "feature_cutoff": [CUT], "start_time": [TIP]})


# ============================================================================ synthetic pages
def item(pid, name, team, pos, inj, headline, summary, date="November 9, 2026", locked=True, ts_attr=None, slug=None):
    slug = slug or name.lower().replace(" ", "-")
    ana = ('<b>ANALYSIS</b><br><a href="/subscribe/">Subscribe now</a> to instantly reveal our take on this news.'
           if locked else "<b>ANALYSIS</b><br>Synthetic subscriber analysis.")
    ts = f' data-datetime="{ts_attr}"' if ts_attr else ""
    link = f'/cbasketball/player/{slug}-{pid}' if pid else f'/cbasketball/player/{slug}'
    return f'''<div class="news-update is-injured">
  <div class="news-update__top"><img class="news-update__logo" src="x.png" alt="{team}">
    <div class="news-update__playerhead"><a class="news-update__player-link" href="{link}">{name}</a>
      <div class="news-update__headline">{headline}</div></div></div>
  <div class="news-update__meta"><div><b class="news-update__pos">{pos}</b>{team}</div><div class="news-update__inj">{inj}</div></div>
  <div class="news-update__main"><div class="news-update__timestamp"{ts}>{date}</div>
    <div class="news-update__news">{summary}</div><div class="news-update__analysis">{ana}</div></div>
</div>'''


def page(body="", logged_in=True):
    flag = "true" if logged_in else "false"
    return f"<html><head><title>T</title><script>window.rwUserLoggedIn = {flag};</script></head><body>{body}</body></html>"


def news_page(*items, logged_in=True):
    return page('<div class="news-list">' + "".join(items) + "</div>", logged_in)


def row(pid, first, last, team, pos, inj, status, ret="Nov 20", url=True):
    r = {"ID": pid, "firstname": first, "lastname": last, "player": f"{first} {last}", "team": team, "position": pos,
         "injury": inj, "status": status, "rDate": ret}
    if url:
        r["playerURL"] = f"/cbasketball/player/{first.lower()}-{last.lower()}-{pid}"
    return r


DEFAULT_NEWS = news_page(
    item("101", "Jon Doe", "Home U", "G", "Knee", "Ruled out Tuesday", "Doe (knee) is out Tuesday against Away St."),
    item("201", "Kai Ito", "Away St", "C", "Ankle", "Game-time decision", "Ito (ankle) is a game-time decision."))
DEFAULT_ROWS = [row("101", "Jon", "Doe", "Home U", "G", "Knee", "Out"),
                row("201", "Kai", "Ito", "Away St", "C", "Ankle", "Game Time Decision")]


class Resp:
    def __init__(self, content, status=200, url=None, history=()):
        self.content = content.encode() if isinstance(content, str) else content
        self.status_code, self.url, self.history, self.headers = status, url, list(history), {"Content-Type": "text/html"}


class Site:
    """url -> Resp. Pages can be replaced between acquisitions."""

    def __init__(self, news=DEFAULT_NEWS, rows=DEFAULT_ROWS, report=None):
        self.set(news=news, rows=rows, report=report or page())
        self.calls = []

    def set(self, news=None, rows=None, report=None):
        if news is not None:
            self.news = news if isinstance(news, Resp) else Resp(news, url=W.NEWS_URL)
        if rows is not None:
            self.table = rows if isinstance(rows, Resp) else Resp(json.dumps(rows), url=W.SNAPSHOT_TABLE_URL)
        if report is not None:
            self.report = report if isinstance(report, Resp) else Resp(report, url=W.SNAPSHOT_PAGE_URL)

    def __call__(self, url):
        self.calls.append(url)
        return {W.NEWS_URL: self.news, W.SNAPSHOT_TABLE_URL: self.table, W.SNAPSHOT_PAGE_URL: self.report}[url]


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def ctx():
    teams = pd.DataFrame({"canonical_team_id": ["T1", "T2"], "school": ["Home U", "Away St"],
                          "display_name": ["Home U", "Away St"], "mascot": [None, None]})
    roster = pd.DataFrame([("P1", "T1", "Jon", "Doe", "Jon Doe", "Guard"), ("P2", "T1", "Ann", "Lee", "Ann Lee", "Forward"),
                           ("P5", "T2", "Kai", "Ito", "Kai Ito", "Center"), ("P6", "T2", "Max", "Ray", "Max Ray", "Guard")],
                          columns=["canonical_player_id", "canonical_team_id", "first_name", "last_name", "player_name",
                                   "position"])
    return RW.IdentityContext(teams, roster)


@pytest.fixture
def env(tmp_path, ctx):
    d = {"root": tmp_path / "web", "ledger": tmp_path / "rw_requests.jsonl", "maps": tmp_path / "maps",
         "store": RW.EventStore(tmp_path / "events"), "clock": Clock(), "ctx": ctx, "tmp": tmp_path}

    def run(site, ingest=True, automap=True):
        client = W.WebClient(get=site, clock=d["clock"])
        return W.acquire(client, d["ctx"], store=d["store"], root=d["root"], ledger_path=d["ledger"],
                         map_root=d["maps"], ingest=ingest, automap=automap)
    d["run"] = run
    return d


def processed(env, ep):
    return [e for e in W.OS.read_jsonl(env["ledger"]) if e.get("endpoint") == ep and e.get("status") == "PROCESSED"]


def state(env, as_of=None):
    return RW.player_state(env["store"].frame(), as_of or (env["clock"].t + timedelta(minutes=1)))


# ============================================================================ 1-4 field parsing
def test_01_player_id_extraction_from_url():
    assert W.player_id_from_url("/cbasketball/player/guillermo-del-pino-25918") == "25918"
    assert W.player_id_from_url("https://www.rotowire.com/cbasketball/player/joe-grahovac-25672/") == "25672"
    assert W.player_id_from_url("\\/cbasketball\\/player\\/cade-krueger-28336") == "28336"
    assert W.player_id_from_url("/cbasketball/player/o-neal-jr-7?x=1") == "7"
    for bad in (None, "", "/cbasketball/player/no-id", "/basketball/player/nba-guy-123", "/cbasketball/team/x-5"):
        assert W.player_id_from_url(bad) is None


def test_02_03_04_team_position_injury_parsing():
    html = news_page(item("101", "Jon Doe", "St. Bonaventure", "G-F", "Lower Body", "Remains out", "Doe is still out."))
    items, stats = W.parse_news_html(html, T0.isoformat(), "sha")
    i = items[0]
    assert (i["team_name"], i["position"], i["injury"]) == ("St. Bonaventure", "G-F", "Lower Body")
    assert i["player_name"] == "Jon Doe" and i["provider_player_id"] == "101"
    assert stats == {"blocks": 1, "items": 1, "items_missing_elements": 0, "items_without_provider_id": 0,
                     "items_without_displayed_date": 0}
    rows = W.parse_snapshot_table(json.dumps([row("9", "A", "B", "USC", "F", "Groin", "Out")]).encode(), "t", "s")
    assert (rows[0]["team_name"], rows[0]["position"], rows[0]["injury"], rows[0]["provider_player_id"]) == \
        ("USC", "F", "Groin", "9")
    assert W.position_of(" c ") == "C" and W.position_of("F/C") == "F/C"


# ============================================================================ 5 / 21 / 22 status normalization
@pytest.mark.parametrize("label,norm", [
    ("Out", "OUT"), ("Out For Season", "LONG_TERM_OUT"), ("Out Indefinitely", "LONG_TERM_OUT"),
    ("Suspension", "SUSPENDED"), ("Inactive", "UNAVAILABLE"), ("Redshirt", "LONG_TERM_OUT"),
    ("Game Time Decision", "GTD"), ("Questionable", "QUESTIONABLE"), ("Doubtful", "DOUBTFUL"),
    ("Day-To-Day", "QUESTIONABLE"), ("Status Unknown", "QUESTIONABLE"), ("GTD", "GTD"), ("Q", "QUESTIONABLE"),
    ("Something New", "UNKNOWN"), (None, "UNKNOWN"), ("", "UNKNOWN")])
def test_05_snapshot_status_normalization(label, norm):
    assert W.normalize_web_status(label) == norm


@pytest.mark.parametrize("headline,summary,norm", [
    ("Ruled out Friday", "Smith (knee) is out Friday against State.", "OUT"),
    ("Won't play Saturday", "Smith won't play Saturday.", "OUT"),
    ("Will not appear", "Smith will not appear Tuesday.", "OUT"),
    ("Inactive Monday", "Smith is inactive Monday.", "UNAVAILABLE"),
    ("Remains out", "Smith (leg) is still out for the Bonnies.", "OUT"),
    ("Will miss several weeks", "Smith will miss several weeks with a fracture.", "LONG_TERM_OUT"),
    ("Out for season", "Smith will be out for the rest of the season.", "LONG_TERM_OUT"),
    ("Shut down", "Smith has been shut down for the season.", "LONG_TERM_OUT"),
    ("Shut down for 2026-27", "Smith (knee) is out for the 2026-27 season with a torn ACL.", "LONG_TERM_OUT"),
    ("Suspended indefinitely", "Smith is suspended for violating team rules.", "SUSPENDED"),
    ("Ineligible this season", "Smith is ineligible after his waiver was denied.", "UNAVAILABLE"),
    ("Being held out", "Smith is being held out as a precaution.", "OUT")])
def test_21_out_like_text_is_confirmed_unavailable(headline, summary, norm):
    c = W.classify_news(headline, summary)
    assert c["normalized_status"] == norm
    assert RW.interface_status(norm) in IN.UNAVAILABLE


@pytest.mark.parametrize("headline,summary,norm", [
    ("Game-time decision", "Smith is a game-time decision Friday.", "GTD"),
    ("Questionable Saturday", "Smith is questionable for Saturday.", "QUESTIONABLE"),
    ("Doubtful Friday", "Smith is doubtful against State.", "DOUBTFUL"),
    ("Could return Tuesday", "Smith could return Tuesday.", "RETURN_DESIGNATION"),
    ("May play", "Smith may play Saturday.", "QUESTIONABLE"),
    ("Status uncertain", "Smith's status is unknown for Friday.", "QUESTIONABLE"),
    ("Day-to-day", "Smith is considered day-to-day.", "QUESTIONABLE"),
    ("Still fighting for eligibility", "Smith (not injury related) is still fighting for eligibility.", "QUESTIONABLE"),
    ("Awaiting waiver", "Smith is awaiting an NCAA waiver.", "QUESTIONABLE")])
def test_22_uncertain_text_stays_unresolved(headline, summary, norm):
    c = W.classify_news(headline, summary)
    assert c["normalized_status"] == norm and norm in RW.UNRESOLVED


def test_classifier_ambiguity_and_scrubbing_fail_closed():
    assert W.classify_news("Out Friday", "Smith is out Friday but could return Sunday.")["normalized_status"] == "UNKNOWN"
    assert W.classify_news("Update", "Smith is out of his walking boot.")["normalized_status"] == "NO_STATUS"
    assert W.classify_news("Practices", "Smith worked out with the team.")["normalized_status"] == "NO_STATUS"
    assert W.classify_news("Cleared", "Smith has been cleared to play Saturday.")["normalized_status"] == "ACTIVE"
    assert W.classify_news("Won't be available", "Smith won't be available.")["normalized_status"] == "OUT"


# ============================================================================ 6 / 7 / 25 timestamps
def test_06_25_date_only_publication_never_becomes_a_time(env):
    r = env["run"](Site())
    assert r["news_status"] == "PROCESSED", r
    items = W.OS.read_jsonl(env["root"] / "news_items.jsonl")
    assert all(i["provider_displayed_date"] == "2026-11-09" and i["provider_timestamp"] is None
               and i["provider_timestamp_status"] == "DATE_ONLY" and i["effective_known_at"] == i["collected_at_utc"]
               for i in items)
    news = [e for e in env["store"].load() if e["event_kind"] == "NEWS_UPDATE"]
    assert news and all(e["source_datetime"] is None and e["knowable_at"] == e["collected_at_utc"] for e in news)
    blob = (env["root"] / "news_items.jsonl").read_text() + env["store"].path.read_text()
    for fake in ("2026-11-09T00:00:00", "2026-11-09T12:00:00", "2026-11-09T23:59:59", "2026-11-09 00:00"):
        assert fake not in blob


def test_07_exact_provider_timestamp_is_used_when_present(env):
    html = news_page(item("101", "Jon Doe", "Home U", "G", "Knee", "Ruled out", "Doe is out Tuesday.",
                          ts_attr="2026-11-10T14:05:00-05:00"))
    items, _ = W.parse_news_html(html, T0.isoformat(), "s")
    assert items[0]["provider_timestamp"] == "2026-11-10T19:05:00+00:00"
    assert items[0]["provider_timestamp_status"] == "OK"
    naive = news_page(item("101", "Jon Doe", "Home U", "G", "Knee", "Ruled out", "Doe is out.", ts_attr="2026-11-10T14:05:00"))
    assert W.parse_news_html(naive, T0.isoformat(), "s")[0][0]["provider_timestamp"] is None   # no zone: not guessed
    env["run"](Site(news=html))
    e = [x for x in env["store"].load() if x["event_kind"] == "NEWS_UPDATE"][0]
    assert e["source_datetime"] == "2026-11-10T19:05:00+00:00"
    assert pd.Timestamp(e["knowable_at"]) == max(pd.Timestamp(e["collected_at_utc"]), pd.Timestamp(e["source_datetime"]))


# ============================================================================ 8 / 19 / 24 append-only news stream
def test_08_multiple_updates_for_one_player_are_separate_events(env):
    html = news_page(   # page order: newest first
        item("101", "Jon Doe", "Home U", "G", "Knee", "Questionable Tuesday", "Doe is questionable Tuesday.",
             date="November 10, 2026"),
        item("101", "Jon Doe", "Home U", "G", "Knee", "Ruled out", "Doe is out Saturday.", date="November 8, 2026"))
    env["run"](Site(news=html, rows=[row("101", "Jon", "Doe", "Home U", "G", "Knee", "Questionable")]))
    news = [e for e in env["store"].load() if e["event_kind"] == "NEWS_UPDATE"]
    assert [e["normalized_status"] for e in sorted(news, key=lambda e: e["seq"])] == ["OUT", "QUESTIONABLE"]
    s = state(env).set_index("rotowire_player_id").loc["101"]
    assert s["news_status"] == "QUESTIONABLE" and s["effective_status"] == "QUESTIONABLE"


def test_19_append_only_only_genuinely_new_records(env):
    env["run"](Site())
    before = env["store"].path.read_bytes()
    items_before = (env["root"] / "news_items.jsonl").read_bytes()
    n0 = len(env["store"].load())
    env["run"](Site())                                                   # identical pages: nothing new
    assert env["store"].path.read_bytes() == before and len(env["store"].load()) == n0
    newer = news_page(item("101", "Jon Doe", "Home U", "G", "Knee", "Questionable now", "Doe is questionable Friday.",
                           date="November 10, 2026"),
                      item("101", "Jon Doe", "Home U", "G", "Knee", "Ruled out Tuesday", "Doe (knee) is out Tuesday against Away St."),
                      item("201", "Kai Ito", "Away St", "C", "Ankle", "Game-time decision", "Ito (ankle) is a game-time decision."))
    r = env["run"](Site(news=newer))
    assert r["news_new_items"] == 1 and r["news_events_new"] == 1
    assert env["store"].path.read_bytes().startswith(before)            # earlier lines never rewritten
    assert (env["root"] / "news_items.jsonl").read_bytes().startswith(items_before)


def test_24_disappearance_from_news_is_not_active(env):
    env["run"](Site())
    assert state(env).set_index("rotowire_player_id").loc["101", "effective_status"] == "OUT"
    env["run"](Site(news=news_page(item("201", "Kai Ito", "Away St", "C", "Ankle", "Game-time decision",
                                        "Ito (ankle) is a game-time decision."))))      # overlapping window, 101 gone
    s = state(env).set_index("rotowire_player_id")
    assert s.loc["101", "effective_status"] == "OUT" and s.loc["101", "news_status"] == "OUT"   # still the last news
    env["run"](Site(rows=[row("201", "Kai", "Ito", "Away St", "C", "Ankle", "Game Time Decision")]))   # off the list too
    s = state(env).set_index("rotowire_player_id")
    assert s.loc["101", "snapshot_status"] == RW.NOT_LISTED
    assert s.loc["101", "effective_status"] != "ACTIVE" and s.loc["101", "gate"] == RW.GATE_CONFLICT


# ============================================================================ 9 / 10 reconciliation
def test_09_snapshot_and_news_agree(env):
    env["run"](Site())
    s = state(env).set_index("rotowire_player_id")
    assert s.loc["101", ["effective_status", "gate", "news_status", "snapshot_status"]].tolist() == ["OUT", "", "OUT", "OUT"]
    assert s.loc["201", "effective_status"] == "GTD" and s.loc["201", "gate"] == RW.GATE_UNCERTAIN


def test_10_snapshot_news_conflict_preserves_both_and_gates(env):
    env["run"](Site(rows=[row("101", "Jon", "Doe", "Home U", "G", "Knee", "Game Time Decision"),
                          row("201", "Kai", "Ito", "Away St", "C", "Ankle", "Game Time Decision")]))
    s = state(env).set_index("rotowire_player_id").loc["101"]
    assert s["news_status"] == "OUT" and s["snapshot_status"] == "GTD"
    assert s["effective_status"] == "UNKNOWN" and s["gate"] == RW.GATE_CONFLICT
    kinds = {e["event_kind"] for e in env["store"].load() if e["rotowire_player_id"] == "101"}
    assert kinds == {"NEWS_UPDATE", "SNAPSHOT_OBSERVATION"}


# ============================================================================ 11 / 12 identity
def test_11_unknown_player_id_fails_closed(env):
    html = news_page(item("999", "Ghost Player", "Home U", "G", "Knee", "Ruled out", "Player is out Tuesday."))
    env["run"](Site(news=html, rows=[row("999", "Ghost", "Player", "Home U", "G", "Knee", "Out")]))
    s = state(env).set_index("rotowire_player_id").loc["999"]
    assert s["identity_resolution_status"] == "UNMATCHED" and s["canonical_player_id"] is None
    assert [e["identity_method"] for e in env["store"].load()][0] == "PROVIDER_ID_UNMAPPED"
    fr, reasons = RW.availability_frame(env["store"].frame(), GAMES, env["clock"].t + timedelta(minutes=1))
    assert reasons["G1"] == ["AVAILABILITY_IDENTITY_UNRESOLVED"] and fr.empty
    review = W.OS.read_jsonl(env["maps"] / "rotowire_web_map_review.jsonl")
    assert any(x["kind"] == "PLAYER" and x["rotowire_player_id"] == "999" for x in review)


def test_11b_no_name_fallback_without_the_persisted_map(env):
    env["run"](Site(), automap=False)                       # maps never extended -> nothing resolves by name
    assert {e["identity_resolution_status"] for e in env["store"].load()} == {"UNMATCHED"}
    env2 = W.WebIdentity(env["ctx"], env["maps"])
    assert env2.resolve({"team_name": "Home U", "rotowire_player_id": "101"})["identity_method"] == "TEAM_UNMAPPED"


def test_12_unknown_team_fails_closed_and_is_logged(env):
    html = news_page(item("101", "Jon Doe", "Mystery College", "G", "Knee", "Ruled out", "Doe is out Tuesday."))
    env["run"](Site(news=html, rows=[row("101", "Jon", "Doe", "Mystery College", "G", "Knee", "Out")]))
    e = env["store"].load()[0]
    assert e["team_resolution"] == "TEAM_UNMAPPED" and e["canonical_team_id"] is None
    _, reasons = RW.availability_frame(env["store"].frame(), GAMES, env["clock"].t + timedelta(minutes=1))
    assert reasons["G1"] == ["AVAILABILITY_IDENTITY_UNRESOLVED"]
    review = W.OS.read_jsonl(env["maps"] / "rotowire_web_map_review.jsonl")
    assert any(x["kind"] == "TEAM" and x["rotowire_team_name"] == "Mystery College" for x in review)


def test_maps_are_persisted_append_only_and_never_overwritten(env):
    env["run"](Site())
    tm = pd.read_csv(env["maps"] / W.TEAM_MAP_FILE, dtype=str)
    pm = pd.read_csv(env["maps"] / W.PLAYER_MAP_FILE, dtype=str)
    assert set(tm["rotowire_team_name"]) == {"Home U", "Away St"} and set(tm["method"]) == {"EXACT_NORMALIZED_TEAM_NAME"}
    assert dict(zip(pm["rotowire_player_id"], pm["canonical_player_id"])) == {"101": "P1", "201": "P5"}
    before = ((env["maps"] / W.TEAM_MAP_FILE).read_bytes(), (env["maps"] / W.PLAYER_MAP_FILE).read_bytes())
    env["run"](Site())
    assert ((env["maps"] / W.TEAM_MAP_FILE).read_bytes(), (env["maps"] / W.PLAYER_MAP_FILE).read_bytes()) == before
    assert {e["identity_method"] for e in env["store"].load()} == {"EXPLICIT_WEB_PLAYER_MAP"}


# ============================================================================ 13-16 fail closed
def _no_processed(env):
    return not processed(env, "news_injuries") and not processed(env, "snapshot") and not env["store"].load()


def test_13_login_redirect_fails_closed(env):
    login = Resp(page(logged_in=False), url="https://www.rotowire.com/subscribe/login/")
    r = env["run"](Site(news=login, report=login, rows=Resp("<html></html>", url="https://www.rotowire.com/subscribe/login/")))
    assert r["reasons"] == [W.R_LOGIN_REDIRECT, W.R_LOGIN_REDIRECT] and r["auth"] == "FAIL"
    assert _no_processed(env)
    assert {e["reason"] for e in W.OS.read_jsonl(env["ledger"]) if e["status"] == "FAILED"} == {W.R_LOGIN_REDIRECT}


def test_14_subscription_wall_and_logged_out_session_fail_closed(env):
    teaser = [row(str(i), "A", f"B{i}", "Home U", "G", "Knee", "Out", ret="<i>Subscribers Only</i>") for i in range(5)]
    r = env["run"](Site(rows=teaser))
    assert W.R_SUBSCRIPTION_WALL in r["reasons"] and r["snapshot_status"] == "FAILED"
    assert not processed(env, "snapshot")
    r = env["run"](Site(news=news_page(logged_in=False), report=page(logged_in=False)))
    assert r["reasons"] == [W.R_SESSION_EXPIRED, W.R_SESSION_EXPIRED]


def test_15_empty_responses_never_mean_everyone_healthy(env):
    r = env["run"](Site(news=Resp("", url=W.NEWS_URL), rows=[]))
    assert r["reasons"] == [W.R_EMPTY, W.R_EMPTY] and _no_processed(env)
    with pytest.raises(W.WebAcquisitionFailure) as e:
        W.parse_snapshot_table(b"[]", "t", "s")
    assert e.value.reason == W.R_EMPTY


def test_16_dom_or_schema_change_fails_closed(env):
    r = env["run"](Site(news=page('<div class="totally-new-layout">x</div>'),
                        rows=[{"name": "x", "state": "Out"}]))
    assert r["reasons"] == [W.R_NEWS_MISSING, W.R_SCHEMA] and _no_processed(env)
    r = env["run"](Site(news="<html><body>" + item("1", "A B", "Home U", "G", "K", "Out", "A is out.") + "</body></html>"))
    assert r["reasons"][0] == W.R_AUTH_MARKER
    broken = news_page(*['<div class="news-update"><div class="news-update__headline">x</div></div>'] * 5,
                       item("101", "Jon Doe", "Home U", "G", "Knee", "Ruled out", "Doe is out."))
    assert W.R_SCHEMA in env["run"](Site(news=broken))["reasons"]
    no_ids = news_page(*[item(None, f"P {chr(97 + i)}", "Home U", "G", "K", "Out", "P is out.") for i in range(5)])
    assert W.R_ID_MISSING in env["run"](Site(news=no_ids))["reasons"]
    challenge = Resp("<html><head><title>Just a moment...</title></head></html>", status=403, url=W.NEWS_URL)
    assert env["run"](Site(news=challenge))["reasons"][0] == W.R_HTTP


def test_row_count_collapse_fails_closed(env):
    big = [row(str(1000 + i), "N", f"X{i}", "Home U", "G", "Knee", "Out") for i in range(60)]
    for _ in range(3):
        assert env["run"](Site(rows=big))["snapshot_status"] == "PROCESSED"
    r = env["run"](Site(rows=big[:5]))
    assert W.R_COLLAPSE in r["reasons"] and r["snapshot_status"] == "FAILED"


# ============================================================================ 17 stale / 18 dedupe / 23 postgame
def test_17_stale_feed_fails_the_game_closed(env):
    env["run"](Site())
    ent = W.OS.read_jsonl(env["ledger"])
    near = TIP - pd.Timedelta(minutes=50)            # inside 1 h of tip: 20-minute bound
    assert RW.freshness(ent, GAMES, near)["game_reasons"]["G1"] == ["AVAILABILITY_STALE"]
    fresh_at = pd.Timestamp(env["clock"].t) + pd.Timedelta(minutes=5)
    g = GAMES.assign(start_time=[fresh_at + pd.Timedelta(hours=2)])
    assert RW.freshness(ent, g, fresh_at)["game_reasons"]["G1"] == []
    assert RW.freshness([], GAMES, near)["game_reasons"]["G1"] == ["AVAILABILITY_FEED_UNAVAILABLE"]


def test_18_duplicate_content_sha_is_stored_once_metadata_kept(env):
    env["run"](Site())
    env["run"](Site())
    day = env["root"] / "raw" / T0.date().isoformat()
    metas = sorted(day.glob("*.meta.json"))
    payloads = sorted(day.glob("*.gz"))
    assert len(metas) == 6 and len(payloads) == 3                    # 2 acquisitions x 3 pages; 3 distinct contents
    m = [json.loads(p.read_text()) for p in metas]
    assert sum(x["payload_created"] for x in m) == 3
    assert {x["http_status"] for x in m} == {200} and all(x["parser_version"] == W.PARSER_VERSION for x in m)
    assert W.read_payload(metas[0]).startswith(b"<html>") or W.read_payload(metas[0]).startswith(b"[")


def test_23_dnp_postgame_text_is_never_pregame_evidence(env):
    html = news_page(item("101", "Jon Doe", "Home U", "G", "Knee", "Doesn't play Saturday",
                          "Doe (knee) didn't play Saturday against Away St."),
                     item("201", "Kai Ito", "Away St", "C", "Ankle", "Scores 20", "Ito scored 20 points in his return."))
    c = W.classify_news("Doesn't play Saturday", "Doe (knee) didn't play Saturday against Away St.")
    assert c["normalized_status"] == "POSTGAME_ONLY"
    env["run"](Site(news=html, rows=[row("201", "Kai", "Ito", "Away St", "C", "Ankle", "Game Time Decision")]))
    ev = env["store"].frame()
    assert set(ev.loc[ev["event_kind"] == "NEWS_UPDATE", "normalized_status"]) == {"POSTGAME_ONLY"}
    u = RW.usable(ev, env["clock"].t + timedelta(minutes=1))
    assert not u["normalized_status"].isin(RW.POSTGAME).any()
    fr, _ = RW.availability_frame(ev, GAMES, env["clock"].t + timedelta(minutes=1))
    assert "101" not in set(fr["rotowire_player_id"]) and not fr["status"].isin(RW.POSTGAME).any()


# ============================================================================ 20 no invented P(play)
def test_20_no_invented_p_play(env):
    env["run"](Site())
    fr, _ = RW.availability_frame(env["store"].frame(), GAMES, env["clock"].t + timedelta(minutes=1))
    assert "p_play" not in fr.columns
    assert dict(zip(fr["player_id"], fr["status"])) == {"P1": "OUT", "P5": "GTD"}
    rows = pd.DataFrame({"game_id": ["G1"] * 4, "team_id": ["T1", "T1", "T2", "T2"], "player_id": ["P1", "P2", "P5", "P6"],
                         "p_play": [0.91, 0.8, 0.77, 0.6], "min_mu": [30.0, 10.0, 12.0, 8.0], "min_sigma": [5.0] * 4,
                         "role_diagnostic": ["TOP5_MINUTES", "BENCH", "BENCH", "BENCH"]})
    for s in IN.L.STATS:
        rows[f"{s}_mu"] = 1.0
    out, flags = IN.apply_availability(rows, fr)
    o = out.set_index("player_id")
    assert np.allclose(o["p_play"].to_numpy(), [0.91, 0.8, 0.77, 0.6])          # every P(play) exactly unchanged
    assert o.loc["P1", "player_suppression"] == "CONFIRMED_UNAVAILABLE"
    assert o.loc["P5", "player_suppression"] == "" and o.loc["P5", "availability_gate"] == RW.GATE_UNCERTAIN
    assert "UNSUPPORTED_ROTATION_SHOCK" in flags["G1"]["reasons"]


# ============================================================================ 26 determinism + provenance + secrets
def test_26_repeat_parsing_is_deterministic():
    a = W.parse_news_html(DEFAULT_NEWS, T0.isoformat(), "s")
    b = W.parse_news_html(DEFAULT_NEWS, T0.isoformat(), "s")
    assert a == b and [i["item_key"] for i in a[0]] == [i["item_key"] for i in b[0]]
    t = json.dumps(DEFAULT_ROWS).encode()
    assert W.parse_snapshot_table(t, "c", "s") == W.parse_snapshot_table(t, "c", "s")


def test_events_carry_web_provenance_not_api_paths(env):
    env["run"](Site())
    for e in env["store"].load():
        assert e["acquisition_source"] == "ROTOWIRE_WEB" and e["source_endpoint"] in (W.NEWS_URL, W.SNAPSHOT_TABLE_URL)
        assert "api.rotowire.com" not in json.dumps(e)
    led = W.OS.read_jsonl(env["ledger"])
    assert {e["provider"] for e in led} == {"rotowire-web"} and {e["endpoint"] for e in led} == {"news_injuries", "snapshot"}


def test_acceptance_mode_writes_evidence_but_no_event_map_or_ledger(env):
    r = env["run"](Site(), ingest=False)
    s = W.acceptance_summary(r, env["ctx"], env["maps"])
    assert s["ROTOWIRE_WEB_LIVE_ACCEPTANCE"] == "PASS" and s["SNAPSHOT_ROWS"] == 2 and s["NEWS_ROWS"] == 2
    assert s["PLAYER_ID_EXTRACTION_RATE"] == 1.0 and s["TEAM_MAPPING_RATE"] == 1.0 and s["PLAYER_MAPPING_RATE"] == 1.0
    assert not env["store"].load() and not env["ledger"].exists() and not env["maps"].exists()
    assert list((env["root"] / "raw").rglob("*.meta.json"))


def test_cookie_session_file_rules_and_no_secret_leaks(env, tmp_path):
    ck = tmp_path / "rw.cookies"
    ck.write_text(f"Cookie: rw_session={COOKIE_SECRET}; other=1\n")
    os.chmod(ck, 0o644)
    with pytest.raises(W.WebAcquisitionFailure) as e:
        W.WebClient(cookie_file=ck).check()
    assert e.value.reason == W.R_SESSION_INSECURE and COOKIE_SECRET not in str(e.value)
    os.chmod(ck, 0o600)
    c = W.WebClient(cookie_file=ck)
    c.check()
    assert {x.name for x in c.session.cookies} == {"rw_session", "other"}
    ns = tmp_path / "ns.cookies"
    ns.write_text(f"# Netscape\n.rotowire.com\tTRUE\t/\tTRUE\t0\trw_session\t{COOKIE_SECRET}\n"
                  f".example.com\tTRUE\t/\tTRUE\t0\tforeign\tx\n")
    assert [x[0] for x in W._load_cookie_file(ns)] == ["rw_session"]
    with pytest.raises(W.WebAcquisitionFailure) as e:
        W.WebClient(cookie_file=tmp_path / "absent").check()
    assert e.value.reason == W.R_SESSION_MISSING
    r = W.acquire(W.WebClient(cookie_file=tmp_path / "absent", clock=Clock()), env["ctx"], store=env["store"],
                  root=env["root"], ledger_path=env["ledger"], map_root=env["maps"])
    assert r["reasons"] == [W.R_SESSION_MISSING] and _no_processed(env)
    env["run"](Site())
    for p in tmp_path.rglob("*"):
        if p.is_file() and p not in (ck, ns):
            data = p.read_bytes()
            assert COOKIE_SECRET.encode() not in data, p


def test_url_allowlist():
    with pytest.raises(W.WebAcquisitionFailure):
        W.WebClient(get=lambda u: None).fetch("https://www.rotowire.com/cbasketball/something-else.php")


# ============================================================================ source selection + lifecycle wiring
def test_rotowire_source_selection(monkeypatch, tmp_path):
    monkeypatch.delenv("ROTOWIRE_SOURCE", raising=False)
    assert PC.rotowire_source() is None
    for v, want in (("disabled", "DISABLED"), ("WEB", "WEB"), (" api ", "API")):
        monkeypatch.setenv("ROTOWIRE_SOURCE", v)
        assert PC.rotowire_source() == want
    monkeypatch.setenv("ROTOWIRE_SOURCE", "SCRAPE")
    with pytest.raises(ValueError):
        PC.rotowire_source()
    life = L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod")
    monkeypatch.setenv("ROTOWIRE_SOURCE", "DISABLED")
    monkeypatch.setenv("ROTOWIRE_API_KEY", "dummy-not-a-key")
    assert L.rotowire_state(life) == "DISABLED"
    monkeypatch.setenv("ROTOWIRE_SOURCE", "WEB")
    monkeypatch.delenv("ROTOWIRE_API_KEY")
    monkeypatch.delenv(PC.ROTOWIRE_WEB_COOKIE_ENV, raising=False)
    assert L.rotowire_state(life) == "BLOCKED_NO_WEB_SESSION"             # WEB never needs ROTOWIRE_API_KEY
    monkeypatch.setenv(PC.ROTOWIRE_WEB_COOKIE_ENV, str(tmp_path / "ck"))
    assert L.rotowire_state(life) == "DISABLED_WEB_ACCEPTANCE_NOT_PASSED"
    W.OS.write_json(tmp_path / "ops" / "markers" / W.ACCEPTANCE_MARKER, {"status": "FAIL"})
    assert L.rotowire_state(life) == "DISABLED_WEB_ACCEPTANCE_NOT_PASSED"
    W.OS.write_json(tmp_path / "ops" / "markers" / W.ACCEPTANCE_MARKER, {"status": "PASS"})
    assert L.rotowire_state(life) == "ENABLED"
    monkeypatch.delenv("ROTOWIRE_SOURCE")
    assert L.rotowire_state(life) == "BLOCKED_NO_KEY"                     # unset: the legacy rule, unchanged


def test_refresh_runs_web_acquisition_only_when_web_enabled(monkeypatch, tmp_path, ctx):
    calls = []
    monkeypatch.setattr(L, "forecast", lambda life, window: {"status": "NO_FORECAST_DUE", "rotowire_refresh": L.rotowire_state(life)})
    monkeypatch.setattr(L, "exports_for", lambda life, day: {})
    monkeypatch.setattr(L, "web_acquire", lambda life: calls.append(1) or {"auth": "PASS"})
    life = L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", clock=lambda: T0)
    for src in (None, "DISABLED", "API"):
        if src:
            monkeypatch.setenv("ROTOWIRE_SOURCE", src)
        else:
            monkeypatch.delenv("ROTOWIRE_SOURCE", raising=False)
        L.refresh(life, "10:00")
    monkeypatch.setenv("ROTOWIRE_SOURCE", "WEB")
    monkeypatch.setenv(PC.ROTOWIRE_WEB_COOKIE_ENV, str(tmp_path / "ck"))
    L.refresh(life, "13:00")                                               # acceptance not passed: no acquisition
    assert calls == []
    W.OS.write_json(tmp_path / "ops" / "markers" / W.ACCEPTANCE_MARKER, {"status": "PASS"})
    out = L.refresh(life, "16:00")
    assert calls == [1] and out["rotowire_web_acquisition"] == {"auth": "PASS"}
    h = json.loads((tmp_path / "prod" / "health" / f"{L.et_date(T0).isoformat()}.json").read_text())
    assert h["availability_1600"]["rotowire_web_acquisition"] == {"auth": "PASS"} and h["rotowire_mode"] == "SOURCE_WEB"


# ============================================================================ Droplet runner (bash; no network)
RUN = Path(__file__).resolve().parents[1] / "deploy" / "droplet" / "bin" / "cbb-originator-run"


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, text=True)  # noqa: E731
    g("init", "-q")
    (r / "README").write_text("x\n")
    g("add", "README")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    state = tmp_path / "state"
    state.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "CBB_APP_ROOT": str(r), "CBB_STATE_ROOT": str(state),
           "CBB_EXPECTED_COMMIT": g("rev-parse", "HEAD").stdout.strip(), "CBB_VENV_DIR": str(tmp_path / "venv"),
           **{k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                               "NUMEXPR_NUM_THREADS")}}
    return {"env": env, "state": state}


def _pre(repo, **kv):
    env = {**repo["env"], **{k: v for k, v in kv.items() if v is not None}}
    return subprocess.run(["bash", str(RUN), "preflight"], env=env, capture_output=True, text=True, timeout=60)


def test_runner_source_rules(repo):
    ck = repo["state"] / "secrets" / "rotowire_web.cookies"
    ck.parent.mkdir()
    ck.write_text("Cookie: a=b\n")
    os.chmod(ck, 0o600)
    assert _pre(repo).returncode == 0
    assert _pre(repo, ROTOWIRE_SOURCE="DISABLED").returncode == 0
    assert _pre(repo, ROTOWIRE_ENABLED="true").returncode == 3                       # legacy: still refused
    assert _pre(repo, ROTOWIRE_SOURCE="DISABLED", ROTOWIRE_ENABLED="true").returncode == 3
    assert _pre(repo, ROTOWIRE_SOURCE="API", ROTOWIRE_ENABLED="true").returncode == 3  # no API key on this host
    assert _pre(repo, ROTOWIRE_SOURCE="SCRAPE").returncode == 3
    assert _pre(repo, ROTOWIRE_SOURCE="WEB", ROTOWIRE_ENABLED="false", ROTOWIRE_WEB_COOKIE_FILE=str(ck)).returncode == 3
    assert _pre(repo, ROTOWIRE_SOURCE="WEB", ROTOWIRE_ENABLED="true").returncode == 3  # no session file configured
    assert _pre(repo, ROTOWIRE_SOURCE="WEB", ROTOWIRE_ENABLED="true",
                ROTOWIRE_WEB_COOKIE_FILE="/tmp/elsewhere.cookies").returncode == 3
    ok = _pre(repo, ROTOWIRE_SOURCE="WEB", ROTOWIRE_ENABLED="true", ROTOWIRE_WEB_COOKIE_FILE=str(ck))
    assert ok.returncode == 0 and "rotowire source WEB" in ok.stderr and "a=b" not in ok.stderr + ok.stdout
    os.chmod(ck, 0o644)
    assert _pre(repo, ROTOWIRE_SOURCE="WEB", ROTOWIRE_ENABLED="true", ROTOWIRE_WEB_COOKIE_FILE=str(ck)).returncode == 3


def test_identity_context_tolerates_missing_mascots(tmp_path, monkeypatch):
    """Regression (Droplet 2026-11-08): core.teams.mascot NULL -> pd.NA broke matching.team_index in the loader."""
    import duckdb
    from cbb_platform.prod import state as ST
    hist, inc = tmp_path / "hist.duckdb", tmp_path / "inc.duckdb"
    con = duckdb.connect(str(hist))
    con.execute("CREATE SCHEMA core; CREATE TABLE core.teams AS SELECT * FROM (VALUES ('T1','Home U','Home U',NULL),"
                "('T2','Away St','Away St','Hawks')) t(canonical_team_id, school, display_name, mascot)")
    con.close()
    con = duckdb.connect(str(inc))
    con.execute("CREATE SCHEMA core; CREATE SCHEMA flagship; CREATE TABLE core.teams AS SELECT * FROM (VALUES "
                "('T3','New U','New U')) t(canonical_team_id, school, display_name);"
                "CREATE TABLE flagship.rosters AS SELECT * FROM (VALUES (2027,'P1','T1','Jon','Doe','Jon Doe','G')) "
                "r(season, canonical_player_id, canonical_team_id, first_name, last_name, player_name, position)")
    con.close()
    monkeypatch.setattr(ST, "current", lambda root: {"increment_db": inc})
    c = RW.load_identity_context(tmp_path, hist, tmp_path / "maps")
    assert c.resolve_team({"team_name": "Home U"})[0] == "T1" and c.resolve_team({"team_name": "New U"})[0] == "T3"


# ============================================================================ curated team aliases (2026-11-08 review)
OBSERVED_ALIAS_NAMES = ["Connecticut", "Louisiana State", "Mississippi", "St. Joseph's", "Texas-Arlington",
                        "Texas-San Antonio", "UNC-Charlotte", "Wisconsin-Green Bay"]


def aliases(**m):
    return pd.DataFrame([{"rotowire_team_name": k, "canonical_team_id": v, "canonical_display_name": "x",
                          "verified_on": "2026-11-08", "evidence": "test"} for k, v in m.items()], columns=W.ALIAS_COLS)


def test_committed_alias_file_is_exactly_the_reviewed_eight_and_valid(tmp_path):
    m = W.team_aliases()
    assert list(m["rotowire_team_name"]) == OBSERVED_ALIAS_NAMES
    assert m["canonical_team_id"].str.fullmatch(r"T-[0-9a-f]{16}").all() and m["canonical_team_id"].is_unique
    assert (m["verified_on"] == "2026-11-08").all() and m["evidence"].str.contains("W20261108T150352Z-60c436").all()
    expected = {"Connecticut": "UConn Huskies", "Louisiana State": "LSU Tigers", "Mississippi": "Ole Miss Rebels",
                "St. Joseph's": "Saint Joseph's Hawks", "Texas-Arlington": "UT Arlington Mavericks",
                "Texas-San Antonio": "UTSA Roadrunners", "UNC-Charlotte": "Charlotte 49ers",
                "Wisconsin-Green Bay": "Green Bay Phoenix"}
    assert dict(zip(m["rotowire_team_name"], m["canonical_display_name"])) == expected
    bad = tmp_path / "a.csv"
    m2 = pd.concat([m, m.iloc[:1]])
    m2.to_csv(bad, index=False)
    with pytest.raises(ValueError, match="duplicate"):
        W.team_aliases(bad)
    m.assign(evidence="").to_csv(bad, index=False)
    with pytest.raises(ValueError, match="required"):
        W.team_aliases(bad)
    m.drop(columns="evidence").to_csv(bad, index=False)
    with pytest.raises(ValueError, match="columns"):
        W.team_aliases(bad)


def _f(pid, first, last, team, pos="G"):
    return {"rotowire_player_id": pid, "first_name": first, "last_name": last, "team_name": team, "position": pos}


def test_alias_maps_only_the_exact_spelling_and_is_audited(ctx, tmp_path):
    al = aliases(**{"Home University": "T1"})
    res = W.extend_maps(ctx, [_f("101", "Jon", "Doe", "Home University"), _f("102", "Ann", "Lee", "Home Univ."),
                              _f("103", "Ann", "Lee", "home university")], tmp_path, aliases=al)
    tm = pd.read_csv(tmp_path / W.TEAM_MAP_FILE, dtype=str)
    assert dict(zip(tm["rotowire_team_name"], tm["method"])) == {"Home University": W.M_TEAM_ALIAS}
    assert {x["rotowire_team_name"] for x in res["review"] if x["kind"] == "TEAM"} == {"Home Univ.", "home university"}
    pm = pd.read_csv(tmp_path / W.PLAYER_MAP_FILE, dtype=str)
    assert dict(zip(pm["rotowire_player_id"], pm["canonical_player_id"])) == {"101": "P1"}   # player rule after alias
    assert W.WebIdentity(ctx, tmp_path).resolve({"team_name": "Home University", "rotowire_player_id": "101"}
                                                 )["identity_resolution_status"] == "RESOLVED"


def test_alias_conflicts_and_off_roster_alias_never_map_or_overwrite(ctx, tmp_path):
    res = W.extend_maps(ctx, [_f("101", "Jon", "Doe", "Home U"), _f("9", "A", "B", "Ghost U")], tmp_path,
                        aliases=aliases(**{"Home U": "T2", "Ghost U": "T404"}))
    why = {x["rotowire_team_name"]: x["reason"] for x in res["review"] if x["kind"] == "TEAM"}
    assert why == {"Home U": "CURATED_ALIAS_CONFLICTS_WITH_EXACT_NAME", "Ghost U": "CURATED_ALIAS_TEAM_NOT_ON_CURRENT_ROSTER"}
    assert not (tmp_path / W.TEAM_MAP_FILE).exists()
    W.extend_maps(ctx, [_f("101", "Jon", "Doe", "Home U")], tmp_path, aliases=aliases())        # persisted: Home U -> T1
    before = (tmp_path / W.TEAM_MAP_FILE).read_bytes()
    res = W.extend_maps(ctx, [_f("101", "Jon", "Doe", "Home U")], tmp_path, aliases=aliases(**{"Home U": "T2"}))
    assert (tmp_path / W.TEAM_MAP_FILE).read_bytes() == before
    assert [x["reason"] for x in res["review"]] == ["CURATED_ALIAS_CONFLICTS_WITH_PERSISTED_MAP"]


# ============================================================================ player review rules
def test_position_discrepancy_alone_does_not_reject_a_unique_exact_match_but_is_audited(ctx, tmp_path):
    res = W.extend_maps(ctx, [_f("201", "Kai", "Ito", "Away St", pos="G")], tmp_path, aliases=aliases())  # roster: Center
    pm = pd.read_csv(tmp_path / W.PLAYER_MAP_FILE, dtype=str)
    assert pm[["rotowire_player_id", "canonical_player_id", "method"]].values.tolist() == [["201", "P5", W.M_PLAYER_POS]]
    d = W.OS.read_jsonl(tmp_path / W.DISCREPANCY_FILE)
    assert len(d) == 1 and d[0]["rotowire_position"] == "G" and d[0]["roster_position"] == "Center"
    assert d[0]["canonical_player_id"] == "P5" and res["discrepancies"] == d
    W.extend_maps(ctx, [_f("201", "Kai", "Ito", "Away St", pos="G")], tmp_path, aliases=aliases())
    assert len(W.OS.read_jsonl(tmp_path / W.DISCREPANCY_FILE)) == 1                         # recorded once


def test_two_same_name_roster_rows_or_a_shared_canonical_player_stay_unresolved(tmp_path):
    teams = pd.DataFrame({"canonical_team_id": ["T1"], "school": ["Home U"], "display_name": ["Home U"], "mascot": [None]})
    roster = pd.DataFrame([("P1", "T1", "Jon", "Doe", "Jon Doe", "Guard"), ("P9", "T1", "Jon", "Doe", "Jon Doe", "Center"),
                           ("P2", "T1", "Ann", "Lee", "Ann Lee", "Forward")],
                          columns=["canonical_player_id", "canonical_team_id", "first_name", "last_name", "player_name", "position"])
    c = RW.IdentityContext(teams, roster)
    res = W.extend_maps(c, [_f("101", "Jon", "Doe", "Home U"), _f("102", "Ann", "Lee", "Home U"),
                            _f("103", "Ann", "Lee", "Home U")], tmp_path, aliases=aliases())
    why = {x["rotowire_player_id"]: x["reason"] for x in res["review"]}
    assert why == {"101": "EXACT_NAME_MULTIPLE", "102": "CANONICAL_PLAYER_ALREADY_MAPPED",
                   "103": "CANONICAL_PLAYER_ALREADY_MAPPED"}
    assert not (tmp_path / W.PLAYER_MAP_FILE).exists()


def test_unresolved_players_carry_the_exact_reason_and_are_never_guessed(ctx, tmp_path):
    res = W.extend_maps(ctx, [_f("301", "Jonathan", "Doe", "Home U"),      # near name only (same last name)
                              _f("302", "Kai", "Ito", "Home U", pos="C"),  # exact name, but on the OTHER team's roster
                              _f("303", "Zed", "Quill", "Home U")], tmp_path, aliases=aliases())
    r = {x["rotowire_player_id"]: x for x in res["review"]}
    assert r["301"]["reason"] == "NO_EXACT_NAME_ON_TEAM_ROSTER_NEAR_NAME_NOT_MAPPED"
    assert [x["canonical_player_id"] for x in r["301"]["candidates"]] == ["P1"]
    assert r["302"]["reason"] == "EXACT_NAME_ONLY_ON_OTHER_TEAM_ROSTER"
    assert r["303"]["reason"] == "NOT_ON_CURRENT_TEAM_ROSTER" and r["303"]["candidates"] == []
    assert not (tmp_path / W.PLAYER_MAP_FILE).exists()
    assert len(W.OS.read_jsonl(tmp_path / W.REVIEW_FILE)) == 3


def test_acceptance_reports_unresolved_and_requires_every_team_resolved(env):
    html = news_page(item("101", "Jon Doe", "Home U", "G", "Knee", "Ruled out", "Doe is out Tuesday."),
                     item("777", "Zed Quill", "Mystery College", "G", "Knee", "Ruled out", "Quill is out."))
    r = env["run"](Site(news=html), ingest=False)
    s = W.acceptance_summary(r, env["ctx"], env["maps"])
    assert s["UNRESOLVED_TEAMS"] == ["Mystery College"] and s["TEAM_MAPPING_RATE"] == 0.6667
    assert s["ROTOWIRE_WEB_LIVE_ACCEPTANCE"] == "FAIL" and s["TEAM_MAPPING_RATE_PERSISTED_MAP"] == 0.0
    assert [x["rotowire_player_id"] for x in s["UNRESOLVED_PLAYERS"]] == ["777"]
    assert s["UNRESOLVED_PLAYERS"][0]["reason"] == "TEAM_UNMATCHED"
    assert s["NEWS_COVERAGE_COMPLETE"] == "UNPROVEN_25_ITEM_WINDOW" and s["NEWS_WINDOW_SIZE"] == 2


# ============================================================================ news continuity guard
NEWER = news_page(*[item(str(500 + i), f"Pl {chr(97 + i)}", "Home U", "G", "Knee", "Ruled out", f"Player {i} is out.",
                         date="November 11, 2026") for i in range(3)])


def test_news_window_without_overlap_is_a_coverage_gap_that_fails_every_game_closed(env):
    r1 = env["run"](Site())
    assert r1["news_continuity"] == "BASELINE" and r1["news_status"] == "PROCESSED"
    n_news = sum(e["event_kind"] == "NEWS_UPDATE" for e in env["store"].load())
    r2 = env["run"](Site(news=NEWER))                                       # shares no item with the last window
    assert r2["news_continuity"] == "GAP" and r2["news_status"] == "COVERAGE_GAP"
    assert r2["reasons"] == [W.R_NEWS_GAP] == ["ROTOWIRE_WEB_NEWS_COVERAGE_GAP"]
    assert r2["snapshot_status"] == "PROCESSED"                             # the snapshot still reconciles
    assert sum(e["event_kind"] == "NEWS_UPDATE" for e in env["store"].load()) == n_news   # no news event from a gap
    led = W.OS.read_jsonl(env["ledger"])
    gap = [e for e in led if e.get("status") == RW.NEWS_GAP_STATUS]
    assert len(gap) == 1 and gap[0]["reason"] == W.R_NEWS_GAP and len(processed(env, "news_injuries")) == 1
    at = pd.Timestamp(env["clock"].t) + pd.Timedelta(minutes=1)
    g = GAMES.assign(start_time=[at + pd.Timedelta(hours=30)])              # earlier PROCESSED news is within its bound
    f = RW.freshness(led, g, at)
    assert f["game_reasons"]["G1"] == [RW.NEWS_COVERAGE_GAP] and f["news_coverage_gap_since"]
    w = W.OS.read_jsonl(env["root"] / W.NEWS_WINDOW_FILE)                   # every observed window + IDs persisted
    assert [x["continuity"] for x in w] == ["BASELINE", "GAP"] and w[1]["rotowire_player_ids"] == ["500", "501", "502"]
    assert {i["provider_player_id"] for i in W.OS.read_jsonl(env["root"] / "news_items.jsonl")} >= {"500", "501", "502"}
    # the next window overlaps the gap window -> continuous again, PROCESSED, the gate closes
    later = news_page(item("503", "Pl x", "Home U", "G", "Knee", "Ruled out", "Player x is out.", date="November 11, 2026"),
                      *[item(str(500 + i), f"Pl {chr(97 + i)}", "Home U", "G", "Knee", "Ruled out", f"Player {i} is out.",
                             date="November 11, 2026") for i in range(3)])
    r3 = env["run"](Site(news=later))
    assert r3["news_continuity"] == "CONTINUOUS" and r3["news_window_overlap"] == 3 and r3["news_status"] == "PROCESSED"
    at = pd.Timestamp(env["clock"].t) + pd.Timedelta(minutes=1)
    assert RW.freshness(W.OS.read_jsonl(env["ledger"]), GAMES.assign(start_time=[at + pd.Timedelta(hours=30)]),
                        at)["game_reasons"]["G1"] == []


def test_failed_polls_and_acceptance_runs_do_not_reset_continuity(env):
    env["run"](Site(), ingest=False)                                        # acceptance: recorded, never the baseline
    assert env["run"](Site(news=NEWER))["news_continuity"] == "BASELINE"
    env["run"](Site(news=Resp("", url=W.NEWS_URL)))                         # failed poll: no window recorded
    assert env["run"](Site(news=NEWER))["news_continuity"] == "CONTINUOUS"
    assert env["run"](Site())["news_continuity"] == "GAP"
    assert [x["mode"] for x in W.OS.read_jsonl(env["root"] / W.NEWS_WINDOW_FILE)] == ["ACCEPTANCE", "INGEST", "INGEST", "INGEST"]


def test_coverage_gap_code_is_known_blocking_and_unpublishable():
    from cbb_platform.prod import production_board as PB, readiness as RD
    c = RW.NEWS_COVERAGE_GAP
    assert c in RD.AVAILABILITY_GAME_REASONS and RD.evaluate((), (c,))["readiness_state"] == "BLOCKED"
    assert PB.CODE_CONDITION[c] == "MISSING_STATE" and PB.condition_of(c)[:3] == (False, False, False)
