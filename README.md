# Rebase Grid API

Open REST API for European power system and electricity market data: day-ahead prices, load,
generation by type, wind and solar forecasts, cross-border flows, balancing and grid frequency.
The data comes from ENTSO-E, TSOs and exchanges, in one schema.

**No API key needed.** Every endpoint answers without a key at 10 requests per minute. A free key
raises the limit to 240 per minute.

- Base URL: `https://grid.rebase.energy/api/v1`
- Docs: https://grid.rebase.energy/docs/v1
- For AI agents: https://grid.rebase.energy/llms.txt
- About: https://rebase.energy/grid-API

## Try it

Day-ahead prices for Germany-Luxembourg, one day. This works as it is:

```bash
curl "https://grid.rebase.energy/api/v1/entities/DE-LU/timeseries/price.day_ahead.day_ahead_auction_price/values?provider=entsoe&start=2026-11-09T00:00:00Z&end=2026-11-10T00:00:00Z"
```

```json
{
  "entity": "DE-LU",
  "name": "price.day_ahead.day_ahead_auction_price",
  "provider": "entsoe",
  "unit": "EUR/MWh",
  "resolution": "PT15M",
  "points": [
    {"valid_time": "2026-11-09T00:00:00Z", "value": 230.77, "knowledge_time": "2026-11-08T13:34:47.811427Z", "annotation": null}
  ]
}
```

## Python

No client library is needed, only `requests`:

```python
import pandas as pd
import requests

API = "https://grid.rebase.energy/api/v1"


def read(entity: str, name: str, start: str, end: str, provider: str = "entsoe") -> pd.Series:
    """One timeseries on one entity, as a pandas Series indexed by UTC time."""
    r = requests.get(
        f"{API}/entities/{entity}/timeseries/{name}/values",
        params={"provider": provider, "start": start, "end": end},
    )
    r.raise_for_status()
    points = r.json()["points"]
    index = pd.to_datetime([p["valid_time"] for p in points])
    return pd.Series([p["value"] for p in points], index=index, name=name)


prices = read("DE-LU", "price.day_ahead.day_ahead_auction_price",
              "2026-11-01T00:00:00Z", "2026-11-08T00:00:00Z")
```

## What to ask for

Every call has the same shape:
`GET /entities/{entity}/timeseries/{name}/values?provider=…&start=…&end=…`

| Data | Entity | Timeseries name | Provider |
|---|---|---|---|
| Day-ahead price | `DE-LU` | `price.day_ahead.day_ahead_auction_price` | `entsoe` |
| Load (consumption) | `DE-LU` | `consumption.realised_load.total_load` | `entsoe` |
| Day-ahead load forecast | `DE-LU` | `consumption.load_forecast.day_ahead_total_load_forecast` | `entsoe` |
| Generation by type | `DE-LU` | `production.realised_output.actual_generation_per_type.wind_onshore` (also `solar`, `nuclear`, `fossil_gas`, …) | `entsoe` |
| Solar forecast | `DE-LU` | `production.forecast.solar_generation_forecast.day_ahead` | `entsoe` |
| Wind forecast | `DE-LU` | `production.forecast.wind_generation_forecast.onshore_day_ahead` | `entsoe` |
| Cross-border physical flow | `border:FI~SE-SE3` | `exchange.physical_flow.physical_flow` | `entsoe` |
| Grid frequency (live) | — | `GET /views/frequency` | — |

Price zones use ids such as `DE-LU`, `FR`, `SE-SE3`, `NO-NO1`, `DK-DK1`. A name is the same in
every zone.

## Find more

```bash
# every entity: countries, price zones, borders, plants
curl "https://grid.rebase.energy/api/v1/entities"

# every timeseries on one entity, with its providers
curl "https://grid.rebase.energy/api/v1/entities/DE-LU/timeseries"

# search by keyword
curl "https://grid.rebase.energy/api/v1/timeseries?entity=SE-SE3&category=price&q=day-ahead"
```

## Limits

| | Without a key | With a free key |
|---|---|---|
| Requests | 10 per minute per IP address | 240 per minute |
| Data | all | all |
| Points per request | 100,000 | 100,000 |

When the limit is spent the API answers `429` with `Retry-After` and a message that says how to
get a key. Request one here:
https://rebase-energy.notion.site/3798f95cc39b802f8672eb93c6ab77c5?pvs=105
and send it in the `X-API-Key` header.

## What you get

- Values as the provider published them: not filled, smoothed or estimated.
- Every value carries the time it became known (`knowledge_time`), and revisions are kept as
  versions. `as_of` returns the data as it was known at any past instant.
- Each response names the publisher's licence and the credit line to show.
