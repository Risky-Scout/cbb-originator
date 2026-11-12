"""Sportsbook market consensus for one GAME x PLAYER x STAT (CBB_Boards market fields). Pure functions: no network,
no model, no file IO. Every sportsbook observation is returned in the audit as INCLUDED or with one exclusion reason.

Input: the quote observations of ONE capture (the latest collection of the game made before the Board's as-of),
already normalized (prod.quotes), strictly matched (prod.matching) and freshness-checked (prod.board.quote_freshness).

Order of the deterministic rules (thresholds are module constants, tested):
 1. row exclusions from the production normalizer / matcher / freshness check
        INVALID_QUOTE -> MALFORMED_MARKET, UNSUPPORTED_MARKET, UNMATCHED_/AMBIGUOUS_ PLAYER / GAME -> IDENTITY_MISMATCH,
        STALE_QUOTE, POST_CUTOFF_QUOTE, an alternate-market key -> ALTERNATE_MARKET (never requested)
 2. DUPLICATE_BOOK_OBSERVATION  same book, line and side twice: the latest market_last_update is kept
 3. UNPAIRED_PRICE              a book's line without both OVER and UNDER (no two-sided de-vig is possible)
 4. NON_PRIMARY_LINE            a book with several paired lines keeps ONE primary line: the line whose de-vigged over
                                probability is closest to 0.5 (ties: lower line)
 5. SUPERSEDED_QUOTE            stale-move protection: when >= MOVE_MIN_BOOKS books updated within MOVE_WINDOW_SECONDS of
                                the newest update all quote one line, every one of them updated after every other book,
                                and the older books' median line differs, the older books on a different line are
                                superseded (recency outranks a stale consensus)
 6. LINE_OUTLIER                only with >= MAD_MIN_BOOKS (5) independent books: robust z = |line - median| /
                                (1.4826 MAD) > MAD_Z. MAD = 0: the median cluster (>= 3 books) defines consensus and a
                                differing line is excluded unless >= 2 differing books agree on one line and are all newer
                                than the whole cluster (a coordinated newer move). 3-4 books: never excluded on distance;
                                1-2 books: retained (confidence LOW / SINGLE_BOOK)
 7. CONSENSUS_LINE              ORDINARY MEDIAN of the remaining primary lines (no weights: no approved weight table)
 8. consensus price             ONLY the books quoting exactly CONSENSUS_LINE (no interpolation): each book's pair is
                                de-vigged with the EXISTING production method prod.exports.no_vig (proportional:
                                (1/d_side) / (1/d_over + 1/d_under)); CONSENSUS_*_FAIR_PROB = median of the books' fair
                                probabilities; displayed odds = props_v1_pricing.decimal_to_american(prob_to_decimal(p))
                                (the existing production conversion). Raw American / decimal odds and vigged
                                probabilities are never averaged.
Books on another line keep their audit row (INCLUDED_OTHER_LINE) and count in RAW_BOOK_COUNT, not in the price.

MARKET_STATUS: VALID_MARKET (>= 1 book at the consensus line) | NO_EXACT_LINE_PRICE (books remain but the median is a
line none of them quotes, e.g. median of 14.5 and 15.5) | NO_VALID_MARKET (no valid book).
CONSENSUS_CONFIDENCE (consensus_books = books at the consensus line, books = books after exclusions):
    NO_MARKET    0 books
    SINGLE_BOOK  1 book
    LOW          2 books, or consensus_books < 3, or consensus_books / books < 0.5
    MEDIUM       consensus_books >= 3 and consensus_books / books >= 0.5
    HIGH         consensus_books >= 4 and consensus_books / books >= 0.75
  then one level down (HIGH -> MEDIUM -> LOW) when the oldest consensus quote was more than CONFIDENCE_MAX_AGE_SECONDS
  old at capture.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CONSENSUS_VERSION = "cbb-prod-market-consensus-1.0.0"
CONSENSUS_LINE_METHOD = "MEDIAN"
CONSENSUS_PROBABILITY_METHOD = "MEDIAN_OF_DEVIGGED_EXACT_LINE_PROBS"
OUTLIER_METHOD = "MEDIAN_MAD_Z3.5_MIN5_BOOKS+ZERO_MAD_CLUSTER+STALE_MOVE_PROTECTION"
MAD_MIN_BOOKS = 5
MAD_SCALE = 1.4826
MAD_Z = 3.5
ZERO_MAD_MIN_CLUSTER = 3
MOVE_WINDOW_SECONDS = 600
MOVE_MIN_BOOKS = 2
CONFIDENCE_MAX_AGE_SECONDS = 3600
SIDES = ("OVER", "UNDER")
ROW_REASON = {"INVALID_QUOTE": "MALFORMED_MARKET", "UNSUPPORTED_MARKET": "UNSUPPORTED_MARKET",
              "UNMATCHED_PLAYER": "IDENTITY_MISMATCH", "AMBIGUOUS_PLAYER": "IDENTITY_MISMATCH",
              "UNMATCHED_GAME": "IDENTITY_MISMATCH", "AMBIGUOUS_GAME": "IDENTITY_MISMATCH",
              "STALE_QUOTE": "STALE_QUOTE", "POST_CUTOFF_QUOTE": "POST_CUTOFF_QUOTE"}
AUDIT_COLS = ["canonical_game_id", "canonical_player_id", "provider_player_name", "stat", "market_key", "sportsbook",
              "line", "side", "american_odds", "decimal_odds", "market_last_update", "collector_received_at",
              "quote_age_seconds", "status", "reason"]


def _devig(d_over: float, d_under: float) -> tuple[float | None, float | None]:
    from cbb_platform.prod import exports as EX                 # the existing production de-vig, reused as is
    return EX.no_vig("OVER", d_over, d_under), EX.no_vig("UNDER", d_under, d_over)


def display_american(p):
    from cbb_platform import props_v1_pricing as PV             # the existing production probability -> odds conversion
    if p is None or not np.isfinite(p) or not 0 < p < 1:
        return None
    return float(PV.decimal_to_american(PV.prob_to_decimal(p)))


def _t(x):
    if x is None or (not isinstance(x, pd.Timestamp) and pd.isna(x)):
        return None
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _age(r) -> float | None:
    a, b = _t(r.get("market_last_update")), _t(r.get("collector_received_at"))
    return None if a is None or b is None else float((b - a).total_seconds())


def row_exclusions(q: pd.DataFrame) -> pd.Series:
    """Per quote row: '' (eligible) or the exclusion reason from the production normalizer / matcher / freshness."""
    def one(r):
        if r.get("quote_status") == "VALID" and r.get("is_alternate") is not True:
            if not isinstance(r.get("canonical_player_id"), str) or not isinstance(r.get("canonical_game_id"), str):
                return "IDENTITY_MISMATCH"
            return ""
        if r.get("is_alternate") is True:
            return "ALTERNATE_MARKET"
        why = str(r.get("suppression_reason") or "")
        return ROW_REASON.get(why, why or "MALFORMED_MARKET")
    return q.apply(one, axis=1) if len(q) else pd.Series(dtype=object)


def _confidence(books: int, cons: int, oldest_age) -> str:
    if books == 0:
        return "NO_MARKET"
    if books == 1:
        return "SINGLE_BOOK"
    share = cons / books if books else 0.0
    if cons >= 4 and share >= 0.75:
        c = "HIGH"
    elif cons >= 3 and share >= 0.5:
        c = "MEDIUM"
    else:
        c = "LOW"
    if oldest_age is not None and oldest_age > CONFIDENCE_MAX_AGE_SECONDS and c in ("HIGH", "MEDIUM"):
        c = {"HIGH": "MEDIUM", "MEDIUM": "LOW"}[c]
    return c


def consensus(q: pd.DataFrame) -> tuple[dict, list[dict]]:
    """q: the observations of ONE game x player x stat (any status). Returns (market fields, audit rows)."""
    audit: list[dict] = []

    def note(r, status, reason=""):
        audit.append({**{c: r.get(c) for c in AUDIT_COLS if c not in ("quote_age_seconds", "status", "reason")},
                      "quote_age_seconds": _age(r), "status": status, "reason": reason})
    rows = q.to_dict(orient="records") if len(q) else []
    why = row_exclusions(q).tolist() if len(q) else []
    ok = []
    for r, w in zip(rows, why):
        (note(r, "EXCLUDED", w) if w else ok.append(r))
    # 2. duplicates: (book, line, side) -> latest update kept
    ok.sort(key=lambda r: (r["sportsbook"], float(r["line"]), r["side"], _t(r.get("market_last_update")) or pd.Timestamp.min.tz_localize("UTC"),
                           float(r["decimal_odds"])))
    dedup: dict = {}
    for r in ok:
        k = (r["sportsbook"], float(r["line"]), r["side"])
        if k in dedup:
            note(dedup[k], "EXCLUDED", "DUPLICATE_BOOK_OBSERVATION")
        dedup[k] = r
    # 3. pairing
    pairs: dict = {}
    for (book, line, side), r in sorted(dedup.items()):
        pairs.setdefault((book, line), {})[side] = r
    paired = {}
    for (book, line), sd in sorted(pairs.items()):
        if set(sd) != set(SIDES):
            for r in sd.values():
                note(r, "EXCLUDED", "UNPAIRED_PRICE")
            continue
        fo, fu = _devig(float(sd["OVER"]["decimal_odds"]), float(sd["UNDER"]["decimal_odds"]))
        if fo is None or fu is None:
            for r in sd.values():
                note(r, "EXCLUDED", "MALFORMED_MARKET")
            continue
        paired.setdefault(book, []).append({"book": book, "line": line, "over": sd["OVER"], "under": sd["UNDER"],
                                            "fair_over": fo, "fair_under": fu,
                                            "upd": max(_t(sd["OVER"].get("market_last_update")),
                                                       _t(sd["UNDER"].get("market_last_update")))})
    # 4. one primary line per book
    books = []
    for book, cands in sorted(paired.items()):
        cands.sort(key=lambda c: (abs(c["fair_over"] - 0.5), c["line"]))
        books.append(cands[0])
        for c in cands[1:]:
            note(c["over"], "EXCLUDED", "NON_PRIMARY_LINE")
            note(c["under"], "EXCLUDED", "NON_PRIMARY_LINE")

    def drop(sel, reason):
        nonlocal books
        for b in sel:
            note(b["over"], "EXCLUDED", reason)
            note(b["under"], "EXCLUDED", reason)
        ids = {id(b) for b in sel}
        books = [b for b in books if id(b) not in ids]
    # 5. stale-move protection
    if len(books) >= MOVE_MIN_BOOKS + 1:
        newest = max(b["upd"] for b in books)
        recent = [b for b in books if (newest - b["upd"]).total_seconds() <= MOVE_WINDOW_SECONDS]
        older = [b for b in books if b not in recent]
        if len(recent) >= MOVE_MIN_BOOKS and older and len({b["line"] for b in recent}) == 1 \
                and min(b["upd"] for b in recent) > max(b["upd"] for b in older):
            new_line = recent[0]["line"]
            if float(np.median([b["line"] for b in older])) != new_line:
                drop([b for b in older if b["line"] != new_line], "SUPERSEDED_QUOTE")
    # 6. outliers (>= 5 books only)
    outliers = 0
    if len(books) >= MAD_MIN_BOOKS:
        lines = np.array([b["line"] for b in books])
        med = float(np.median(lines))
        mad = float(np.median(np.abs(lines - med)))
        if mad > 0:
            sel = [b for b in books if abs(b["line"] - med) / (MAD_SCALE * mad) > MAD_Z]
        else:
            cluster = [b for b in books if b["line"] == med]
            other = [b for b in books if b["line"] != med]
            sel = []
            if len(cluster) >= ZERO_MAD_MIN_CLUSTER and other:
                newer_move = len(other) >= 2 and len({b["line"] for b in other}) == 1 and \
                    min(b["upd"] for b in other) > max(b["upd"] for b in cluster)
                sel = [] if newer_move else other
        outliers = len(sel)
        drop(sel, "LINE_OUTLIER")
    out = {"consensus_version": CONSENSUS_VERSION, "consensus_line": None, "market_over_prob": None,
           "market_under_prob": None, "consensus_over_odds": None, "consensus_under_odds": None,
           "raw_book_count": len(books), "consensus_book_count": 0, "outlier_book_count": outliers,
           "market_status": "NO_VALID_MARKET", "consensus_confidence": "NO_MARKET", "quote_age_seconds": None,
           "quote_captured_utc": None, "consensus_books": [], "best_over": None, "best_under": None}
    if not books:
        return out, audit
    line = float(np.median([b["line"] for b in books]))
    at = [b for b in books if b["line"] == line]
    for b in books:
        st = "INCLUDED" if b["line"] == line else "INCLUDED_OTHER_LINE"
        note(b["over"], st)
        note(b["under"], st)
    out.update(consensus_line=line, consensus_book_count=len(at),
               quote_captured_utc=max(_t(b["over"].get("collector_received_at")) for b in books).isoformat())
    if not at:
        out.update(market_status="NO_EXACT_LINE_PRICE", consensus_confidence="LOW" if len(books) > 1 else "SINGLE_BOOK")
        return out, audit
    fo = float(np.median([b["fair_over"] for b in at]))
    fu = float(np.median([b["fair_under"] for b in at]))
    ages = [a for b in at for a in (_age(b["over"]), _age(b["under"])) if a is not None]
    oldest = max(ages) if ages else None

    def best(side):
        """The best executable price at the consensus line (highest decimal; ties: book name ascending)."""
        k = "over" if side == "OVER" else "under"
        top = max(float(x[k]["decimal_odds"]) for x in at)
        b = sorted([x for x in at if float(x[k]["decimal_odds"]) == top], key=lambda x: x["book"])[0]
        return {"book": b["book"], "decimal": float(b[k]["decimal_odds"]), "american": b[k].get("american_odds")}
    out.update(market_over_prob=fo, market_under_prob=fu, consensus_over_odds=display_american(fo),
               consensus_under_odds=display_american(fu), market_status="VALID_MARKET",
               consensus_confidence=_confidence(len(books), len(at), oldest), quote_age_seconds=oldest,
               consensus_books=sorted(b["book"] for b in at), best_over=best("OVER"), best_under=best("UNDER"))
    return out, audit
