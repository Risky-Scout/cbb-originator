# CBBD PBP wallclock semantics (Stage 2L)

Rendered from `data/qa/cbbd_wallclock_semantics.json` by `cbb_platform/wallclock_audit.py`. Do not edit by hand.

## Classification: **MIXED** -- policy **UNTRUSTED_MIXED** (architect ruling, Stage 2.1; closed)

Raw wallclock is preserved verbatim and is prohibited for: historical point-in-time cutoffs, feature availability, event latency, game tip reconstruction, live-model timing.

**Approved for PIT / live latency: False.** Raw wallclock is preserved unchanged and remains PROHIBITED for PIT cutoffs and live latency until the architect approves a conversion rule backed by this classification.

Games with wallclock and an exact tip: 26613. Excluded from the decision: {'SPLIT_TIMEZONE_STATE': 6710, 'NO_VENUE_TIMEZONE': 261} (venues in split-timezone states or without a timezone).

Method: the first wallclock of each game is read as a naive clock reading and compared with the BDL-verified scheduled tip under each hypothesis; a game is consistent with a hypothesis when the residual lies in (-0.25, 1.0) hours. A hypothesis is accepted for a timezone group at >= 95% consistency with every rival <= 5%. Eastern venues cannot separate Eastern-labelled from venue-local time.

| venue timezone group | games | UTC | EASTERN_TIME_MISLABELED_Z | LOCAL_VENUE_TIME_MISLABELED_Z | verdict |
|---|---|---|---|---|---|
| ATLANTIC | 6 | 0.000 | 1.000 | 0.000 | INSUFFICIENT |
| CENTRAL | 4531 | 0.053 | 0.894 | 0.007 | MIXED |
| EASTERN | 10771 | 0.053 | 0.899 | 0.899 | MIXED |
| HAWAII | 164 | 0.012 | 0.774 | 0.000 | MIXED |
| MOUNTAIN | 1165 | 0.050 | 0.913 | 0.000 | MIXED |
| MOUNTAIN_NO_DST | 318 | 0.053 | 0.909 | 0.000 | MIXED |
| PACIFIC | 2687 | 0.058 | 0.897 | 0.000 | MIXED |

## By season (verdict per timezone group)

| season | ATLANTIC | CENTRAL | EASTERN | HAWAII | MOUNTAIN | MOUNTAIN_NO_DST | PACIFIC |
|---|---|---|---|---|---|---|---|
| 2021 | - | MIXED | MIXED | INSUFFICIENT | MIXED | INSUFFICIENT | MIXED |
| 2022 | - | EASTERN_TIME_MISLABELED_Z | EASTERN_OR_LOCAL | INSUFFICIENT | EASTERN_TIME_MISLABELED_Z | EASTERN_TIME_MISLABELED_Z | EASTERN_TIME_MISLABELED_Z |
| 2023 | - | EASTERN_TIME_MISLABELED_Z | EASTERN_OR_LOCAL | MIXED | EASTERN_TIME_MISLABELED_Z | EASTERN_TIME_MISLABELED_Z | EASTERN_TIME_MISLABELED_Z |
| 2024 | - | EASTERN_TIME_MISLABELED_Z | EASTERN_OR_LOCAL | EASTERN_TIME_MISLABELED_Z | EASTERN_TIME_MISLABELED_Z | EASTERN_TIME_MISLABELED_Z | EASTERN_TIME_MISLABELED_Z |
| 2025 | INSUFFICIENT | MIXED | MIXED | MIXED | MIXED | MIXED | MIXED |

## Consistency patterns

```json
{
 "ATLANTIC": {
  "EASTERN_TIME_MISLABELED_Z": 6
 },
 "CENTRAL": {
  "EASTERN_TIME_MISLABELED_Z": 4021,
  "UTC": 239,
  "NONE": 238,
  "EASTERN_TIME_MISLABELED_Z+LOCAL_VENUE_TIME_MISLABELED_Z": 30,
  "LOCAL_VENUE_TIME_MISLABELED_Z": 3
 },
 "EASTERN": {
  "EASTERN_TIME_MISLABELED_Z+LOCAL_VENUE_TIME_MISLABELED_Z": 9679,
  "UTC": 574,
  "NONE": 518
 },
 "HAWAII": {
  "EASTERN_TIME_MISLABELED_Z": 127,
  "NONE": 35,
  "UTC": 2
 },
 "MOUNTAIN": {
  "EASTERN_TIME_MISLABELED_Z": 1064,
  "UTC": 58,
  "NONE": 43
 },
 "MOUNTAIN_NO_DST": {
  "EASTERN_TIME_MISLABELED_Z": 289,
  "UTC": 17,
  "NONE": 12
 },
 "PACIFIC": {
  "EASTERN_TIME_MISLABELED_Z": 2410,
  "UTC": 155,
  "NONE": 122
 }
}
```

