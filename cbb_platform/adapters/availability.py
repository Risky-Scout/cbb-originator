"""Current-season availability adapter — provider: Sharp Stats (`sharp_stats`), the frozen P0
production availability source (AMENDMENT_2026-10-04_DATA_SOURCE_FREEZE, architect Stage 1 closure).

No scraper. A human saves a capture of the injury page and ingests it:

    .venv/bin/python -m cbb_platform.adapters.availability ingest sharp_stats path/to/capture.html \
        [--observed-at 2026-11-04T21:00:00Z]

Accepted capture formats
  * .csv  — the provider-independent capture format, header (case-insensitive):
            team, player, position, class, injury, status, updated, note
  * .html — saved web page: every <table> is read; the team is the nearest preceding heading
            (h1-h6) or <caption>; columns are matched by header name (Player/Name, Pos/Position,
            Class/Yr, Injury/Reason, Status, Date/Updated, Note/Notes/Comment/Details).
            A single-cell row directly under a player row is that player's narrative note.

PIT: `received_at` = when the snapshot entered our raw store (authoritative). A user-declared
`--observed-at` capture time is kept in the raw metadata for audit only.

Rules
  * raw status and raw note are both preserved verbatim; status_normalized comes ONLY from
    the structured status field — the note never overwrites it;
  * status/note contradictions set conflict_flag + conflict_type (documented, tested rules);
  * identical snapshot bytes -> idempotent no-op; changed snapshot -> new append-only events.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
from html.parser import HTMLParser

import duckdb

from cbb_platform import config as C, raw_store

PARSER_VERSION = "availability-parser-1.0.0"
PROVIDERS = ("sharp_stats",)   # provider identity, stored verbatim in availability.events.provider

# ---------------------------------------------------------------------------
# Status normalization: exact tokens only; anything else is UNKNOWN (raw kept).
# ---------------------------------------------------------------------------
STATUS_MAP = {
    "probable": "PROBABLE",
    "questionable": "QUESTIONABLE",
    "doubtful": "DOUBTFUL",
    "out": "OUT",
    "out for season": "OUT_SEASON", "out for the season": "OUT_SEASON", "out season": "OUT_SEASON",
    "out-season": "OUT_SEASON",
}


def normalize_status(raw: str | None) -> str:
    key = re.sub(r"\s+", " ", (raw or "").strip().lower())
    return STATUS_MAP.get(key, "UNKNOWN")


# Note signals (DATA_SOURCE_POLICY §4). Each conflict rule compares a note signal with the
# structured status; it never changes status_normalized.
NOTE_OUT_SEASON = re.compile(r"out for (the )?season|season[- ]ending|rest of the season", re.I)
NOTE_AVAILABLE = re.compile(r"expected to play|will play|is available|cleared to play|decent chance to play", re.I)
NOTE_OUT = re.compile(r"not expected to play|will not play|won't play|ruled out|will miss|is out\b", re.I)


def conflict(status_norm: str, note: str | None) -> tuple[bool, str | None]:
    note = note or ""
    if NOTE_OUT_SEASON.search(note) and status_norm != "OUT_SEASON":
        return True, "NOTE_SAYS_OUT_SEASON_STATUS_DIFFERS"
    if NOTE_OUT.search(note) and status_norm in ("PROBABLE", "QUESTIONABLE"):
        return True, "NOTE_SAYS_OUT_STATUS_SAYS_LIKELY_AVAILABLE"
    if NOTE_AVAILABLE.search(note) and not NOTE_OUT.search(note) and status_norm in ("OUT", "OUT_SEASON", "DOUBTFUL"):
        return True, "NOTE_SAYS_AVAILABLE_STATUS_SAYS_UNAVAILABLE"
    return False, None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
FIELD_ALIASES = {
    "team": ("team",), "player": ("player", "name"), "position": ("pos", "position"),
    "class": ("class", "yr", "year"), "injury": ("injury", "reason", "type"), "status": ("status",),
    "updated": ("updated", "date", "update"), "note": ("note", "notes", "comment", "details", "description"),
}


def _field(header: str) -> str | None:
    h = re.sub(r"[^a-z]", "", header.lower())
    for name, aliases in FIELD_ALIASES.items():
        if h in aliases:
            return name
    return None


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    cols = {c: _field(c) for c in reader.fieldnames or []}
    return [{cols[k]: (v or "").strip() or None for k, v in row.items() if cols.get(k)} for row in reader]


class _TableParser(HTMLParser):
    """Collects (heading_text, rows) for every <table>; rows are lists of cell texts."""

    def __init__(self):
        super().__init__()
        self.tables, self.heading, self._in_heading, self._cell, self._row, self._rows = [], None, False, None, None, None

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "caption"):
            self._in_heading, self._heading_buf = True, []
        elif tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "caption") and self._in_heading:
            self.heading, self._in_heading = " ".join("".join(self._heading_buf).split()) or self.heading, False
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            self.tables.append((self.heading, self._rows))
            self._rows = None

    def handle_data(self, data):
        if self._in_heading:
            self._heading_buf.append(data)
        if self._cell is not None:
            self._cell.append(data)


def parse_html(text: str) -> list[dict]:
    p = _TableParser()
    p.feed(text)
    out: list[dict] = []
    for heading, rows in p.tables:
        if not rows:
            continue
        cols = [_field(h) for h in rows[0]]
        if "player" not in cols:
            continue
        for r in rows[1:]:
            if len(r) == 1 and out and out[-1].get("_table") is rows:   # narrative note row
                out[-1]["note"] = (out[-1].get("note") + " | " if out[-1].get("note") else "") + r[0]
                continue
            rec = {c: (v or None) for c, v in zip(cols, r) if c}
            rec.setdefault("team", heading)
            rec["_table"] = rows
            out.append(rec)
    for rec in out:
        rec.pop("_table", None)
    return out


def parse_snapshot(payload: bytes, ext: str) -> list[dict]:
    text = payload.decode("utf-8", errors="replace")
    return parse_csv(text) if ext == "csv" else parse_html(text)


# ---------------------------------------------------------------------------
# Ingest (raw, immutable) and normalize (raw -> availability.events)
# ---------------------------------------------------------------------------
def ingest(provider: str, path: str, observed_at: str | None = None, *, synthetic: bool = False,
           root=None) -> raw_store.RawRecord:
    provider = provider.lower()
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    ext = path.rsplit(".", 1)[-1].lower()
    with open(path, "rb") as fh:
        payload = fh.read()
    return raw_store.store(provider, "injuries", payload, ext=ext, root=root,
                           params={"observed_at_declared": observed_at, "original_filename": path.rsplit("/", 1)[-1],
                                   "synthetic_fixture": synthetic})


def normalize_all(con: duckdb.DuckDBPyConnection, root=None) -> None:
    for provider in PROVIDERS:
        for path, meta in raw_store.records(provider, root=root):
            rows = parse_snapshot(path.read_bytes(), path.suffix.lstrip("."))
            for n, r in enumerate(rows, start=1):
                status_norm = normalize_status(r.get("status"))
                flag, ctype = conflict(status_norm, r.get("note"))
                event_id = "AV-" + hashlib.sha256(f"{provider}|{meta['sha256']}|{n}".encode()).hexdigest()[:20]
                team_id = _resolve_team(con, r.get("team"))
                player_id, ident = _resolve_player(con, team_id, r.get("player"))
                con.execute("""INSERT INTO availability.events VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,getvariable('proc'),?,?,?,?,?,?,?,?)""", [
                    event_id, provider, meta["sha256"], n, r.get("team"), r.get("player") or "",
                    r.get("position"), r.get("class"), r.get("injury"), r.get("status"), status_norm,
                    r.get("note"), r.get("updated"), meta["received_at"], flag, ctype, team_id, player_id,
                    ident, PARSER_VERSION, bool(meta["params"].get("synthetic_fixture")),
                    str(path.relative_to(path.parents[2]))])


def _resolve_team(con, name: str | None) -> str | None:
    """Exact match on CBBD school or display name only. Anything else stays unresolved."""
    if not name:
        return None
    rows = con.execute("SELECT DISTINCT canonical_team_id FROM core.teams WHERE school = ? OR display_name = ?",
                       [name, name]).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def _resolve_player(con, team_id: str | None, name: str | None) -> tuple[str | None, str]:
    """Exact name among players whose MOST RECENT season-stats team is `team_id`.

    Until CBBD current-season rosters are ingested this is necessarily a prior-season match,
    so even a unique hit is returned as RESOLVED_NEEDS_REVIEW (never silently trusted).
    """
    if not team_id or not name:
        return None, "UNRESOLVED_NO_TEAM" if not team_id else "UNRESOLVED_NO_NAME"
    rows = con.execute("""
        SELECT DISTINCT canonical_player_id FROM (
            SELECT canonical_player_id, arg_max(canonical_team_id, season) AS last_team,
                   arg_max(source_player_name, season) AS last_name
            FROM fact.player_season_stats GROUP BY 1)
        WHERE last_team = ? AND last_name = ?""", [team_id, name]).fetchall()
    if len(rows) == 1:
        return rows[0][0], "RESOLVED_NEEDS_REVIEW_PRIOR_SEASON_TEAM_EXACT_NAME"
    return None, "UNRESOLVED_AMBIGUOUS" if rows else "UNRESOLVED_NO_MATCH"


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 3 and args[0] == "ingest":
        obs = args[args.index("--observed-at") + 1] if "--observed-at" in args else None
        rec = ingest(args[1], args[2], obs)
        print(json.dumps({"stored": str(rec.path), "created": rec.created, "sha256": rec.sha256}, indent=1))
    else:
        print(__doc__)
