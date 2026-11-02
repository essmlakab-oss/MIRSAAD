# USCRN reference dataset

Source: NOAA/NCEI U.S. Climate Reference Network, product `hourly02`
(`www.ncei.noaa.gov/pub/data/uscrn/products/hourly02/`, open access). These are real ground-sensor
measurements, one text file per station and year, 38 columns, one row per hour.

The delivery contains 24 raw files (6 stations × 4 years) in `raw/`, one clean file
`data/uscrn_clean.parquet` (210,384 hourly rows, 23 columns, 35,064 hours per station, key
`time` + `station`), the correction log `data/uscrn_rapport_qualite.csv` (590 rows, per station,
year and column), and the scripts in `scripts/`. The target variable is `SOIL_MOISTURE_5`
(5 cm soil moisture, m³/m³), which is 97.4 % complete.

## Station selection: 115 candidates → 6 stations

| Filter | Stations left |
|---|---:|
| Full USCRN network | 159 |
| Present in all four years 2022–2025 | 156 |
| Outside Alaska and Hawaii, and in a climate group | 115 |
| At least 90 % `SOIL_MOISTURE_5` availability in 2025 | 46 |

`scripts/select_stations.py` ranks the 115 candidates (`scripts/uscrn_candidates.csv`). The final set
is one station per climate plus one twin for spatial consistency checks:

| Climate | Station | Lat / Lon | Rain per year | Mean air temp. | 5 cm moisture range (m³/m³) |
|---|---|---|---:|---:|---|
| Arid | `AZ_Tucson_11_W` | 32.24 / -111.17 | 223 mm | 22.1 °C | 0.012–0.275 |
| Semi-arid plains | `OK_Goodwell_2_SE` | 36.57 / -101.61 | 443 mm | 14.3 °C | 0.068–0.499 |
| Humid subtropical | `MS_Newton_5_ENE` | 32.34 / -89.07 | 1,439 mm | 17.8 °C | 0.054–0.409 |
| Cold continental | `NY_Ithaca_13_E` | 42.44 / -76.25 | 1,047 mm | 8.5 °C | 0.098–0.523 |
| Mediterranean | `CA_Fallbrook_5_NE` | 33.44 / -117.19 | 428 mm | 17.1 °C | 0.113–0.349 |
| Twin of Goodwell 2_SE, 3.8 km away | `OK_Goodwell_2_E` | 36.60 / -101.59 | 454 mm | 14.2 °C | 0.009–0.512 |

`AZ_Tucson_11_W` and `CA_Fallbrook_5_NE` measure soil moisture at 5 and 10 cm only.

## The 4-year check

The first selection only tested 2025. `scripts/verify_4years.py` re-tested the leading candidates on
all four years (`scripts/verif_4ans.csv`). Of 10 replacement candidates, 7 fall away in 2022, some at
0 %, so a 2025-only selection would have failed at training time. Decisions that came from it:

| Station | Finding | Decision |
|---|---|---|
| `AL_Selma_13_WNW` | Five-month outage, Sep 2023 to Feb 2024; 85.3 % average | Replaced by `MS_Newton_5_ENE` (100 %) |
| `AL_Selma_6_SSE` | No solar radiation or surface temperature at all | Dropped |
| `OK_Goodwell_2_E` | Outage Jan–Apr 2022 | Became the twin; `OK_Goodwell_2_SE` (98.9 %) became the primary |
| `AL_Clanton_2_NE`, `GA_Newton_8_W` | 0 % soil moisture in 2022 (and 2023 for Clanton) | Dropped |

Availability of `SOIL_MOISTURE_5` per partition:

