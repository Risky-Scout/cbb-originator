"""Stage 07, local and read-only: when each curated status row became knowable to production.

For every row of the curated roster-omission and RotoWire player-state CSVs it records:
* verified_on: the date the code applies the row from (00:00 ET of verified_on);
* the first commit introducing the row's exact content (git log -S on the row's key fields);
* the first production deploy containing that commit. Deploys are taken from the ledger lineage; that time is the row's
  conservative acquisition bound.

Output: results/curated_provenance.{csv,json}."""
import json, subprocess, sys
import pandas as pd
from cbb_platform.prod import config as PC
OUT = sys.argv[1]
DEPLOYS = json.loads(sys.argv[2])                 # [[commit, deployed_at_utc], ...] in deploy order
rows = []
for kind, path, key in (("ROSTER_OMISSION", PC.ROSTER_OMISSIONS, ("canonical_player_id", "canonical_team_id")),
                        ("ROTOWIRE_PLAYER_STATE", PC.ROTOWIRE_PLAYER_STATES, ("rotowire_player_id",))):
    m = pd.read_csv(path, dtype=str, keep_default_na=False)
    raw = path.read_text().splitlines()[1:]
    if len(raw) != len(m):
        raise SystemExit(f"{path}: multi-line rows; exact-content tracing needs one line per row")
    for r, line in zip(m.itertuples(index=False), raw):
        d = r._asdict()
        needle = line                                  # the row's exact current content (a later revision re-dates it)
        log = subprocess.run(["git", "log", "--reverse", "--format=%H %cI", "-S", needle, "--", str(path)],
                             capture_output=True, text=True).stdout.split("\n")
        first = log[0].split() if log and log[0] else [None, None]
        dep = None
        if first[0]:
            for c, at in DEPLOYS:
                if subprocess.run(["git", "merge-base", "--is-ancestor", first[0], c]).returncode == 0:
                    dep = (c, at); break
        vo = pd.Timestamp(d.get("verified_on") or "NaT")
        applied_from = vo.tz_localize("America/New_York").tz_convert("UTC") if pd.notna(vo) else None
        rows.append({"kind": kind, "key": str(d.get(key[0], "")), "verified_on": d.get("verified_on"), "applied_from_utc": applied_from,
                     "first_commit": first[0], "first_commit_at": first[1], "first_deploy": dep[0] if dep else None,
                     "first_deploy_at": dep[1] if dep else None})
df = pd.DataFrame(rows)
df["deploy_after_applied_from_h"] = (pd.to_datetime(df.first_deploy_at, utc=True) - pd.to_datetime(df.applied_from_utc, utc=True)).dt.total_seconds() / 3600
df.to_csv(f"{OUT}/curated_provenance.csv", index=False)
rep = {k: {"rows": int(len(g)), "deployed": int(g.first_deploy.notna().sum()),
           "not_yet_deployed": int(g.first_deploy.isna().sum()),
           "applied_before_deploy_rows": int((g.deploy_after_applied_from_h > 0).sum()),
           "backdating_h_quantiles": g.deploy_after_applied_from_h.dropna().quantile([0, .5, .9, 1]).round(1).to_dict()}
       for k, g in df.groupby("kind")}
json.dump(rep, open(f"{OUT}/curated_provenance.json", "w"), indent=1, default=str)
print(json.dumps(rep, indent=1, default=str))
