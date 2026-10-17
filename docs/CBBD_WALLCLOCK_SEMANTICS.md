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
 "CENTRAL": [
  {
   "game": "G-7d01dfebab49f785",
   "season": 2021,
   "tip_utc": "2021-01-17 22:00:00+00:00",
   "venue_state": "IL",
   "first_wallclock_raw": "2021-01-17T22:01:21.000Z",
   "residual_hours": {
    "UTC": 0.02,
    "EASTERN_TIME_MISLABELED_Z": 5.02,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.02
   }
  },
  {
   "game": "G-773459980a5fd6a6",
   "season": 2021,
   "tip_utc": "2021-01-17 17:00:00+00:00",
   "venue_state": "IL",
   "first_wallclock_raw": "2021-01-17T17:03:06.000Z",
   "residual_hours": {
    "UTC": 0.05,
    "EASTERN_TIME_MISLABELED_Z": 5.05,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.05
   }
  },
  {
   "game": "G-790b4b2115b133f2",
   "season": 2021,
   "tip_utc": "2021-01-19 23:30:00+00:00",
   "venue_state": "OK",
   "first_wallclock_raw": "2021-01-19T23:34:02.000Z",
   "residual_hours": {
    "UTC": 0.07,
    "EASTERN_TIME_MISLABELED_Z": 5.07,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.07
   }
  },
  {
   "game": "G-3b87d8522e656b41",
   "season": 2021,
   "tip_utc": "2021-01-20 00:00:00+00:00",
   "venue_state": "IL",
   "first_wallclock_raw": "2021-01-20T00:06:35.000Z",
   "residual_hours": {
    "UTC": 0.11,
    "EASTERN_TIME_MISLABELED_Z": 5.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.11
   }
  },
  {
   "game": "G-34bd82604d2f7c68",
   "season": 2021,
   "tip_utc": "2021-01-21 01:00:00+00:00",
   "venue_state": "WI",
   "first_wallclock_raw": "2021-01-21T01:02:02.000Z",
   "residual_hours": {
    "UTC": 0.03,
    "EASTERN_TIME_MISLABELED_Z": 5.03,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.03
   }
  }
 ],
 "MOUNTAIN": [
  {
   "game": "G-5bfbfa116d74c48b",
   "season": 2021,
   "tip_utc": "2021-01-18 02:00:00+00:00",
   "venue_state": "UT",
   "first_wallclock_raw": "2021-01-18T02:07:54.000Z",
   "residual_hours": {
    "UTC": 0.13,
    "EASTERN_TIME_MISLABELED_Z": 5.13,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.13
   }
  },
  {
   "game": "G-9fe18f72531cea8a",
   "season": 2021,
   "tip_utc": "2021-01-19 02:00:00+00:00",
   "venue_state": "CO",
   "first_wallclock_raw": "2021-01-19T02:05:40.000Z",
   "residual_hours": {
    "UTC": 0.09,
    "EASTERN_TIME_MISLABELED_Z": 5.09,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.09
   }
  },
  {
   "game": "G-2915fc93037bc830",
   "season": 2021,
   "tip_utc": "2021-01-22 01:30:00+00:00",
   "venue_state": "CO",
   "first_wallclock_raw": "2021-01-22T01:38:12.000Z",
   "residual_hours": {
    "UTC": 0.14,
    "EASTERN_TIME_MISLABELED_Z": 5.14,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.14
   }
  },
  {
   "game": "G-5c62344a3697574c",
   "season": 2021,
   "tip_utc": "2021-01-23 01:00:00+00:00",
   "venue_state": "WY",
   "first_wallclock_raw": "2021-01-23T01:08:15.000Z",
   "residual_hours": {
    "UTC": 0.14,
    "EASTERN_TIME_MISLABELED_Z": 5.14,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.14
   }
  },
  {
   "game": "G-3839cfaad247b006",
   "season": 2021,
   "tip_utc": "2021-01-23 02:00:00+00:00",
   "venue_state": "CO",
   "first_wallclock_raw": "2021-01-23T02:10:51.000Z",
   "residual_hours": {
    "UTC": 0.18,
    "EASTERN_TIME_MISLABELED_Z": 5.18,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.18
   }
  }
 ],
 "PACIFIC": [
  {
   "game": "G-0accbe7918f01819",
   "season": 2021,
   "tip_utc": "2021-01-18 03:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-01-18T03:02:10.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.04
   }
  },
  {
   "game": "G-38aa1b1dd0abf6ac",
   "season": 2021,
   "tip_utc": "2021-01-21 02:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-01-21T02:10:34.000Z",
   "residual_hours": {
    "UTC": 0.18,
    "EASTERN_TIME_MISLABELED_Z": 5.18,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.18
   }
  },
  {
   "game": "G-69bbae2ad0b78515",
   "season": 2021,
   "tip_utc": "2021-01-21 02:00:00+00:00",
   "venue_state": "WA",
   "first_wallclock_raw": "2021-01-21T02:02:27.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.04
   }
  },
  {
   "game": "G-59d5b6d9e0c2b575",
   "season": 2021,
   "tip_utc": "2021-01-22 01:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-01-22T01:08:23.000Z",
   "residual_hours": {
    "UTC": 0.14,
    "EASTERN_TIME_MISLABELED_Z": 5.14,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.14
   }
  },
  {
   "game": "G-1238fc3097d8c9f5",
   "season": 2021,
   "tip_utc": "2021-01-22 02:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-01-22T02:05:26.000Z",
   "residual_hours": {
    "UTC": 0.09,
    "EASTERN_TIME_MISLABELED_Z": 5.09,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.09
   }
  }
 ],
 "EASTERN": [
  {
   "game": "G-22431afd73d1e1e6",
   "season": 2021,
   "tip_utc": "2021-01-18 19:30:00+00:00",
   "venue_state": "CT",
   "first_wallclock_raw": "2021-01-18T19:37:38.000Z",
   "residual_hours": {
    "UTC": 0.13,
    "EASTERN_TIME_MISLABELED_Z": 5.13,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.13
   }
  },
  {
   "game": "G-ada86b40751f434e",
   "season": 2021,
   "tip_utc": "2021-01-18 19:00:00+00:00",
   "venue_state": "MD",
   "first_wallclock_raw": "2021-01-18T19:03:29.000Z",
   "residual_hours": {
    "UTC": 0.06,
    "EASTERN_TIME_MISLABELED_Z": 5.06,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.06
   }
  },
  {
   "game": "G-bfa1a77832db5d7f",
   "season": 2021,
   "tip_utc": "2021-01-19 00:00:00+00:00",
   "venue_state": "NC",
   "first_wallclock_raw": "2021-01-19T00:03:21.000Z",
   "residual_hours": {
    "UTC": 0.06,
    "EASTERN_TIME_MISLABELED_Z": 5.06,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.06
   }
  },
  {
   "game": "G-b5db2c5d203fcd26",
   "season": 2021,
   "tip_utc": "2021-01-20 02:00:00+00:00",
   "venue_state": "PA",
   "first_wallclock_raw": "2021-01-20T02:11:43.000Z",
   "residual_hours": {
    "UTC": 0.2,
    "EASTERN_TIME_MISLABELED_Z": 5.2,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.2
   }
  },
  {
   "game": "G-b2eab18c1d9b3b0c",
   "season": 2021,
   "tip_utc": "2021-01-20 00:00:00+00:00",
   "venue_state": "MI",
   "first_wallclock_raw": "2021-01-20T00:02:24.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.04
   }
  }
 ],
 "MOUNTAIN_NO_DST": [
  {
   "game": "G-391a651c8653dc73",
   "season": 2021,
   "tip_utc": "2021-01-22 01:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-01-22T01:06:51.000Z",
   "residual_hours": {
    "UTC": 0.11,
    "EASTERN_TIME_MISLABELED_Z": 5.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.11
   }
  },
  {
   "game": "G-72f6033d7e8fdca6",
   "season": 2021,
   "tip_utc": "2021-01-23 19:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-01-23T19:03:33.000Z",
   "residual_hours": {
    "UTC": 0.06,
    "EASTERN_TIME_MISLABELED_Z": 5.06,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.06
   }
  },
  {
   "game": "G-bffb846ffe2ce01c",
   "season": 2021,
   "tip_utc": "2021-01-26 04:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-01-26T04:05:05.000Z",
   "residual_hours": {
    "UTC": 0.08,
    "EASTERN_TIME_MISLABELED_Z": 5.08,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.08
   }
  },
  {
   "game": "G-99bc98e2a960a7d1",
   "season": 2021,
   "tip_utc": "2021-01-29 03:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-01-29T03:05:31.000Z",
   "residual_hours": {
    "UTC": 0.09,
    "EASTERN_TIME_MISLABELED_Z": 5.09,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.09
   }
  },
  {
   "game": "G-84e5aae36ae8e105",
   "season": 2021,
   "tip_utc": "2021-01-29 04:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-01-29T04:11:33.000Z",
   "residual_hours": {
    "UTC": 0.19,
    "EASTERN_TIME_MISLABELED_Z": 5.19,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.19
   }
  }
 ],
 "HAWAII": [
  {
   "game": "G-e6049fe986d0eb21",
   "season": 2021,
   "tip_utc": "2021-02-27 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2021-02-26T00:07:19.000Z",
   "residual_hours": {
    "UTC": -28.86,
    "EASTERN_TIME_MISLABELED_Z": -23.86,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -18.86
   }
  },
  {
   "game": "G-6db1ab53bf100c7c",
   "season": 2021,
   "tip_utc": "2021-01-31 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2021-01-30T05:07:11.000Z",
   "residual_hours": {
    "UTC": -23.86,
    "EASTERN_TIME_MISLABELED_Z": -18.86,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -13.86
   }
  },
  {
   "game": "G-0149518fd4b4c78b",
   "season": 2021,
   "tip_utc": "2021-02-06 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2021-02-05T00:06:35.000Z",
   "residual_hours": {
    "UTC": -28.87,
    "EASTERN_TIME_MISLABELED_Z": -23.87,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -18.87
   }
  },
  {
   "game": "G-65b84f56d1d90d61",
   "season": 2022,
   "tip_utc": "2022-02-11 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2022-02-10T00:08:14.000Z",
   "residual_hours": {
    "UTC": -28.85,
    "EASTERN_TIME_MISLABELED_Z": -23.85,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -18.85
   }
  },
  {
   "game": "G-f26e65b2dcb9639c",
   "season": 2022,
   "tip_utc": "2022-02-13 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2022-02-12T00:08:11.000Z",
   "residual_hours": {
    "UTC": -28.85,
    "EASTERN_TIME_MISLABELED_Z": -23.85,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -18.85
   }
  }
 ],
 "ATLANTIC": [
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
   "game": "G-43d8216559862923",
   "season": 2025,
   "tip_utc": "2024-11-22 18:00:00+00:00",
   "venue_state": "Puerto Rico",
   "first_wallclock_raw": "2024-11-22T13:12:58.000Z",
   "residual_hours": {
    "UTC": -4.78,
    "EASTERN_TIME_MISLABELED_Z": 0.22,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -0.78
   }
  }
 ]
}
```
