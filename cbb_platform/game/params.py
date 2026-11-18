"""Frozen game-model parameter artifacts (production reads ONLY these; never PBP / research tables).

    params/endgame_ot.npz + endgame_ot.json     endgame kernel (pace x lead x dh x da) + OT law   (research/fit_endgame_ot)
    params/game_model.json                       ratings / variance / player-adjustment parameters (research/backtest)

Every load verifies the artifact sha256 recorded in its manifest; a mismatch refuses the load (no silent drift).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from cbb_platform.game import joint as J

ROOT = Path(__file__).resolve().parent / "params"


class ParamsIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class EndgameOT:
    tables: np.ndarray                    # (n_pace, 2D+1, R+1, R+1)
    pace_edges_ppm: tuple
    t_star_sec: int
    max_lead: int
    ot: J.OvertimeParams
    sha256: str

    def kernel(self, exp_total: float) -> J.EndgameKernel:
        """The kernel of the pace bucket implied by the expected FINAL total (regulation points per minute at T*,
        net of the mean endgame increment)."""
        mins = 40.0 - self.t_star_sec / 60.0
        mean_inc = float(np.einsum("lij,i->", self.tables[1], np.arange(self.tables.shape[2]))
                         + np.einsum("lij,j->", self.tables[1], np.arange(self.tables.shape[3]))) / self.tables.shape[1]
        ppm = (exp_total - mean_inc) / mins
        b = int(np.digitize(ppm, self.pace_edges_ppm))
        return J.EndgameKernel(self.t_star_sec, self.max_lead, self.tables[b], f"pace{b}:{self.sha256[:12]}")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@lru_cache(maxsize=4)
def endgame_ot(root: str | None = None) -> EndgameOT:
    r = Path(root) if root else ROOT
    man = json.loads((r / "endgame_ot.json").read_text())
    npz = r / man["artifact"]
    if _sha(npz) != man["sha256"]:
        raise ParamsIntegrityError(f"{npz.name}: sha256 {_sha(npz)[:12]} != manifest {man['sha256'][:12]}")
    tab = np.load(npz)["table"].astype(np.float64)
    tab = tab / tab.sum(axis=(2, 3), keepdims=True)                     # float32 storage -> exact renormalization
    o = man["ot"]
    return EndgameOT(tab, tuple(man["pace_edges_ppm"]), int(man["t_star_sec"]), int(man["max_lead"]),
                     J.OvertimeParams(kappa=o["kappa"], sd_team=o["sd_team"], rho=o["rho"], reach=o["reach"],
                                      source=man["artifact"]), man["sha256"])


@lru_cache(maxsize=4)
def game_model(root: str | None = None) -> dict:
    r = Path(root) if root else ROOT
    p = r / "game_model.json"
    m = json.loads(p.read_text())
    body = {k: v for k, v in m.items() if k != "params_sha256"}
    sha = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if m.get("params_sha256") != sha:
        raise ParamsIntegrityError(f"game_model.json params_sha256 mismatch ({sha[:12]})")
    return m


def seal_game_model(m: dict, root: str | None = None) -> Path:
    """Research helper: write game_model.json with its content hash."""
    r = Path(root) if root else ROOT
    body = {k: v for k, v in m.items() if k != "params_sha256"}
    out = {**body, "params_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}
    (r / "game_model.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    game_model.cache_clear()
    return r / "game_model.json"
