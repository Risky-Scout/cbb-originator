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
 "MOUNTAIN": [
  {
   "game": "G-06b4bf8e53d23c58",
   "season": 2021,
   "tip_utc": "2021-02-07 01:00:00+00:00",
   "venue_state": "CO",
   "first_wallclock_raw": "2021-02-06T20:01:50.000Z",
   "residual_hours": {
    "UTC": -4.97,
    "EASTERN_TIME_MISLABELED_Z": 0.03,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.03
   }
  },
  {
   "game": "G-9b942636efc7d3f2",
   "season": 2021,
   "tip_utc": "2021-02-09 21:00:00+00:00",
   "venue_state": "UT",
   "first_wallclock_raw": "2021-02-09T16:05:37.000Z",
   "residual_hours": {
    "UTC": -4.91,
    "EASTERN_TIME_MISLABELED_Z": 0.09,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.09
   }
  },
  {
   "game": "G-731fa7877bb066c5",
   "season": 2021,
   "tip_utc": "2021-02-12 02:00:00+00:00",
   "venue_state": "CO",
   "first_wallclock_raw": "2021-02-11T21:08:57.000Z",
   "residual_hours": {
    "UTC": -4.85,
    "EASTERN_TIME_MISLABELED_Z": 0.15,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.15
   }
  },
  {
   "game": "G-bd49acaa1028b2ac",
   "season": 2021,
   "tip_utc": "2021-02-13 01:00:00+00:00",
   "venue_state": "UT",
   "first_wallclock_raw": "2021-02-12T20:07:14.000Z",
   "residual_hours": {
    "UTC": -4.88,
    "EASTERN_TIME_MISLABELED_Z": 0.12,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.12
   }
  },
  {
   "game": "G-bb106fa447370f23",
   "season": 2021,
   "tip_utc": "2021-02-13 20:00:00+00:00",
   "venue_state": "CO",
   "first_wallclock_raw": "2021-02-13T15:06:17.000Z",
   "residual_hours": {
    "UTC": -4.9,
    "EASTERN_TIME_MISLABELED_Z": 0.1,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.1
   }
  }
 ],
 "EASTERN": [
  {
   "game": "G-edba975d401c93c6",
   "season": 2021,
   "tip_utc": "2021-02-07 01:00:00+00:00",
   "venue_state": "GA",
   "first_wallclock_raw": "2021-02-06T20:01:43.000Z",
   "residual_hours": {
    "UTC": -4.97,
    "EASTERN_TIME_MISLABELED_Z": 0.03,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 0.03
   }
  },
  {
   "game": "G-2417eb2101a437cc",
   "season": 2021,
   "tip_utc": "2021-02-07 19:00:00+00:00",
   "venue_state": "VA",
   "first_wallclock_raw": "2021-02-07T14:05:43.000Z",
   "residual_hours": {
    "UTC": -4.9,
    "EASTERN_TIME_MISLABELED_Z": 0.1,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 0.1
   }
  },
  {
   "game": "G-354aa4744e2d3a9b",
   "season": 2021,
   "tip_utc": "2021-02-08 18:00:00+00:00",
   "venue_state": "OH",
   "first_wallclock_raw": "2021-02-08T13:05:40.000Z",
   "residual_hours": {
    "UTC": -4.91,
    "EASTERN_TIME_MISLABELED_Z": 0.09,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 0.09
   }
  },
  {
   "game": "G-b2094bcb52274342",
   "season": 2021,
   "tip_utc": "2021-02-08 21:00:00+00:00",
   "venue_state": "VA",
   "first_wallclock_raw": "2021-02-08T16:03:44.000Z",
   "residual_hours": {
    "UTC": -4.94,
    "EASTERN_TIME_MISLABELED_Z": 0.06,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 0.06
   }
  },
  {
   "game": "G-5e798f44628a8e6f",
   "season": 2021,
   "tip_utc": "2021-02-08 22:00:00+00:00",
   "venue_state": "SC",
   "first_wallclock_raw": "2021-02-08T17:03:30.000Z",
   "residual_hours": {
    "UTC": -4.94,
    "EASTERN_TIME_MISLABELED_Z": 0.06,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 0.06
   }
  }
 ],
 "CENTRAL": [
  {
   "game": "G-9568e7085c73b9f2",
   "season": 2021,
   "tip_utc": "2021-02-07 01:00:00+00:00",
   "venue_state": "LA",
   "first_wallclock_raw": "2021-02-06T20:04:17.000Z",
   "residual_hours": {
    "UTC": -4.93,
    "EASTERN_TIME_MISLABELED_Z": 0.07,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 1.07
   }
  },
  {
   "game": "G-b7439a894379137a",
   "season": 2021,
   "tip_utc": "2021-02-07 17:00:00+00:00",
   "venue_state": "LA",
   "first_wallclock_raw": "2021-02-07T12:03:24.000Z",
   "residual_hours": {
    "UTC": -4.94,
    "EASTERN_TIME_MISLABELED_Z": 0.06,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 1.06
   }
  },
  {
   "game": "G-8ed800767fb3d0d6",
   "season": 2021,
   "tip_utc": "2021-02-11 00:30:00+00:00",
   "venue_state": "IA",
   "first_wallclock_raw": "2021-02-10T19:43:02.000Z",
   "residual_hours": {
    "UTC": -4.78,
    "EASTERN_TIME_MISLABELED_Z": 0.22,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 1.22
   }
  },
  {
   "game": "G-7e7e374dd85dddc3",
   "season": 2021,
   "tip_utc": "2021-02-11 01:00:00+00:00",
   "venue_state": "AL",
   "first_wallclock_raw": "2021-02-10T20:02:34.000Z",
   "residual_hours": {
    "UTC": -4.96,
    "EASTERN_TIME_MISLABELED_Z": 0.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 1.04
   }
  },
  {
   "game": "G-61f77a61ff362b4f",
   "season": 2021,
   "tip_utc": "2021-02-11 02:00:00+00:00",
   "venue_state": "MO",
   "first_wallclock_raw": "2021-02-10T21:06:31.000Z",
   "residual_hours": {
    "UTC": -4.89,
    "EASTERN_TIME_MISLABELED_Z": 0.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 1.11
   }
  }
 ],
 "PACIFIC": [
  {
   "game": "G-943074965c8cde33",
   "season": 2021,
   "tip_utc": "2021-02-07 03:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-02-06T22:05:39.000Z",
   "residual_hours": {
    "UTC": -4.91,
    "EASTERN_TIME_MISLABELED_Z": 0.09,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 3.09
   }
  },
  {
   "game": "G-00a83edc9d2f8d5c",
   "season": 2021,
   "tip_utc": "2021-02-07 21:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-02-07T16:02:34.000Z",
   "residual_hours": {
    "UTC": -4.96,
    "EASTERN_TIME_MISLABELED_Z": 0.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 3.04
   }
  },
  {
   "game": "G-bcf3e4403523418b",
   "season": 2021,
   "tip_utc": "2021-02-08 03:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-02-07T22:02:15.000Z",
   "residual_hours": {
    "UTC": -4.96,
    "EASTERN_TIME_MISLABELED_Z": 0.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 3.04
   }
  },
  {
   "game": "G-299674aa87df1210",
   "season": 2021,
   "tip_utc": "2021-02-10 22:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-02-10T17:06:23.000Z",
   "residual_hours": {
    "UTC": -4.89,
    "EASTERN_TIME_MISLABELED_Z": 0.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 3.11
   }
  },
  {
   "game": "G-3c1ec103cd8f8a36",
   "season": 2021,
   "tip_utc": "2021-02-11 23:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2021-02-11T18:03:05.000Z",
   "residual_hours": {
    "UTC": -4.95,
    "EASTERN_TIME_MISLABELED_Z": 0.05,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 3.05
   }
  }
 ],
 "MOUNTAIN_NO_DST": [
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
  },
  {
   "game": "G-5367cfe698a9d371",
   "season": 2021,
   "tip_utc": "2021-02-15 00:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-02-14T19:02:11.000Z",
   "residual_hours": {
    "UTC": -4.96,
    "EASTERN_TIME_MISLABELED_Z": 0.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.04
   }
  },
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
   "game": "G-d4b95ab6b9023b18",
   "season": 2021,
   "tip_utc": "2021-02-21 02:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2021-02-20T21:08:09.000Z",
   "residual_hours": {
    "UTC": -4.86,
    "EASTERN_TIME_MISLABELED_Z": 0.14,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.14
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
  },
  {
   "game": "G-5b96def39a6bff95",
   "season": 2022,
   "tip_utc": "2021-11-14 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2021-11-13T00:23:11.000Z",
   "residual_hours": {
    "UTC": -28.6,
    "EASTERN_TIME_MISLABELED_Z": -23.6,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -18.6
   }
  },
  {
   "game": "G-0cf33ed612b5c2ab",
   "season": 2022,
   "tip_utc": "2021-12-22 20:00:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2021-12-22T15:02:09.000Z",
   "residual_hours": {
    "UTC": -4.96,
    "EASTERN_TIME_MISLABELED_Z": 0.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.04
   }
  },
  {
   "game": "G-9f5b2b3271c66c1c",
   "season": 2022,
   "tip_utc": "2021-12-23 02:30:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2021-12-22T21:36:52.000Z",
   "residual_hours": {
    "UTC": -4.89,
    "EASTERN_TIME_MISLABELED_Z": 0.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.11
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
  }
 ]
}
```
