# Commercial data rights and production source policy

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN**. **Deployment gate**, not a research restriction.

## 1. Rules

1. **The CBB Starter Pack remains research-only** unless commercial rights are separately obtained in writing. It may be
   used without restriction for private research, model development and historical validation inside this project.
2. **No WizardOfOdds production deployment may depend on a data source whose commercial / publication rights have not
   been verified.** "Depend" covers direct inputs, training data of the deployed models, calibration data, outcome
   grading displayed publicly, and any derived feature.
3. This gate does not require discarding legitimate private research. If a production-eligible source cannot reproduce
   what a research-only source provided, the gap is measured and the production model is retrained on
   production-eligible data (or the rights are obtained) before Phase F.

## 2. Rights register (to be completed before Phase F; one record per source)

| source | current use | commercial / publication rights | status |
|---|---|---|---|
| CBB Starter Pack | canonical history 2005..2025 (box, season files) | not verified; treated research-only | **RESEARCH_ONLY** |
| CBBD API (collegebasketballdata.com) | canonical events, rosters, recruiting, portal | terms of the API tier must be reviewed for commercial redistribution of derived products | **UNVERIFIED** |
| BALLDONTLIE NCAAB | QA, market benchmark | plan terms to be reviewed | **UNVERIFIED** |
| Sharp Stats / StatSharp | current availability (manual capture) | manual capture by an authorized user; commercial use of derived signals to be confirmed | **UNVERIFIED** |
| Covers (Amendment V2 preferred availability) | not ingested | to be reviewed | **NOT_IN_USE** |
| Bart Torvik, KenPom | not ingested | to be reviewed if ever used | **NOT_IN_USE** |

Each record will hold: the agreement / terms URL and version, the date reviewed, the reviewer, the permitted uses (internal
modeling, derived-probability publication, raw redistribution), attribution requirements, and the expiry / renewal date.

## 3. Enforcement (with the production build)

* `model_artifact` and `information_snapshot` list their sources. The publisher refuses any bundle with a source whose
  register status is not `PRODUCTION_APPROVED` (publication spec §9).
* A source's status change is a new register version with an approval (production policy §10).
