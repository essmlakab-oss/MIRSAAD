# ERA5 reference data

`era5_uscrn_hourly_2022_2025.csv` holds Copernicus ERA5 reanalysis values at the grid cell nearest each
USCRN station, 2022–2025, hourly. ERA5 is an official challenge dataset. The signal-processing module
uses it as an optional reference the sensor attacker does not control.

| Property | Value |
|---|---|
| SHA-256 | `fbabd0b0a777cc0a39c41bcbc4f17afc5516c218c8b10f03cb54745387fd0ad0` |
| Shape | 175,320 rows, 35,064 per series, no missing values, no duplicate keys |
| Time range | `2022-01-01 00:00` to `2025-12-31 23:00` |
| Columns | `time`, `station`, `lat_grille`, `lon_grille`, `t2m`, `d2m`, `tp_mm`, `ssrd_wm2`, `swvl1`, `stl1` |

`.gitattributes` keeps the file's CRLF line endings exact, so the hash holds on every platform.

## Station mapping

There is one series per grid cell, so the two Goodwell stations, 3.8 km apart, share one series:

| USCRN station | ERA5 series | Grid cell (lat, lon) |
|---|---|---|
| `AZ_Tucson_11_W` | `AZ_Tucson_11_W` | 32.25, -111.25 |
| `CA_Fallbrook_5_NE` | `CA_Fallbrook_5_NE` | 33.50, -117.25 |
| `MS_Newton_5_ENE` | `MS_Newton_5_ENE` | 32.25, -89.00 |
| `NY_Ithaca_13_E` | `NY_Ithaca_13_E` | 42.50, -76.25 |
| `OK_Goodwell_2_E` | `OK_Goodwell` | 36.50, -101.50 |
| `OK_Goodwell_2_SE` | `OK_Goodwell` | 36.50, -101.50 |

ERA5 therefore cannot separate the two Goodwell stations. The linear mapping and scales are still
fitted per USCRN station.

## Time convention

USCRN `time` marks the end of the measured hour. We correlated USCRN against the file at shifts of
-2 to +2 hours. Both the solar pair (`SOLARAD` / `ssrd_wm2`) and the air-temperature pair
(`T_HR_AVG` / `t2m`) peak at shift 0 at all six stations, and the solar peak is symmetric:

| Shift added to ERA5 `time` | -1 h | 0 | +1 h |
|---|---:|---:|---:|
| Solar correlation, range over stations | 0.90–0.93 | **0.95–0.98** | 0.91–0.93 |

So **no shift is applied**. Equal stamps are joined directly, recorded as `time_shift_hours: 0`.

The USCRN series starts at `2022-01-01 01:00` and ends at `2026-01-01 00:00`, so its final row has no
ERA5 value. The ERA5 features there are NaN. The extra ERA5 row at `2022-01-01 00:00` is only outside
USCRN's range and is not used for any row value.

## Limits

- **Publication latency: 120 h.** ECMWF publishes ERA5 daily about 5 days behind real time (the
  preliminary ERA5T stream), and the validated final product follows 2–3 months behind. Scores using
  ERA5 therefore carry `decision_delay_h: 120` and form the delayed-confirmation mode, not real-time
  detection. ERA5 also assimilates observations around each analysis time, so even a stamp at or before
  t contains information from slightly after t. The file does not say which stream it came from; if it
  is the final product, ERA5T values available at 120 h could differ slightly.
- **Weak local agreement in dry climates.** Correlation of `SOIL_MOISTURE_5` with `swvl1` is 0.50–0.60
  at Tucson and Goodwell, and 0.84–0.88 at Fallbrook, Newton and Ithaca. `swvl1` also covers a 7 cm
  layer of a roughly 25 km cell, not a 5 cm point.
- **Negative `swvl1`.** 200 Tucson hours have slightly negative `swvl1` (minimum -0.004). The values are
  used unchanged, since they were present in the file as received.
- **Rain.** ERA5 rain is a smooth areal estimate, so a real local storm also raises
  `sp_era5_rain_mismatch`. Rain totals differ from USCRN, for example 173 against 223 mm per year at
  Tucson and 1,260 against 1,047 mm at Ithaca.

Latency sources: [ERA5 hourly data, Copernicus Climate Data Store](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels);
[Copernicus announcement of ERA5T](https://climate.copernicus.eu/key-update-climate-dataset-brings-data-five-days-behind-real-time).
