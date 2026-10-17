# Off-machine baseline and backup plan

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN (plan)**. **Nothing has been configured or pushed.**
The repository has **no git remote** (`git remote -v` is empty). No unknown destination is used without the user's
approval.

## 1. What must exist off-machine before the future v2.4-successor baseline is accepted

| item | content | size (2026-10-07) | how |
|---|---|---|---|
| git history | all branches and tags (incl. `main`, `research/stage3-pit-feature-store`) | ~12 MB | `git push --mirror` to a private remote, or a `git bundle` |
| authoritative documents | everything in `docs/ARCHITECTURE_PRECEDENCE.md` §2 | in git | git |
| environment lock | `uv.lock`, `requirements.lock.txt`, `ENVIRONMENT_FINGERPRINT.json` (A2) | small | git |
| dataset manifests | `data/manifests/DATASET_MANIFEST*.json` (all ancestors), `starter_pack_*`, `cbbd_backfill_manifest.json` | small | git |
| raw manifest / checksums | per-payload sha256 sidecars + the raw manifest hash; Starter Pack sha256 manifest | small | git (sidecar index export) |
| **raw payloads** | `data/raw/**` (immutable inputs; 1.4 GB on disk) | 1.4 GB | encrypted object storage or an encrypted external drive |
| Starter Pack | the upstream file set (research-only license; private storage only) | ~0.8 GB | encrypted private storage, never public |
| canonical fingerprints | `canonical_tables` in the dataset manifest | in git | git |
| feature / label fingerprints | `modeling/STAGE3_MANIFEST.json`, `modeling/folds/*.json` | in git | git |
| test evidence | the full-suite log and the clean-room comparison JSON | small | git (`data/qa/`) |

Derived stores (`data/canonical`, `modeling/store`, Parquet) are **not** backed up as the baseline of record. They are
rebuilt from the items above, and the fingerprints prove it. An optional convenience copy may be kept.

## 2. Verification of a backup

A restore drill (clean-room procedure, `docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md` §3) from the off-machine copy must
reproduce the accepted fingerprints. The drill result is committed.

## 3. Decision still needed from the user

1. **Git remote:** which private host and repository (e.g. a private GitHub / GitLab repository under the user's
   account), and whether the research branches are pushed or only `main`.
2. **Bulk data destination:** where the 1.4 GB raw store and the Starter Pack go: an encrypted external drive, a
   personal cloud bucket (provider and region), or both. Who holds the encryption key.
3. **Retention:** how many dataset generations to keep off-machine (recommendation: all manifests forever; raw store
   is append-only, so one current mirror plus monthly snapshots).
Until these are answered, the plan is frozen but not executed. This is a Phase A2 dependency.
