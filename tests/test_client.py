"""The client against a fake HTTP session: URLs, retries, provider lookup, window splitting."""
from __future__ import annotations

import pandas as pd
import pytest

from rebase_grid_api import GridAPI, GridAPIError
from rebase_grid_api import client as client_module


class FakeResponse:
    def __init__(self, status: int, body: dict, headers: dict | None = None) -> None:
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = str(body)

    def json(self) -> dict:
        return self._body


class FakeSession:
    """Answers each GET from `handler(path, params)` and records the calls."""

    def __init__(self, handler) -> None:
        self.handler, self.calls, self.headers = handler, [], {}

    def get(self, url: str, params=None, timeout=None):
        path = url.split("/api/v1", 1)[1]
        self.calls.append((path, dict(params or {})))
        return self.handler(path, dict(params or {}))


def point(t: str, v: float) -> dict:
    return {"valid_time": t, "value": v, "knowledge_time": t, "annotation": None}


VALUES = "/entities/DE-LU/timeseries/price.x.y/values"


def test_values_returns_a_utc_series_and_sends_utc_instants():
    session = FakeSession(lambda path, params: FakeResponse(200, {"points": [
        point("2026-11-09T00:00:00Z", 230.77), point("2026-11-09T00:15:00Z", 228.1)]}))
    series = GridAPI(session=session).values("DE-LU", "price.x.y", "2026-11-09", "2026-11-10",
                                             provider="entsoe")
    assert list(series) == [230.77, 228.1] and series.name == "price.x.y"
    assert str(series.index.tz) == "UTC" and series.index[0] == pd.Timestamp("2026-11-09T00:00Z")
    path, params = session.calls[0]
    assert path == VALUES
    assert params == {"provider": "entsoe", "start": "2026-11-09T00:00:00Z", "end": "2026-11-10T00:00:00Z"}


def test_the_key_is_sent_as_a_header_only_when_given():
    assert "X-API-Key" not in GridAPI(session=FakeSession(None)).session.headers
    assert GridAPI("k", session=FakeSession(None)).session.headers["X-API-Key"] == "k"


def test_a_rate_limited_request_waits_retry_after_and_repeats(monkeypatch):
    waits = []
    monkeypatch.setattr(client_module.time, "sleep", waits.append)
    answers = iter([FakeResponse(429, {"error": "rate_limited"}, {"Retry-After": "6"}),
                    FakeResponse(200, {"entities": [{"id": "Sweden"}]})])
    grid = GridAPI(session=FakeSession(lambda path, params: next(answers)))
    assert grid.entities() == [{"id": "Sweden"}] and waits == [6.0]


def test_an_error_carries_the_status_code_and_body():
    grid = GridAPI(session=FakeSession(lambda path, params: FakeResponse(
        404, {"error": "not_found", "message": "no such entity", "closest": ["DE-LU"]})))
    with pytest.raises(GridAPIError) as err:
        grid.entity("DE-LX")
    assert err.value.status == 404 and err.value.code == "not_found"
    assert err.value.body["closest"] == ["DE-LU"]


def test_the_provider_is_looked_up_when_only_one_publishes_the_series():
    def handler(path, params):
        if "provider" not in params:
            return FakeResponse(400, {"error": "bad_request", "param": "provider", "providers": ["entsoe"]})
        return FakeResponse(200, {"points": [point("2026-11-09T00:00:00Z", 1.0)]})

    session = FakeSession(handler)
    assert len(GridAPI(session=session).values("DE-LU", "price.x.y", "2026-11-09", "2026-11-10")) == 1
    assert session.calls[-1][1]["provider"] == "entsoe"


def test_several_providers_ask_the_caller_to_choose():
    grid = GridAPI(session=FakeSession(lambda path, params: FakeResponse(
        400, {"error": "bad_request", "param": "provider", "providers": ["entsoe", "smard"]})))
    with pytest.raises(ValueError, match="entsoe.*smard"):
        grid.values("DE-LU", "price.x.y", "2026-11-09", "2026-11-10")


def test_a_window_too_long_for_one_request_is_split_and_joined():
    def handler(path, params):
        start, end = pd.Timestamp(params["start"]), pd.Timestamp(params["end"])
        if end - start > pd.Timedelta(days=400):
            return FakeResponse(400, {"error": "bad_request", "param": "end"})
        # one point at each end: the shared boundary point must appear once in the result
        return FakeResponse(200, {"points": [point(params["start"], 1.0), point(params["end"], 2.0)]})

    session = FakeSession(handler)
    series = GridAPI(session=session).values("DE-LU", "price.x.y", "2024-01-01", "2026-01-01",
                                             provider="entsoe")
    assert len(session.calls) == 3                          # the refused one, then two halves
    assert series.index.is_unique and series.index.is_monotonic_increasing and len(series) == 3


def test_search_follows_every_page():
    pages = {None: {"items": [{"name": "a"}], "next": "2"}, "2": {"items": [{"name": "b"}], "next": None}}
    session = FakeSession(lambda path, params: FakeResponse(200, pages[params.get("cursor")]))
    assert list(GridAPI(session=session).search("DE-LU", q="price")["name"]) == ["a", "b"]


@pytest.mark.live
def test_live_day_ahead_prices_without_a_key():
    series = GridAPI().values("DE-LU", "price.day_ahead.day_ahead_auction_price",
                              "2026-11-09", "2026-11-10")
    assert len(series) > 20 and series.notna().all()
