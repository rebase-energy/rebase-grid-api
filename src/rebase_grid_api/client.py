"""A small client for the Rebase Grid API (https://grid.rebase.energy/docs/v1).

No API key is needed: without one the API answers 30 requests per minute. A free key raises the
limit to 240 per minute.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests

BASE_URL = "https://grid.rebase.energy/api/v1"
SIGN_UP_URL = "https://rebase-energy.notion.site/3798f95cc39b802f8672eb93c6ab77c5?pvs=105"

__all__ = ["GridAPI", "GridAPIError", "BASE_URL"]


class GridAPIError(Exception):
    """The API answered with an error. `status` is the HTTP status, `code` the API's error code
    (`bad_request`, `not_found`, `rate_limited`, ...), `body` the whole JSON answer."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.code = body.get("error", "")
        self.body = body
        super().__init__(f"{status} {self.code}: {body.get('message', '')}")


def _instant(value: str | date | datetime) -> str:
    """Any date, datetime or ISO string as the UTC instant the API takes (`2026-11-09T00:00:00Z`).
    A value without a time zone is read as UTC."""
    ts = pd.Timestamp(value)
    ts = ts.tz_localize(timezone.utc) if ts.tzinfo is None else ts.tz_convert(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


class GridAPI:
    """Read European power system data: prices, load, generation, forecasts, flows, frequency.

        grid = GridAPI()                       # no key needed
        prices = grid.values("DE-LU", "price.day_ahead.day_ahead_auction_price",
                             start="2026-11-01", end="2026-11-08")

    `api_key` is optional. `max_retries` is how many times a rate-limited (429) or temporarily
    unavailable (503) request is repeated after the wait the API asks for.
    """

    def __init__(self, api_key: str | None = None, *, base_url: str = BASE_URL,
                 max_retries: int = 5, timeout: float = 60.0,
                 session: requests.Session | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "rebase-grid-api-python")
        if api_key:
            self.session.headers["X-API-Key"] = api_key

    # -- transport ---------------------------------------------------------------------------

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        """GET `path` (e.g. `/entities`) and return the JSON answer. Waits and repeats on 429 and
        503; raises `GridAPIError` on any other error."""
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(self.max_retries + 1):
            response = self.session.get(self.base_url + path, params=params, timeout=self.timeout)
            if response.status_code in (429, 503) and attempt < self.max_retries:
                time.sleep(float(response.headers.get("Retry-After", 5)))
                continue
            try:
                body = response.json()
            except ValueError:
                body = {"error": "invalid_response", "message": response.text[:200]}
            if response.status_code >= 400:
                raise GridAPIError(response.status_code, body)
            return body
        raise AssertionError("unreachable")

    # -- discover ----------------------------------------------------------------------------

    def entities(self) -> list[dict[str, Any]]:
        """The entity tree: countries with their price zones, areas and substations."""
        return self.get("/entities")["entities"]

    def entity(self, entity: str) -> dict[str, Any]:
        """One entity with its parent, children and relations."""
        return self.get(f"/entities/{entity}")

    def timeseries(self, entity: str) -> pd.DataFrame:
        """Every timeseries on an entity: one row per (name, provider)."""
        return pd.DataFrame(self.get(f"/entities/{entity}/timeseries")["timeseries"])

    def search(self, entity: str | None = None, *, q: str | None = None, category: str | None = None,
               provider: str | None = None, data_type: str | None = None) -> pd.DataFrame:
        """Search timeseries by keyword, category, provider or data type. Follows every page."""
        items: list[dict[str, Any]] = []
        cursor = None
        while True:
            page = self.get("/timeseries", entity=entity, q=q, category=category, provider=provider,
                            data_type=data_type, limit=1000, cursor=cursor)
            items += page["items"]
            cursor = page.get("next")
            if not cursor:
                return pd.DataFrame(items)

    def metadata(self, entity: str, name: str) -> dict[str, Any]:
        """What a timeseries measures, its unit, and per provider its resolution and extent."""
        return self.get(f"/entities/{entity}/timeseries/{name}")

    # -- read --------------------------------------------------------------------------------

    def values(self, entity: str, name: str, start: str | date | datetime,
               end: str | date | datetime, *, provider: str | None = None,
               as_of: str | date | datetime | None = None) -> pd.Series:
        """The values of one timeseries between `start` and `end`, as a pandas Series indexed by
        UTC time. Dates without a time zone are read as UTC.

        `provider` may be left out when only one organisation publishes the series; when several
        do, the error names them. A range too long for one request is split automatically.
        `as_of` returns the values as they were known at that instant.
        """
        t0, t1 = pd.Timestamp(_instant(start)), pd.Timestamp(_instant(end))
        as_of = _instant(as_of) if as_of is not None else None
        if provider is None:
            provider = self._only_provider(entity, name, t0)
        points = self._points(entity, name, provider, t0, t1, as_of)
        index = pd.DatetimeIndex(pd.to_datetime([p["valid_time"] for p in points], utc=True),
                                 name="valid_time")
        series = pd.Series([p["value"] for p in points], index=index, name=name, dtype="float64")
        return series[~series.index.duplicated(keep="last")]

    def frequency(self) -> dict[str, Any]:
        """The latest grid frequency samples of each synchronous area."""
        return self.get("/views/frequency")

    # -- internals ---------------------------------------------------------------------------

    def _only_provider(self, entity: str, name: str, t0: pd.Timestamp) -> str:
        """Ask the API which providers publish the series; it lists them when none is given."""
        path = f"/entities/{entity}/timeseries/{name}/values"
        try:
            self.get(path, start=_instant(t0), end=_instant(t0))
        except GridAPIError as exc:
            providers = exc.body.get("providers")
            if exc.body.get("param") != "provider" or not providers:
                raise
            if len(providers) == 1:
                return providers[0]
            raise ValueError(f"{name} on {entity} is published by {providers}: pass provider=") from None
        raise ValueError("pass provider=")

    def _points(self, entity: str, name: str, provider: str, t0: pd.Timestamp, t1: pd.Timestamp,
                as_of: str | None) -> list[dict[str, Any]]:
        """One request, or two halves when the API says the window holds too many points."""
        try:
            return self.get(f"/entities/{entity}/timeseries/{name}/values", provider=provider,
                            start=_instant(t0), end=_instant(t1), as_of=as_of)["points"]
        except GridAPIError as exc:
            too_long = exc.status == 400 and exc.body.get("param") == "end"
            if not too_long or t1 - t0 <= timedelta(hours=1):
                raise
        middle = (t0 + (t1 - t0) / 2).floor("h")
        return (self._points(entity, name, provider, t0, middle, as_of)
                + self._points(entity, name, provider, middle, t1, as_of))
