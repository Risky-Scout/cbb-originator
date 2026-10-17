# LOCKBOX_POLICY_v2.md — 2025-26 holdout: the one-time opening

**Status:** NEW VERSION of `LOCKBOX_POLICY.md` (protocol §2.2), recorded on the opening branch `lockbox/open-2025-26`.
`LOCKBOX_POLICY.md` (v1, FROZEN 2026-10-05) is kept unchanged; every rule there remains in force except as amended
below. **Enforced by:** `cbb_platform/lockbox.py` (guard v3), `cbb_platform/source_fetch.py`.

## 1. The opening

* **Decision:** `data/manifests/LOCKBOX_OPEN_DECISION_2025_26.json` (decision id `LOCKBOX_OPEN_2025_26_CBB_PROPS_V1`,
  authorized 2026-10-16 by architect ruling; one approver available, stated as such). Its sha256 is bound in
  `lockbox.OPEN_DECISION_SHA256` on the opening commit only.
* **Freeze record cited (v1 §4):** the frozen candidate (tag `cbb-props-v1-prelockbox-2024-25-r2`, artifact sha256
  `96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f`), the frozen scorecard
  (`docs/lockbox/lockbox_scorecard_v2.json`), the frozen data-opening plan and the readiness record of commit
  `d65aac908c5b910476d95d299ae3cf49093d4733` (NON_APPROVAL_LOCKBOX_GATES = PASS).
* **Reviewed guard change:** `docs/lockbox/lockbox_open_2025_26_guard_v3.patch` (sha256 `366ba2d1…cba4e`) and the
  opening-time loader change `docs/lockbox/lockbox_open_2025_26_scoring_enable.patch` (sha256 `8f964e01…be7`), applied
  byte-for-byte (design approval: ruling Q).

## 2. What the opening allows (and nothing else)

2025-26 is allowed only when ALL hold: the bound decision record hashes to `OPEN_DECISION_SHA256`; the session sets
`LOCKBOX_OPEN_2025_26=1` (a flag alone never opens it, v1 §4); the provider is CBBD (or the canonical / Stage 3 readers);
the endpoint is in the frozen data plan (`games`, `games/teams`, `games/players`, `teams/roster`, `substitutions/team`,
`stats/player/season`, `stats/team/season`, `recruiting/players`, `recruiting/portal`).

## 3. What stays forbidden

* Every season after 2025-26 (2026-27 and later), any other provider, any unplanned endpoint.
* Any use of 2025-26 for fitting, recalibration, re-selection, tuning, feature or architecture decisions, or any change
  to the scorecard, its thresholds or their interpretation (protocol §2.4, §3). 2025-26 is consumed solely as the final
  go/no-go evaluation of the frozen candidate. A second opening is not possible.
* Every commit other than the opening commit keeps `OPEN_DECISION_SHA256 = None` (closed).