Neutral-site games: {'EASTERN_TIME_MISLABELED_Z+LOCAL_VENUE_TIME_MISLABELED_Z': 826, 'EASTERN_TIME_MISLABELED_Z': 817, 'UTC': 142, 'NONE': 117}

First-to-last wallclock span (minutes): {'p10': 99.2, 'median': 114.3, 'p90': 130.8}

## Examples

```json
{
 "EASTERN": [
  {
   "game": "G-cf338b9a81c9575e",
   "season": 2021,
   "tip_utc": "2021-01-14 17:00:00+00:00",
   "venue_state": "PA",
   "first_wallclock_raw": "2021-01-14T17:15:07.000Z",
   "residual_hours": {
    "UTC": 0.25,
    "EASTERN_TIME_MISLABELED_Z": 5.25,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.25
   }
  },
  {
   "game": "G-94716c5e80e74ec8",
   "season": 2021,
   "tip_utc": "2021-01-14 23:00:00+00:00",
   "venue_state": "SC",
   "first_wallclock_raw": "2021-01-14T23:02:32.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.04
   }
  },
  {
   "game": "G-243983397da3617c",
   "season": 2021,
   "tip_utc": "2021-01-15 00:00:00+00:00",
   "venue_state": "MI",
   "first_wallclock_raw": "2021-01-15T00:04:14.000Z",
   "residual_hours": {
    "UTC": 0.07,
    "EASTERN_TIME_MISLABELED_Z": 5.07,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.07
   }
  },
  {
   "game": "G-48a65058a9e00cec",
   "season": 2021,
   "tip_utc": "2021-01-15 00:00:00+00:00",
   "venue_state": "PA",
   "first_wallclock_raw": "2021-01-15T00:02:53.000Z",
   "residual_hours": {
    "UTC": 0.05,
    "EASTERN_TIME_MISLABELED_Z": 5.05,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.05
   }
  },
  {
   "game": "G-498fb84f13805a65",
   "season": 2021,
   "tip_utc": "2021-01-15 00:00:00+00:00",
   "venue_state": "CT",
   "first_wallclock_raw": "2021-01-15T00:07:02.000Z",
   "residual_hours": {
    "UTC": 0.12,
    "EASTERN_TIME_MISLABELED_Z": 5.12,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.12
   }
  }
 ],
 "MOUNTAIN": [
  {
   "game": "G-b4f6a45b1f17946e",
   "season": 2021,
   "tip_utc": "2021-01-14 22:00:00+00:00",
   "venue_state": "UT",
   "first_wallclock_raw": "2021-01-14T22:02:21.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.04
   }
  },
  {
   "game": "G-0865a6c3375f3003",
   "season": 2021,
   "tip_utc": "2021-01-15 00:00:00+00:00",
   "venue_state": "MT",
   "first_wallclock_raw": "2021-01-15T00:08:12.000Z",
   "residual_hours": {
    "UTC": 0.14,
    "EASTERN_TIME_MISLABELED_Z": 5.14,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.14
   }
  },
  {
   "game": "G-c7ad480988f4c806",
   "season": 2021,
   "tip_utc": "2021-01-15 19:00:00+00:00",
   "venue_state": "CO",
   "first_wallclock_raw": "2021-01-15T19:08:11.000Z",
   "residual_hours": {
    "UTC": 0.14,
    "EASTERN_TIME_MISLABELED_Z": 5.14,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.14
   }
  },
  {
   "game": "G-d615ac132c021152",
   "season": 2021,
   "tip_utc": "2021-01-16 18:00:00+00:00",
   "venue_state": "MT",
   "first_wallclock_raw": "2021-01-16T18:06:18.000Z",
   "residual_hours": {
    "UTC": 0.1,
    "EASTERN_TIME_MISLABELED_Z": 5.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.11
   }
  },
  {
   "game": "G-85f7a25752066a62",
   "season": 2021,
   "tip_utc": "2021-01-16 20:00:00+00:00",
   "venue_state": "CO",
   "first_wallclock_raw": "2021-01-16T20:06:37.000Z",
   "residual_hours": {
    "UTC": 0.11,
    "EASTERN_TIME_MISLABELED_Z": 5.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.11
   }
  }
 ],
 "PACIFIC": [
  {
   "game": "G-33d5b5c7769ffa6b",
   "season": 2021,
   "tip_utc": "2021-01-14 22:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-01-14T22:02:15.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.04
   }
  },
  {
   "game": "G-bf092c45100cbae7",
   "season": 2021,
   "tip_utc": "2021-01-15 02:05:00+00:00",
   "venue_state": "WA",
   "first_wallclock_raw": "2021-01-15T02:07:17.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.04
   }
  },
  {
   "game": "G-b724b1dacc50c3b9",
   "season": 2021,
   "tip_utc": "2021-01-16 00:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-01-16T00:05:30.000Z",
   "residual_hours": {
    "UTC": 0.09,
    "EASTERN_TIME_MISLABELED_Z": 5.09,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.09
   }
  },
  {
   "game": "G-4d39bab75b2a45f7",
   "season": 2021,
   "tip_utc": "2021-01-16 02:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-01-16T02:00:44.000Z",
   "residual_hours": {
    "UTC": 0.01,
    "EASTERN_TIME_MISLABELED_Z": 5.01,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.01
   }
  },
  {
   "game": "G-4d6456a67854853b",
   "season": 2021,
   "tip_utc": "2021-01-16 22:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-01-16T22:06:59.000Z",
   "residual_hours": {
    "UTC": 0.12,
    "EASTERN_TIME_MISLABELED_Z": 5.12,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.12
   }
  }
 ],
 "CENTRAL": [
  {
   "game": "G-918522136f55590e",
   "season": 2021,
   "tip_utc": "2021-01-15 01:30:00+00:00",
   "venue_state": "AL",
   "first_wallclock_raw": "2021-01-15T01:31:56.000Z",
   "residual_hours": {
    "UTC": 0.03,
    "EASTERN_TIME_MISLABELED_Z": 5.03,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.03
   }
  },
  {
   "game": "G-17fd384836f82b6e",
   "season": 2021,
   "tip_utc": "2021-01-15 02:00:00+00:00",
   "venue_state": "IL",
   "first_wallclock_raw": "2021-01-15T02:08:08.000Z",
   "residual_hours": {
    "UTC": 0.14,
    "EASTERN_TIME_MISLABELED_Z": 5.14,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.14
   }
  },
  {
   "game": "G-7f88823edbcd7d22",
   "season": 2021,
   "tip_utc": "2021-01-16 00:00:00+00:00",
   "venue_state": "WI",
   "first_wallclock_raw": "2021-01-16T00:01:56.000Z",
   "residual_hours": {
    "UTC": 0.03,
    "EASTERN_TIME_MISLABELED_Z": 5.03,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.03
   }
  },
  {
   "game": "G-26f1a89cb1166645",
   "season": 2021,
   "tip_utc": "2021-01-16 00:00:00+00:00",
   "venue_state": "AR",
   "first_wallclock_raw": "2021-01-16T00:22:33.000Z",
   "residual_hours": {
    "UTC": 0.38,
    "EASTERN_TIME_MISLABELED_Z": 5.38,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.38
   }
  },
  {
   "game": "G-4cb1bb1c799c27ef",
   "season": 2021,
   "tip_utc": "2021-01-16 00:30:00+00:00",
   "venue_state": "AR",
   "first_wallclock_raw": "2021-01-16T00:32:45.000Z",
   "residual_hours": {
    "UTC": 0.05,
    "EASTERN_TIME_MISLABELED_Z": 5.05,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.05
   }
  }
 ],
 "HAWAII": [
  {
   "game": "G-f5d0062747099f65",
   "season": 2021,
   "tip_utc": "2021-01-16 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2021-01-15T05:07:10.000Z",
   "residual_hours": {
    "UTC": -23.86,
    "EASTERN_TIME_MISLABELED_Z": -18.86,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -13.86
   }
  },
  {
   "game": "G-7d2e1184fadd7a96",
   "season": 2021,
   "tip_utc": "2021-02-28 03:00:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2021-02-27T22:06:44.000Z",
   "residual_hours": {
    "UTC": -4.89,
    "EASTERN_TIME_MISLABELED_Z": 0.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.11
   }
  },
  {
   "game": "G-0761a7a6c911503e",
   "season": 2022,
   "tip_utc": "2022-01-14 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2022-01-13T00:08:17.000Z",
   "residual_hours": {
    "UTC": -28.85,
    "EASTERN_TIME_MISLABELED_Z": -23.85,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -18.85
   }
  },
  {
   "game": "G-fefff90ec3cb526a",
   "season": 2022,
   "tip_utc": "2022-01-16 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2022-01-15T00:08:52.000Z",
   "residual_hours": {
    "UTC": -28.84,
    "EASTERN_TIME_MISLABELED_Z": -23.84,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -18.84
   }
  },
  {
   "game": "G-d6bb1baaf4300df3",
   "season": 2022,
   "tip_utc": "2021-11-14 02:30:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2021-11-13T21:31:53.000Z",
   "residual_hours": {
    "UTC": -4.97,
    "EASTERN_TIME_MISLABELED_Z": 0.03,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.03
   }
  }
 ],
 "MOUNTAIN_NO_DST": [
  {
   "game": "G-79d5daf97c588bf6",
   "season": 2021,
   "tip_utc": "2021-01-16 21:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-01-16T21:07:48.000Z",
   "residual_hours": {
    "UTC": 0.13,
    "EASTERN_TIME_MISLABELED_Z": 5.13,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.13
   }
  },
  {
   "game": "G-3d297b92b759371c",
   "season": 2021,
   "tip_utc": "2021-02-28 02:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-02-27T21:07:54.000Z",
   "residual_hours": {
    "UTC": -4.87,
    "EASTERN_TIME_MISLABELED_Z": 0.13,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.13
   }
  },
  {
   "game": "G-9dd0ff189b10d6a9",
   "season": 2021,
   "tip_utc": "2021-03-07 02:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-03-06T21:07:45.000Z",
   "residual_hours": {
    "UTC": -4.87,
    "EASTERN_TIME_MISLABELED_Z": 0.13,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.13
   }
  },
  {
   "game": "G-7a7a4afab81cb316",
   "season": 2021,
   "tip_utc": "2021-02-12 01:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-02-11T20:06:44.000Z",
   "residual_hours": {
    "UTC": -4.89,
    "EASTERN_TIME_MISLABELED_Z": 0.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.11
   }
  },
  {
   "game": "G-4f62674ef4420b32",
   "season": 2021,
   "tip_utc": "2021-02-13 19:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-02-13T14:05:58.000Z",
   "residual_hours": {
    "UTC": -4.9,
    "EASTERN_TIME_MISLABELED_Z": 0.1,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.1
   }
  }
 ],
 "ATLANTIC": [
  {
   "game": "G-34d92c9390caabc2",
   "season": 2025,
   "tip_utc": "2024-11-23 21:00:00+00:00",
   "venue_state": "Puerto Rico",
   "first_wallclock_raw": "2024-11-23T16:12:16.000Z",
   "residual_hours": {
    "UTC": -4.8,
    "EASTERN_TIME_MISLABELED_Z": 0.2,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -0.8
   }
  },
  {
   "game": "G-77231480f802b8fc",
   "season": 2025,
   "tip_utc": "2024-11-24 20:30:00+00:00",
   "venue_state": "Puerto Rico",
   "first_wallclock_raw": "2024-11-24T15:33:36.000Z",
   "residual_hours": {
    "UTC": -4.94,
    "EASTERN_TIME_MISLABELED_Z": 0.06,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -0.94
   }
  },
  {
   "game": "G-e7971264af4f01c0",
   "season": 2025,
   "tip_utc": "2024-11-22 21:30:00+00:00",
   "venue_state": "Puerto Rico",
   "first_wallclock_raw": "2024-11-22T16:38:58.000Z",
   "residual_hours": {
    "UTC": -4.85,
    "EASTERN_TIME_MISLABELED_Z": 0.15,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -0.85
   }
  },
  {
   "game": "G-e17d6d508ace2457",
   "season": 2025,
   "tip_utc": "2024-11-23 18:00:00+00:00",
   "venue_state": "Puerto Rico",
   "first_wallclock_raw": "2024-11-23T13:11:28.000Z",
   "residual_hours": {
    "UTC": -4.81,
    "EASTERN_TIME_MISLABELED_Z": 0.19,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -0.81
   }
  },
  {
   "game": "G-7bcd1111b440150a",
   "season": 2025,
   "tip_utc": "2024-11-24 17:00:00+00:00",
   "venue_state": "Puerto Rico",
   "first_wallclock_raw": "2024-11-24T12:10:49.000Z",
   "residual_hours": {
    "UTC": -4.82,
    "EASTERN_TIME_MISLABELED_Z": 0.18,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -0.82
   }
  }
 ]
}
```