| Station | Train 2022–23 | Validation 2024 | Test 2025 |
|---|---:|---:|---:|
| `CA_Fallbrook_5_NE` | 100.0 % | 100.0 % | 100.0 % |
| `MS_Newton_5_ENE` | 99.9 % | 100.0 % | 100.0 % |
| `OK_Goodwell_2_SE` | 99.2 % | 99.9 % | 97.4 % |
| `AZ_Tucson_11_W` | 99.1 % | 92.2 % | 100.0 % |
| `NY_Ithaca_13_E` | 96.5 % | 98.8 % | 100.0 % |
| `OK_Goodwell_2_E` (twin) | 83.9 % | 96.1 % | 96.9 % |

## The 7 cleaning steps

`scripts/clean_uscrn.py` applies them in this order and logs each correction:

| # | Step | Rule | Effect |
|---:|---|---|---|
| 1 | Parsing | Read with the 38 official NOAA column names | 24 files, 210,384 rows |
| 2 | Timestamp | Merge `UTC_DATE` + `UTC_TIME` into one UTC `time` column | One time key |
| 3 | Missing values | `-99` (soil), `-9999` (temperature, rain, humidity), `-99999` (radiation) become NaN | 463,194 values |
| 4 | NOAA flags | A non-zero flag invalidates its value; flag columns are then dropped | 420 values |
| 5 | Physical bounds | Soil 0–0.70 m³/m³; temperature -60–70 °C; radiation 0–1,600 W/m²; rain 0–200 mm/h; humidity 0–100 % | 7 values |
| 6 | Vertical consistency | A 5 cm jump above 0.10 with under 0.01 response at 10 cm and no rain over 3 h sets `flag_incoherence_verticale` | 0 cases |
| 7 | Continuity | Remove duplicates, reindex on a full hourly grid | 0 duplicates, 0 missing hours |

Nothing is interpolated and no row is dropped; only values are invalidated. Step 6 originally flagged
two rises at Goodwell 2_E that coincided with 11.4 and 11.7 mm of rain, which is why the no-rain
condition exists.

## QC findings

**Frozen-soil gaps.** Gaps at cold stations are caused by frozen soil, not hardware faults. The
dielectric probe measures liquid water and stops reporting when the soil freezes:

| Station | Missing hours | With soil below 1 °C | Share |
|---|---:|---:|---:|
| `NY_Ithaca_13_E` | 727 | 701 | 96 % |
| `OK_Goodwell_2_SE` | 376 | 324 | 86 % |
| `OK_Goodwell_2_E` | 3,432 | 698 | 20 % |
| `AZ_Tucson_11_W` | 841 | 0 | 0 % |

These hours stay empty. Detection therefore has a documented winter blind spot at cold stations.

**Twin offset.** The Goodwell stations correlate at 0.907 in level, but 2_SE reads on average
**0.056 m³/m³** higher than 2_E, in 95.3 % of hours. Hourly changes correlate at only 0.415.

**Spatial threshold.** After removing the offset, the mean twin gap drops from 0.058 to 0.025 m³/m³
and its 99th percentile from 0.137 to **0.095 m³/m³**. That percentile is the alert threshold of the
spatial check.

## Train / validation / test split

| Partition | Years | Purpose |
|---|---|---|
| Train | 2022–2023 | Fitting and calibration |
| Validation | 2024 | Development and threshold selection |
| Test | 2025 | Held-out evaluation |

`time` is UTC and marks the **end** of the measured hour, from 2022-01-01 01:00 to 2026-01-01 00:00.
A row belongs to the partition containing `time - 1 hour`, so the 2026-01-01 00:00 row is test data.

## Usage rules

1. Do not interpolate the gaps at cold stations; they are frozen soil, where the measurement has no
   meaning.
2. Remove the 0.056 m³/m³ offset before comparing the two Goodwell stations.
3. Apply the 0.095 m³/m³ spatial threshold to offset-corrected levels, not to hourly changes.
4. Account for the end-of-hour time convention when aligning with other sources such as ERA5.
5. At Tucson and Fallbrook, vertical consistency is limited to the 5 cm / 10 cm comparison.
6. Use the station coordinates above to select grid cells in external sources.
