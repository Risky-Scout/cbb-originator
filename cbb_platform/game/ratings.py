"""Point-in-time opponent-adjusted ridge team ratings (offense, defense, pace, home court) -- the top-down game prior.

Per team-game (team i on offense vs j):   100 * pts_i / poss_g = mu + O_i + Dn_j + h * loc_i + e      (weight poss/70 * decay)
Per game:                                  poss_g             = pi + P_home + P_away + e             (weight decay)
loc = +1 home, -1 away, 0 neutral; Dn_j > 0 = a defence that allows more. decay = exp(-age_days / tau).
Ridge toward the prior theta0: O, D, P toward rho * (previous season final), the league terms (mu, h, pi) toward the
previous season's values with a tiny penalty. Fit ONLY on games knowable before the cutoff (historical_knowable_at <
cutoff): a refit at D's training cutoff uses every eligible completed game through D-1 and nothing later.

Prediction:  poss = pi + P_h + P_a;  E[home pts] = poss * (mu + O_h + Dn_a + h * loc_h) / 100   (away symmetric).
Pure numpy; deterministic; a ~1,100-parameter dense solve per refit (milliseconds).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

RATINGS_VERSION = "cbb-game-ratings-1.0.0"
POSS_FTA = 0.475


@dataclass(frozen=True)
class RatingParams:
    tau_days: float = 60.0
    lam_off: float = 12.0
    lam_def: float = 12.0
    lam_pace: float = 8.0
    rho_carry: float = 0.7          # prior-season carryover shrink for O, D, P
    lam_league: float = 1.0         # weak anchor of mu / h / pi to the previous season
    carry_level: float = 1.0        # GC3 (2026-11-25): extra carryover of the scoring level l = off + def (total only)
    carry_pace: float = 1.0         # GC3 (2026-11-25): extra carryover of pace; strength s = off - def keeps rho_carry

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Ratings:
    teams: list
    off: np.ndarray
    dfn: np.ndarray
    pace: np.ndarray
    mu: float
    hca: float
    pi: float
    meta: dict = field(default_factory=dict)

    def index(self) -> dict:
        return {t: i for i, t in enumerate(self.teams)}

    def prior_for(self, rho: float, level: float = 1.0, pace: float = 1.0) -> "Ratings":
        """The next season's prior: team terms shrunk by rho toward the league mean (0), league terms carried.
        GC3 component carryover: margin strength s = off - def keeps rho; the scoring level l = off + def is carried by
        rho * level and pace by rho * pace (margin depends on s only: eff_h - eff_a = s_h - s_a + 2 h)."""
        if level == 1.0 and pace == 1.0:                         # the original (bit-identical) carryover
            return Ratings(list(self.teams), rho * self.off, rho * self.dfn, rho * self.pace, self.mu, self.hca, self.pi,
                           {"prior_of": self.meta.get("state_id"), "rho": rho})
        s, l = rho * (self.off - self.dfn), rho * level * (self.off + self.dfn)
        return Ratings(list(self.teams), (s + l) / 2, (l - s) / 2, rho * pace * self.pace, self.mu, self.hca, self.pi,
                       {"prior_of": self.meta.get("state_id"), "rho": rho, "carry_level": level, "carry_pace": pace})

    def predict(self, home, away, neutral) -> pd.DataFrame:
        """Expected possessions / home / away points for aligned arrays of team ids (unknown team -> league mean)."""
        ix = self.index()
        hi = np.array([ix.get(t, -1) for t in home])
        ai = np.array([ix.get(t, -1) for t in away])
        g = lambda v, i: np.where(i >= 0, v[np.clip(i, 0, None)], 0.0)            # noqa: E731
        loc = np.where(np.asarray(neutral, dtype=bool), 0.0, 1.0)
        poss = self.pi + g(self.pace, hi) + g(self.pace, ai)
        eff_h = self.mu + g(self.off, hi) + g(self.dfn, ai) + self.hca * loc
        eff_a = self.mu + g(self.off, ai) + g(self.dfn, hi) - self.hca * loc
        return pd.DataFrame({"poss": poss, "exp_home": poss * eff_h / 100.0, "exp_away": poss * eff_a / 100.0,
                             "home_known": hi >= 0, "away_known": ai >= 0})

    def state_id(self) -> str:
        h = hashlib.sha256()
        h.update(json.dumps(self.teams).encode())
        for a in (self.off, self.dfn, self.pace, np.array([self.mu, self.hca, self.pi])):
            h.update(np.ascontiguousarray(np.round(a, 10), dtype=np.float64).tobytes())
        return "TR-" + h.hexdigest()[:16]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"team_id": self.teams, "off": self.off, "def": self.dfn, "pace": self.pace})


def possessions(tg: pd.DataFrame) -> pd.Series:
    """Team possessions estimate fga - orb + tov + 0.475 fta (stage3/team_state convention)."""
    return tg["fga"] - tg["orb"] + tg["tov_total"] + POSS_FTA * tg["fta"]


def games_frame(con, seasons: tuple[int, int] | None = None, universe: str = "DI_VS_DI") -> pd.DataFrame:
    """One row per completed game with a two-team box: ids, date, knowable time, location, points, possessions."""
    w = f"and g.season between {seasons[0]} and {seasons[1]}" if seasons else ""
    df = con.execute(f"""
        select g.canonical_game_id game_id, g.season, g.game_date_et, g.scheduled_tip_utc tip_utc,
               g.home_team_id home, g.away_team_id away, coalesce(g.neutral_site, false) neutral,
               g.conference_game, g.home_points home_pts, g.away_points away_pts, g.overtime_count ot,
               g.home_conference_id home_conf, g.away_conference_id away_conf,
               h.fga hfga, h.orb horb, h.tov_total htov, h.fta hfta, a.fga afga, a.orb aorb, a.tov_total atov,
               a.fta afta, greatest(h.historical_knowable_at, a.historical_knowable_at) knowable_at
        from flagship.played_games g
        join flagship.team_game_outcomes h on h.canonical_game_id = g.canonical_game_id and h.canonical_team_id = g.home_team_id
        join flagship.team_game_outcomes a on a.canonical_game_id = g.canonical_game_id and a.canonical_team_id = g.away_team_id
        where g.game_universe = '{universe}' and g.home_points is not null and g.away_points is not null {w}
        order by g.scheduled_tip_utc, g.canonical_game_id""").df()
    return frame_from_box(df)


def frame_from_box(df: pd.DataFrame) -> pd.DataFrame:
    """Shared by research (games_frame) and production (game.forecast.season_games): per-40 possessions, typed times,
    and the sanity filter (45 <= poss <= 110, both scores > 0)."""
    df = df.copy()
    ph = df["hfga"] - df["horb"] + df["htov"] + POSS_FTA * df["hfta"]
    pa = df["afga"] - df["aorb"] + df["atov"] + POSS_FTA * df["afta"]
    mins = 40.0 + 5.0 * df["ot"].fillna(0)
    df["poss"] = (ph + pa) / 2.0 * 40.0 / mins                               # per-40 possessions (OT normalised)
    df["poss_game"] = (ph + pa) / 2.0
    df["game_date_et"] = pd.to_datetime(df["game_date_et"])
    df["knowable_at"] = pd.to_datetime(df["knowable_at"], utc=True)
    df["tip_utc"] = pd.to_datetime(df["tip_utc"], utc=True)
    ok = df["poss"].between(45, 110) & (df["home_pts"] > 0) & (df["away_pts"] > 0)
    return df[ok].drop(columns=["hfga", "horb", "htov", "hfta", "afga", "aorb", "atov", "afta"]).reset_index(drop=True)


def fit(games: pd.DataFrame, cutoff, prior: Ratings | None, p: RatingParams, teams: list | None = None) -> Ratings:
    """Ridge fit on games knowable strictly before `cutoff`. `prior` None -> league-neutral prior (mu=100, pi=68)."""
    cutoff = pd.Timestamp(cutoff)
    g = games[games["knowable_at"] < cutoff]
    tset = sorted(set(teams or []) | set(g["home"]) | set(g["away"]) | (set(prior.teams) if prior else set()))
    ix = {t: i for i, t in enumerate(tset)}
    n = len(tset)
    pr_ix = prior.index() if prior else {}
    o0 = np.array([prior.off[pr_ix[t]] if t in pr_ix else 0.0 for t in tset]) if prior else np.zeros(n)
    d0 = np.array([prior.dfn[pr_ix[t]] if t in pr_ix else 0.0 for t in tset]) if prior else np.zeros(n)
    p0 = np.array([prior.pace[pr_ix[t]] if t in pr_ix else 0.0 for t in tset]) if prior else np.zeros(n)
    mu0, h0, pi0 = (prior.mu, prior.hca, prior.pi) if prior else (100.0, 3.0, 68.0)
    age = ((cutoff - g["knowable_at"]).dt.total_seconds() / 86400.0).to_numpy()
    dec = np.exp(-age / p.tau_days) if np.isfinite(p.tau_days) else np.ones(len(g))
    hi = g["home"].map(ix).to_numpy()
    ai = g["away"].map(ix).to_numpy()
    loc = np.where(g["neutral"].to_numpy(bool), 0.0, 1.0)
    poss = g["poss"].to_numpy(float)
    # ---- efficiency: theta = [O(n), D(n), mu, h]
    m = 2 * n + 2
    A = np.zeros((m, m))
    b = np.zeros(m)
    for off_i, def_i, pts, sgn in ((hi, ai, g["home_pts"].to_numpy(float), 1.0), (ai, hi, g["away_pts"].to_numpy(float), -1.0)):
        y = 100.0 * pts / g["poss_game"].to_numpy(float)
        w = dec * poss / 70.0
        cols = np.stack([off_i, n + def_i, np.full(len(g), 2 * n), np.full(len(g), 2 * n + 1)], axis=1)
        vals = np.stack([np.ones(len(g)), np.ones(len(g)), np.ones(len(g)), sgn * loc], axis=1)
        for a in range(4):
            for c in range(4):
                np.add.at(A, (cols[:, a], cols[:, c]), w * vals[:, a] * vals[:, c])
            np.add.at(b, cols[:, a], w * vals[:, a] * y)
    lam = np.concatenate([np.full(n, p.lam_off), np.full(n, p.lam_def), [p.lam_league, p.lam_league]])
    th0 = np.concatenate([o0, d0, [mu0, h0]])
    A[np.diag_indices(m)] += lam
    b += lam * th0
    # sum-to-zero soft constraint on O and D (identifiability with mu): large penalty on the mean deviation
    for blk in (slice(0, n), slice(n, 2 * n)):
        A[blk, blk] += 1e3 / n
    th = np.linalg.solve(A, b)
    off, dfn, mu, hca = th[:n], th[n:2 * n], th[2 * n], th[2 * n + 1]
    # ---- pace: theta = [P(n), pi]
    mp = n + 1
    Ap = np.zeros((mp, mp))
    bp = np.zeros(mp)
    cols = np.stack([hi, ai, np.full(len(g), n)], axis=1)
    for a in range(3):
        for c in range(3):
            np.add.at(Ap, (cols[:, a], cols[:, c]), dec)
        np.add.at(bp, cols[:, a], dec * poss)
    lamp = np.concatenate([np.full(n, p.lam_pace), [p.lam_league]])
    Ap[np.diag_indices(mp)] += lamp
    bp += lamp * np.concatenate([p0, [pi0]])
    Ap[:n, :n] += 1e3 / n
    thp = np.linalg.solve(Ap, bp)
    r = Ratings(tset, off, dfn, thp[:n], float(mu), float(hca), float(thp[n]))
    last = g["knowable_at"].max() if len(g) else None
    r.meta = {"ratings_version": RATINGS_VERSION, "cutoff_utc": cutoff.isoformat(), "params": p.as_dict(),
              "games": int(len(g)), "max_game_date": None if not len(g) else str(g["game_date_et"].max().date()),
              "max_knowable_at": None if last is None else last.isoformat(), "teams": n,
              "prior": None if prior is None else prior.meta.get("state_id") or prior.meta.get("prior_of")}
    r.meta["state_id"] = r.state_id()
    return r
