"""Production game forecast (Release B, SHADOW): one GameJointPMF per forecast snapshot x game.

Called by prod.lifecycle.forecast after the player PMFs are written, for exactly the snapshot's games, with the SAME
`now`, state version and training cutoff. Never refits the player model; never sums player PMFs; the player layer
enters only through the availability term (model.AdjustParams.gamma x availability_delta; beta = 0 by walk-forward
selection). availability_delta (Stage 05, 2026-11-27) = S over the player layer's SCORED set minus S over EVERY candidate
at the model's own p_play in the SAME snapshot (the news-free expectation): the delta is caused by availability only.
Before, the baseline was the day's INITIAL snapshot. INITIAL never carries RotoWire availability (it runs before the
first refresh), so since the 2026-11-26 S fix the two agree on availability; the INITIAL baseline additionally
attributed every non-availability change of the model inputs between INITIAL and a later snapshot (candidate set,
inputs) to availability, and gave delta 0 to a game with no earlier snapshot that day (morning recovery / late slate).
Replay 11-20/23/26/27: 1 game changed, |dP(home)| 0.0015. CBB_GAME_AVAILABILITY_BASELINE=INITIAL restores the old
baseline (in-place fallback; only together with CBB_INITIAL_AVAILABILITY=OFF, see availability_baseline).

Ratings: params/ratings_prior_<season>.parquet (season-final chain 2014..season-1, sealed in game_model.json) + every
promoted-state DI_VS_DI final knowable strictly before refit_cutoff(D) (all of D-1), fit in milliseconds per snapshot
(deterministic; identical for every snapshot of the day, so no intraday refit of anything trained).
Output: <snapshot>/game_pmfs.parquet + game_forecast.json. A failure here never touches the player outputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import zlib
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cbb_platform.game import joint as J, markets as GM, model as GMod, params as GP, ratings as GR
from cbb_platform.prod import engine as ENG

FILE = "game_pmfs.parquet"
RECEIPT = "game_forecast.json"
FORECAST_VERSION = "cbb-game-forecast-1.0.0"
MODE_ENV = "CBB_GAME_MODEL"            # SHADOW (default in Release B units) | OFF


def mode() -> str:
    return (os.environ.get(MODE_ENV) or "OFF").upper()


# ============================================================================ ratings inputs
def prior(season: int, root: str | None = None) -> GR.Ratings:
    gm = GP.game_model(root)
    rp = gm.get("ratings_prior", {}).get(str(season))
    if rp is None:
        raise GP.ParamsIntegrityError(f"no sealed ratings prior for season {season}")
    p = (Path(root) if root else GP.ROOT) / rp["file"]
    if hashlib.sha256(p.read_bytes()).hexdigest() != rp["sha256"]:
        raise GP.ParamsIntegrityError(f"{p.name}: sha256 mismatch")
    df = pd.read_parquet(p)
    lg = rp["league"]
    r = GR.Ratings(df["team_id"].tolist(), df["off"].to_numpy(float), df["def"].to_numpy(float),
                   df["pace"].to_numpy(float), float(lg["mu"]), float(lg["hca"]), float(lg["pi"]),
                   {"state_id": rp["state_id"], "prior_of_season": season - 1})
    return r


def season_games(increment_db: str | Path, season: int) -> pd.DataFrame:
    """The promoted state's completed DI_VS_DI games of `season` in ratings.games_frame shape (OT from the team box)."""
    con = duckdb.connect(str(increment_db), read_only=True)
    try:
        con.execute("SET TimeZone = 'UTC'")
        df = con.execute(f"""
            select g.canonical_game_id game_id, g.season, g.game_date_et, g.scheduled_tip_utc tip_utc,
                   g.home_team_id home, g.away_team_id away, coalesce(g.neutral_site, false) neutral, g.conference_game,
                   g.home_points home_pts, g.away_points away_pts, coalesce(h.overtime_count, 0) ot,
                   g.home_conference_id home_conf, g.away_conference_id away_conf,
                   h.fga hfga, h.orb horb, h.tov_total htov, h.fta hfta, a.fga afga, a.orb aorb, a.tov_total atov,
                   a.fta afta, greatest(h.historical_knowable_at, a.historical_knowable_at) knowable_at
            from flagship.games g
            join flagship.team_game_outcomes h on h.canonical_game_id = g.canonical_game_id and h.canonical_team_id = g.home_team_id
            join flagship.team_game_outcomes a on a.canonical_game_id = g.canonical_game_id and a.canonical_team_id = g.away_team_id
            where g.season = {int(season)} and g.game_universe = 'DI_VS_DI'
              and g.home_points is not null and g.away_points is not null
            order by g.scheduled_tip_utc, g.canonical_game_id""").df()
    finally:
        con.close()
    return GR.frame_from_box(df)


