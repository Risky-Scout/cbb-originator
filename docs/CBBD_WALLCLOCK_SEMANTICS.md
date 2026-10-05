# CBBD PBP wallclock semantics (Stage 2L)

Rendered from `data/qa/cbbd_wallclock_semantics.json` by `cbb_platform/wallclock_audit.py`. Do not edit by hand.

## Classification: **MIXED**

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
   "game": "G-2586225d69d7b56e",
   "season": 2021,
   "tip_utc": "2021-01-18 20:00:00+00:00",
   "venue_state": "AL",
   "first_wallclock_raw": "2021-01-18T20:02:42.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.04
   }
  },
  {
   "game": "G-a7bc55ab1101eac9",
   "season": 2021,
   "tip_utc": "2021-01-26 00:00:00+00:00",
   "venue_state": "IL",
   "first_wallclock_raw": "2021-01-26T00:06:49.000Z",
   "residual_hours": {
    "UTC": 0.11,
    "EASTERN_TIME_MISLABELED_Z": 5.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.11
   }
  },
  {
   "game": "G-94a9ce5bae69aebe",
   "season": 2021,
   "tip_utc": "2021-01-28 01:00:00+00:00",
   "venue_state": "LA",
   "first_wallclock_raw": "2021-01-28T01:02:11.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.04
   }
  },
  {
   "game": "G-f9ea4fe7fb3cced4",
   "season": 2021,
   "tip_utc": "2021-01-28 01:30:00+00:00",
   "venue_state": "AR",
   "first_wallclock_raw": "2021-01-28T01:36:15.000Z",
   "residual_hours": {
    "UTC": 0.1,
    "EASTERN_TIME_MISLABELED_Z": 5.1,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 6.1
   }
  },
  {
   "game": "G-b0156c896a8fde81",
   "season": 2022,
   "tip_utc": "2022-01-28 01:00:00+00:00",
   "venue_state": "MN",
   "first_wallclock_raw": "2022-01-27T20:02:22.000Z",
   "residual_hours": {
    "UTC": -4.96,
    "EASTERN_TIME_MISLABELED_Z": 0.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 1.04
   }
  }
 ],
 "EASTERN": [
  {
   "game": "G-90e4e36c6ce2c070",
   "season": 2021,
   "tip_utc": "2021-01-21 00:00:00+00:00",
   "venue_state": "VA",
   "first_wallclock_raw": "2021-01-21T00:02:12.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.04
   }
  },
  {
   "game": "G-281c6f290b0f34ac",
   "season": 2021,
   "tip_utc": "2021-01-22 23:00:00+00:00",
   "venue_state": "NC",
   "first_wallclock_raw": "2021-01-22T23:02:05.000Z",
   "residual_hours": {
    "UTC": 0.03,
    "EASTERN_TIME_MISLABELED_Z": 5.03,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.03
   }
  },
  {
   "game": "G-5f9d98546b7277b7",
   "season": 2021,
   "tip_utc": "2021-01-23 19:00:00+00:00",
   "venue_state": "NC",
   "first_wallclock_raw": "2021-01-23T19:02:42.000Z",
   "residual_hours": {
    "UTC": 0.04,
    "EASTERN_TIME_MISLABELED_Z": 5.04,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.04
   }
  },
  {
   "game": "G-05e8de596c8da439",
   "season": 2021,
   "tip_utc": "2021-01-23 19:00:00+00:00",
   "venue_state": "NH",
   "first_wallclock_raw": "2021-01-23T19:04:17.000Z",
   "residual_hours": {
    "UTC": 0.07,
    "EASTERN_TIME_MISLABELED_Z": 5.07,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.07
   }
  },
  {
   "game": "G-e0083e523caea795",
   "season": 2021,
   "tip_utc": "2021-01-23 21:00:00+00:00",
   "venue_state": "PA",
   "first_wallclock_raw": "2021-01-23T21:04:15.000Z",
   "residual_hours": {
    "UTC": 0.07,
    "EASTERN_TIME_MISLABELED_Z": 5.07,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.07
   }
  }
 ],
 "PACIFIC": [
  {
   "game": "G-80d6804be0c3d731",
   "season": 2021,
   "tip_utc": "2021-01-22 03:00:00+00:00",
   "venue_state": "WA",
   "first_wallclock_raw": "2021-01-22T03:07:27.000Z",
   "residual_hours": {
    "UTC": 0.12,
    "EASTERN_TIME_MISLABELED_Z": 5.12,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.12
   }
  },
  {
   "game": "G-8fdeaa617510026b",
   "season": 2021,
   "tip_utc": "2021-01-26 02:00:00+00:00",
   "venue_state": "NV",
   "first_wallclock_raw": "2021-01-26T02:12:36.000Z",
   "residual_hours": {
    "UTC": 0.21,
    "EASTERN_TIME_MISLABELED_Z": 5.21,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 8.21
   }
  },
  {
   "game": "G-3d43e88b104d523e",
   "season": 2022,
   "tip_utc": "2022-01-28 02:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2022-01-27T21:04:21.000Z",
   "residual_hours": {
    "UTC": -4.93,
    "EASTERN_TIME_MISLABELED_Z": 0.07,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 3.07
   }
  },
  {
   "game": "G-b59df6da800830f5",
   "season": 2022,
   "tip_utc": "2022-01-28 03:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2022-01-27T22:08:33.000Z",
   "residual_hours": {
    "UTC": -4.86,
    "EASTERN_TIME_MISLABELED_Z": 0.14,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 3.14
   }
  },
  {
   "game": "G-452affec414dd71d",
   "season": 2022,
   "tip_utc": "2022-01-30 01:00:00+00:00",
   "venue_state": "CA",
   "first_wallclock_raw": "2022-01-29T20:31:36.000Z",
   "residual_hours": {
    "UTC": -4.47,
    "EASTERN_TIME_MISLABELED_Z": 0.53,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 3.53
   }
  }
 ],
 "MOUNTAIN": [
  {
   "game": "G-87ba129eb5e69556",
   "season": 2021,
   "tip_utc": "2021-01-24 03:00:00+00:00",
   "venue_state": "UT",
   "first_wallclock_raw": "2021-01-24T03:12:12.000Z",
   "residual_hours": {
    "UTC": 0.2,
    "EASTERN_TIME_MISLABELED_Z": 5.2,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 7.2
   }
  },
  {
   "game": "G-0f4a2f9a1cfb6641",
   "season": 2022,
   "tip_utc": "2022-01-28 02:00:00+00:00",
   "venue_state": "MT",
   "first_wallclock_raw": "2022-01-27T21:01:32.000Z",
   "residual_hours": {
    "UTC": -4.97,
    "EASTERN_TIME_MISLABELED_Z": 0.03,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.03
   }
  },
  {
   "game": "G-ed036be6dc396415",
   "season": 2024,
   "tip_utc": "2023-12-03 02:00:00+00:00",
   "venue_state": "NM",
   "first_wallclock_raw": "2023-12-02T21:08:41.000Z",
   "residual_hours": {
    "UTC": -4.86,
    "EASTERN_TIME_MISLABELED_Z": 0.14,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.14
   }
  },
  {
   "game": "G-d879ba6533da93d3",
   "season": 2024,
   "tip_utc": "2023-12-30 02:00:00+00:00",
   "venue_state": "NM",
   "first_wallclock_raw": "2023-12-29T21:04:29.000Z",
   "residual_hours": {
    "UTC": -4.93,
    "EASTERN_TIME_MISLABELED_Z": 0.07,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.07
   }
  },
  {
   "game": "G-ca240a0309bdab5d",
   "season": 2021,
   "tip_utc": "2021-02-28 03:00:00+00:00",
   "venue_state": "CO",
   "first_wallclock_raw": "2021-02-27T22:06:35.000Z",
   "residual_hours": {
    "UTC": -4.89,
    "EASTERN_TIME_MISLABELED_Z": 0.11,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.11
   }
  }
 ],
 "MOUNTAIN_NO_DST": [
  {
   "game": "G-7980aedac606688d",
   "season": 2024,
   "tip_utc": "2023-12-21 01:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2023-12-20T20:07:53.000Z",
   "residual_hours": {
    "UTC": -4.87,
    "EASTERN_TIME_MISLABELED_Z": 0.13,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.13
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
   "game": "G-97fc0e70a0ba8685",
   "season": 2025,
   "tip_utc": "2024-12-07 19:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2024-12-07T14:10:34.000Z",
   "residual_hours": {
    "UTC": -4.82,
    "EASTERN_TIME_MISLABELED_Z": 0.18,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 2.18
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
   "game": "G-e218aedecbadaab4",
   "season": 2025,
   "tip_utc": "2025-02-15 19:00:00+00:00",
   "venue_state": "AZ",
   "first_wallclock_raw": "2025-02-16T00:01:07.000Z",
   "residual_hours": {
    "UTC": 5.02,
    "EASTERN_TIME_MISLABELED_Z": 10.02,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 12.02
   }
  }
 ],
 "HAWAII": [
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
   "game": "G-131d5f3eac622db3",
   "season": 2023,
   "tip_utc": "2022-11-15 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2022-11-14T00:08:30.000Z",
   "residual_hours": {
    "UTC": -28.84,
    "EASTERN_TIME_MISLABELED_Z": -23.84,
    "LOCAL_VENUE_TIME_MISLABELED_Z": -18.84
   }
  },
  {
   "game": "G-2eaaf93adc44c229",
   "season": 2024,
   "tip_utc": "2023-11-21 19:30:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2023-11-21T14:34:46.000Z",
   "residual_hours": {
    "UTC": -4.92,
    "EASTERN_TIME_MISLABELED_Z": 0.08,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.08
   }
  },
  {
   "game": "G-4cbe7c7ae37b3b12",
   "season": 2024,
   "tip_utc": "2024-01-14 04:59:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2024-01-14T00:05:55.000Z",
   "residual_hours": {
    "UTC": -4.88,
    "EASTERN_TIME_MISLABELED_Z": 0.12,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.12
   }
  },
  {
   "game": "G-0bf24f161c46d146",
   "season": 2025,
   "tip_utc": "2024-12-26 02:45:00+00:00",
   "venue_state": "HI",
   "first_wallclock_raw": "2024-12-25T21:47:52.000Z",
   "residual_hours": {
    "UTC": -4.95,
    "EASTERN_TIME_MISLABELED_Z": 0.05,
    "LOCAL_VENUE_TIME_MISLABELED_Z": 5.05
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
  }
 ]
}
```