def neutral_of(increment_db, ids) -> dict:
    con = duckdb.connect(str(increment_db), read_only=True)
    try:
        df = con.execute("select canonical_game_id, coalesce(neutral_site, false) n from flagship.games "
                         "where canonical_game_id in (select unnest(?))", [list(ids)]).df()
    finally:
        con.close()
    return dict(zip(df["canonical_game_id"], df["n"].astype(bool)))


def games_played(games: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    g = games[games["knowable_at"] < cutoff]
    return pd.concat([g["home"], g["away"]]).value_counts().to_dict()


# ============================================================================ the player-layer availability term
BASELINE_ENV = "CBB_GAME_AVAILABILITY_BASELINE"      # NEWS_FREE (default) | INITIAL (pre-Stage-05 fallback)


def availability_baseline() -> str:
    """INITIAL is valid only with a news-free INITIAL snapshot (CBB_INITIAL_AVAILABILITY=OFF, prod.lifecycle): once INITIAL
    consumes availability (Stage 05 closure default) its S already excludes the morning's known absences, and the INITIAL
    baseline would silently drop them from the game mean. Requested without it -> NEWS_FREE."""
    v = (os.environ.get(BASELINE_ENV) or "NEWS_FREE").upper()
    initial_news_free = (os.environ.get("CBB_INITIAL_AVAILABILITY") or "ON").upper() == "OFF"
    return "INITIAL" if v == "INITIAL" and initial_news_free else "NEWS_FREE"


def team_strength(model_inputs: pd.DataFrame | None, news_free: bool = False) -> dict:
    """{(game_id, team_id): sum_k p_play_k * PTS_mu_k} over the player layer's SCORED set -- the model's own expected
    points (a signal, not a team score). prod.inputs.apply_availability keeps a confirmed-unavailable player as a row
    flagged player_suppression (it is never simulated); 2026-11-26 fix: such a row no longer counts toward S (before,
    a confirmed OUT never moved the game layer in production). news_free=True keeps every candidate (the same
    snapshot's expectation without any availability news): the Stage 05 availability baseline."""
    if model_inputs is None or not len(model_inputs) or not {"p_play", "PTS_mu"} <= set(model_inputs.columns):
        return {}
    m = model_inputs.drop_duplicates(["game_id", "player_id"])
    if "player_suppression" in m and not news_free:     # news_free: every candidate at the model's own p_play
        m = m[m["player_suppression"].fillna("").astype(str) == ""]
    s = (m["p_play"].astype(float).fillna(0) * m["PTS_mu"].astype(float).fillna(0)).groupby(
        [m["game_id"], m["team_id"]]).sum()
    return {k: float(v) for k, v in s.items()}


def initial_strength(day_dir: Path, game_id: str) -> tuple[dict, str | None]:
    """The earliest prior snapshot of the day that forecast `game_id`: ({team: S}, snapshot id)."""
    best = None
    for p in sorted(Path(day_dir).glob(f"*/{FILE}")):
        try:
            g = pd.read_parquet(p, columns=["game_id", "snapshot_id", "prediction_as_of", "home_team_id", "away_team_id",
                                            "S_home", "S_away"])
        except Exception:                                                  # noqa: BLE001 - an unreadable file is skipped
            continue
        g = g[g["game_id"] == game_id]
        if len(g) and (best is None or g["prediction_as_of"].iloc[0] < best["prediction_as_of"]):
            best = g.iloc[0].to_dict()
    if best is None:
        return {}, None
    return {best["home_team_id"]: best["S_home"], best["away_team_id"]: best["S_away"]}, best["snapshot_id"]


# ============================================================================ compact PMF encoding
STORE_FLOOR = 1e-10            # cells below are zero in the canonical (stored) PMF; dropped mass ~1e-6


def _from32(p32: np.ndarray, h_min: int, a_min: int, meta: dict) -> J.GameJointPMF:
    p = p32.astype(np.float64)
    return J.GameJointPMF(int(h_min), int(a_min), p / p.sum(), meta)


def canonical(g: J.GameJointPMF) -> J.GameJointPMF:
    """The stored PMF: floor STORE_FLOOR, crop to the live box, float32, renormalised in float64. Every market,
    coherence check and sha256 of a snapshot is computed on THIS matrix, so a decode reproduces it bit for bit."""
    p = np.where(g.p >= STORE_FLOOR, g.p, 0.0)
    rows, cols = np.flatnonzero(p.sum(1)), np.flatnonzero(p.sum(0))
    p = p[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    p32 = np.ascontiguousarray(p, dtype=np.float32)
    out = _from32(p32, g.h_min + rows[0], g.a_min + cols[0], {**g.meta, "store_floor": STORE_FLOOR})
    out.stored32 = p32                                   # the exact bytes encode() persists
    return out


def encode(g: J.GameJointPMF) -> bytes:
    return zlib.compress(g.stored32.tobytes(), 9)


def decode(blob: bytes, h_min: int, a_min: int, shape: tuple, meta: dict | None = None) -> J.GameJointPMF:
    return _from32(np.frombuffer(zlib.decompress(blob), dtype=np.float32).reshape(shape), h_min, a_min, meta or {})


# ============================================================================ the snapshot step
def forecast(sdir: Path, snapshot: dict, games: pd.DataFrame, increment_db, training_cutoff: pd.Timestamp,
             model_inputs: pd.DataFrame | None, season: int, params_root: str | None = None) -> dict:
    """Write <sdir>/game_pmfs.parquet for `games` (the snapshot's slate rows: canonical_game_id, home_team_id,
    away_team_id, scheduled_tip_utc, cutoff, neutral_site?). Returns the receipt (also written as game_forecast.json)."""
    t0 = time.monotonic()
    sdir = Path(sdir)
    gm = GP.game_model(params_root)
    eot = GP.endgame_ot(params_root)
    v = GMod.VarianceParams(**gm["variance"])
    adj = GMod.AdjustParams(**gm["adjust"])
    rp = GR.RatingParams(**{k: gm["ratings"][k] for k in ("tau_days", "lam_off", "lam_def", "lam_pace", "rho_carry",
                                                          "lam_league") + tuple(k for k in ("carry_level", "carry_pace")
                                                                                if k in gm["ratings"])})
    # (Stage 06) carry_level / carry_pace act only where the sealed prior was built; passing them makes the persisted
    # ratings meta record the sealed values instead of the RatingParams default 1.0. The fit is unchanged.
    hist = season_games(increment_db, season)
    pri = prior(season, params_root)
    d1 = (pd.Timestamp(training_cutoff).tz_convert("America/New_York") - pd.Timedelta(days=1)).date()
    r, rstat = daily_ratings(sdir.parent, hist, training_cutoff, pri, rp, pending_finals(increment_db, d1))
    gp = games_played(hist[hist["game_id"].isin(set(rstat["game_ids"]))], training_cutoff)
    S = team_strength(model_inputs)
    baseline = availability_baseline()
    S0 = team_strength(model_inputs, news_free=True) if baseline == "NEWS_FREE" else None
    day_dir = sdir.parent
    if "neutral_site" not in games:
        games = games.assign(neutral_site=games["canonical_game_id"].map(neutral_of(increment_db, games["canonical_game_id"])))
    neutral = games["neutral_site"].astype("boolean").fillna(False).astype(bool).to_numpy()
    pr = r.predict(games["home_team_id"].to_numpy(), games["away_team_id"].to_numpy(), neutral)
    rows, bad = [], []
    for i, gr in enumerate(games.to_dict(orient="records")):
        gid, h, a = gr["canonical_game_id"], gr["home_team_id"], gr["away_team_id"]
        s_h, s_a = S.get((gid, h)), S.get((gid, a))
        if S0 is not None:                                # same-snapshot news-free expectation
            base, base_sid = {h: S0.get((gid, h)), a: S0.get((gid, a))}, f"NEWS_FREE:{snapshot['snapshot_id']}"
        else:
            base, base_sid = initial_strength(day_dir, gid)
        dh = 0.0 if s_h is None or h not in base or base[h] is None else s_h - float(base[h])
        da = 0.0 if s_a is None or a not in base or base[a] is None else s_a - float(base[a])
        n = min(gp.get(h, 0), gp.get(a, 0))
        t = GMod.targets(float(pr["exp_home"].iloc[i]), float(pr["exp_away"].iloc[i]), n, v, adj, dh, da)
        try:
            g0 = GMod.build(t, eot)
            g = canonical(g0)
            coh = GM.coherence(g)
        except Exception as e:                                             # noqa: BLE001 - recorded per game
            bad.append({"game_id": gid, "reason": f"{type(e).__name__}: {e}"})
            continue
        if not coh["COHERENT"]:
            bad.append({"game_id": gid, "reason": "INCOHERENT", "coherence": coh})
            continue
        sm = GM.summary(g)
        rows.append({
            "forecast_id": snapshot["snapshot_id"], "snapshot_id": snapshot["snapshot_id"], "game_id": gid,
            "window": snapshot.get("window"), "slate_date": snapshot.get("slate_date"),
            "prediction_as_of": snapshot.get("prediction_as_of") or snapshot.get("executed_at"),
            "home_team_id": h, "away_team_id": a, "neutral_site": bool(neutral[i]),
            "scheduled_tip_utc": pd.Timestamp(gr["scheduled_tip_utc"]).isoformat(),
            "cutoff_utc": pd.Timestamp(gr["cutoff"]).isoformat() if gr.get("cutoff") is not None else None,
            "player_run_id": snapshot.get("run_id"), "player_model_version": snapshot.get("model_version"),
            "player_engine_id": ENG.attribute(snapshot.get("engine_id"),
                                              snapshot.get("prediction_as_of") or snapshot.get("executed_at")),
            "state_version": snapshot.get("state_version"),
            "training_cutoff_utc": pd.Timestamp(training_cutoff).isoformat(),
            "ratings_state_id": r.meta["state_id"], "ratings_games": r.meta["games"],
            "ratings_max_game_date": r.meta["max_game_date"], "ratings_prior_id": pri.meta["state_id"],
            "home_known": bool(pr["home_known"].iloc[i]), "away_known": bool(pr["away_known"].iloc[i]),
            "games_played_min": int(n), "top_down_home": float(pr["exp_home"].iloc[i]),
            "top_down_away": float(pr["exp_away"].iloc[i]), "expected_possessions": float(pr["poss"].iloc[i]),
            "S_home": s_h, "S_away": s_a, "availability_delta_home": dh, "availability_delta_away": da,
            "availability_base_snapshot": base_sid,
            "target_exp_home": t.exp_home, "target_exp_away": t.exp_away, "target_sd_margin": t.sd_margin,
            "target_sd_total": t.sd_total, "target_rho_mt": t.rho_mt,
            **{k.lower(): float(vv) for k, vv in sm.items()},
            "pmf_sha256": g.sha256(), "h_min": int(g.h_min), "a_min": int(g.a_min), "rows": int(g.p.shape[0]),
            "cols": int(g.p.shape[1]), "pmf_zlib": encode(g), "tie_mass": float(g.tie_mass()),
            "pmf_sum": float(g.p.sum()), "coherent": True,
            "joint_version": J.joint_version(), "mean_basis": J.mean_basis(), "model_version": gm["model_version"],   # the sealed params' version
            "params_sha256": gm["params_sha256"], "endgame_ot_sha256": eot.sha256,
            "endgame_ot_manifest_sha256": eot.manifest_sha256,         # Stage 06: OT / pace-edge values are lineage too
            "moment_match_error": float(g0.meta.get("match_max_abs_err", 0.0)),
            "match_iterations": int(g0.meta.get("match_iterations", 0)), "status": "FORECAST"})
    out = pd.DataFrame(rows)
    sdir.mkdir(parents=True, exist_ok=True)
    if len(out):
        out.to_parquet(sdir / FILE, index=False)
    rec = {"version": FORECAST_VERSION, "mode": mode(), "snapshot_id": snapshot["snapshot_id"],
           "games_due": int(len(games)), "game_pmfs": int(len(out)), "failed": bad,
           "status": "COMPLETE" if len(out) == len(games) else ("PARTIAL" if len(out) else "FAILED"),
           "ratings": {k: r.meta[k] for k in ("state_id", "games", "max_game_date", "max_knowable_at", "cutoff_utc")},
           "ratings_fresh": rstat["fresh"], "ratings_source": rstat["source"], "params_sha256": gm["params_sha256"],
           "availability_baseline": baseline,
           "elapsed_s": round(time.monotonic() - t0, 2),
           "file_sha256": hashlib.sha256((sdir / FILE).read_bytes()).hexdigest() if len(out) else None}
    (sdir / RECEIPT).write_text(json.dumps(rec, indent=1, default=str))
    return rec


RATINGS_DIR = "game_ratings"


def daily_ratings(day_dir: Path, hist: pd.DataFrame, cutoff: pd.Timestamp, pri: GR.Ratings, rp: GR.RatingParams,
                  pending_d1: int = 0):
    """The ONE team-ratings state of ET day D, fit at D's training cutoff and persisted
    (<day_dir>/game_ratings/<state_id>.parquet + index.jsonl). Every later snapshot of D reuses the latest persisted
    FRESH state (no intraday refit). A rebuild happens only when no fresh state exists yet (recovery: a D-1 final
    that reached the promoted state after the first snapshot) -- the same rule as the player REFIT_RETRY."""
    rd = Path(day_dir) / RATINGS_DIR
    idx = rd / "index.jsonl"
    rows = [json.loads(l) for l in idx.read_text().splitlines() if l.strip()] if idx.exists() else []
    fresh = [x for x in rows if x["fresh"]["TEAM_RATINGS_FRESH"] and x["cutoff_utc"] == pd.Timestamp(cutoff).isoformat()]
    # Stage 06: a persisted state is reused only if it was fit under the SAME prior and the same fit parameters; a
    # release that changes the prior or the rating params mid-day rebuilds instead of carrying the old fit forward
    # under the new params_sha256 / prior id.
    want_prior = pri.meta.get("state_id") or pri.meta.get("prior_of") if pri is not None else None
    fit_keys = ("tau_days", "lam_off", "lam_def", "lam_pace", "lam_league")
    same = [x for x in fresh if x["meta"].get("prior") == want_prior
            and all(x["meta"].get("params", {}).get(k) == getattr(rp, k) for k in fit_keys)]
    stale_fit = bool(fresh) and not same
    fresh = same
    if fresh:
        x = fresh[-1]
        df = pd.read_parquet(rd / f"{x['state_id']}.parquet")
        r = GR.Ratings(df["team_id"].tolist(), df["off"].to_numpy(float), df["def"].to_numpy(float),
                       df["pace"].to_numpy(float), x["mu"], x["hca"], x["pi"], dict(x["meta"]))
        if r.state_id() != x["state_id"]:
            raise GP.ParamsIntegrityError(f"daily ratings {x['state_id']}: content hash mismatch")
        return r, {"fresh": x["fresh"], "source": "REUSED_DAILY_STATE", "game_ids": x["game_ids"]}
    r = GR.fit(hist, cutoff, pri, rp)
    fr = ratings_fresh(hist, r, cutoff, pending_d1)
    used = sorted(hist.loc[hist["knowable_at"] < pd.Timestamp(cutoff), "game_id"])
    rd.mkdir(parents=True, exist_ok=True)
    r.to_frame().to_parquet(rd / f"{r.meta['state_id']}.parquet", index=False)
    rec = {"state_id": r.meta["state_id"], "cutoff_utc": pd.Timestamp(cutoff).isoformat(), "mu": r.mu, "hca": r.hca,
           "pi": r.pi, "meta": r.meta, "fresh": fr, "game_ids": used,
           "rebuild_of": rows[-1]["state_id"] if rows else None}
    with idx.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    src = "REBUILT_PRIOR_OR_PARAMS_CHANGED" if stale_fit else "BUILT" if not rows else "REBUILT_RECOVERY_NOT_FRESH"
    return r, {"fresh": fr, "source": src, "game_ids": used}


def ratings_fresh(hist: pd.DataFrame, r: GR.Ratings, cutoff: pd.Timestamp, pending_d1: int = 0) -> dict:
    """TEAM_RATINGS_FRESH: every D-1 (ET) DI_VS_DI final in the state is inside the fit AND no D-1 game is still
    awaiting its final in the schedule (`pending_d1`: status not final / postponed / cancelled, no score)."""
    d1 = (pd.Timestamp(cutoff).tz_convert("America/New_York") - pd.Timedelta(days=1)).date()
    finals = hist[pd.to_datetime(hist["game_date_et"]).dt.date == d1]
    used = finals[finals["knowable_at"] < cutoff]
    return {"d_minus_1": d1.isoformat(), "d_minus_1_finals_in_state": int(len(finals)),
            "d_minus_1_finals_in_ratings": int(len(used)), "d_minus_1_awaiting_final": int(pending_d1),
            "max_game_date": r.meta["max_game_date"],
            "TEAM_RATINGS_FRESH": bool(len(finals) == len(used) and pending_d1 == 0
                                       and (len(finals) == 0 or r.meta["max_game_date"] == d1.isoformat()))}


def pending_finals(increment_db, d1) -> int:
    """D-1 DI_VS_DI games the schedule still expects a result for (not final, not postponed / cancelled)."""
    con = duckdb.connect(str(increment_db), read_only=True)
    try:
        has = con.execute("select count(*) from duckdb_tables() where schema_name='metadata' "
                          "and table_name='prod_schedule'").fetchone()[0]
        if not has:
            return 0
        return int(con.execute("""select count(*) from flagship.games g join metadata.prod_schedule s
                                  using (canonical_game_id) where g.game_date_et = ? and g.game_universe = 'DI_VS_DI'
                                  and g.home_points is null
                                  and lower(coalesce(s.status, '')) not in ('final', 'postponed', 'cancelled',
                                                                            'canceled')""", [d1]).fetchone()[0])
    finally:
        con.close()


def read(sdir: Path) -> pd.DataFrame:
    p = Path(sdir) / FILE
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def pmf_of(row: dict) -> J.GameJointPMF:
    g = decode(row["pmf_zlib"], row["h_min"], row["a_min"], (int(row["rows"]), int(row["cols"])))
    if g.sha256() != row["pmf_sha256"]:
        raise GP.ParamsIntegrityError(f"game_pmfs {row['game_id']}: PMF sha256 mismatch")
    return g
