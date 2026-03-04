"""
Unit tests: searching with location="Seattle" returns Seattle job results.

All outbound HTTP calls and the DuckDuckGo thread are mocked — no real
network access is required.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.job_searcher import JobSearcher


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_response(payload: dict) -> MagicMock:
    """Minimal mock that looks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


ARBEITNOW_SEATTLE_RESPONSE = {
    "data": [
        {
            "title": "Software Engineer",
            "company_name": "Acme Corp",
            "location": "Seattle, WA",
            "description": "<p>Great role in Seattle.</p>",
            "url": "https://www.arbeitnow.com/jobs/1",
            "remote": False,
            "tags": ["python"],
            "created_at": 1700000000,
        },
        {
            "title": "Data Scientist",
            "company_name": "WidgetCo",
            "location": "Seattle, WA",
            "description": "<p>Data science position in Seattle.</p>",
            "url": "https://www.arbeitnow.com/jobs/2",
            "remote": False,
            "tags": ["python", "ml"],
            "created_at": 1700000001,
        },
    ]
}


async def _no_ddg_results(func, *args, **kwargs):
    """Replacement for asyncio.to_thread that skips real DDG calls."""
    return []


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestSeattleLocation:
    """Verify that entering 'Seattle' as location surfaces Seattle results."""

    @pytest.fixture
    def searcher(self):
        return JobSearcher()

    # ------------------------------------------------------------------
    # 1. Results are returned when location="Seattle"
    # ------------------------------------------------------------------

    def test_seattle_returns_results(self, searcher):
        """search_all with location='Seattle' must return at least one job."""
        mock_get = AsyncMock(return_value=_make_response(ARBEITNOW_SEATTLE_RESPONSE))

        with patch("httpx.AsyncClient.get", mock_get), \
             patch("asyncio.to_thread", side_effect=_no_ddg_results):
            jobs = asyncio.get_event_loop().run_until_complete(
                searcher.search_all(
                    queries=["software engineer"],
                    location="Seattle",
                    country="US",
                    job_type="any",
                )
            )

        assert len(jobs) > 0, (
            "Expected job results for Seattle but got an empty list. "
            "Check that the location parameter is forwarded to job-board API calls."
        )

    # ------------------------------------------------------------------
    # 2. 'Seattle' is forwarded to the Arbeitnow API as the `location` param
    # ------------------------------------------------------------------

    def test_seattle_city_sent_to_arbeitnow(self, searcher):
        """Arbeitnow must receive a `location` param that contains 'Seattle'."""
        captured: dict = {}

        async def _side_effect(url, **kwargs):
            if "arbeitnow" in url:
                captured.update(kwargs.get("params", {}))
            return _make_response(ARBEITNOW_SEATTLE_RESPONSE)

        mock_get = AsyncMock(side_effect=_side_effect)

        with patch("httpx.AsyncClient.get", mock_get), \
             patch("asyncio.to_thread", side_effect=_no_ddg_results):
            asyncio.get_event_loop().run_until_complete(
                searcher.search_all(
                    queries=["software engineer"],
                    location="Seattle",
                    country="",
                    job_type="any",
                )
            )

        assert "location" in captured, (
            "Expected a 'location' param to be sent to Arbeitnow when city='Seattle'."
        )
        assert "Seattle" in captured["location"], (
            f"Arbeitnow 'location' param should contain 'Seattle', got: {captured['location']!r}"
        )

    # ------------------------------------------------------------------
    # 3. Returned jobs carry the Seattle location from the API response
    # ------------------------------------------------------------------

    def test_returned_jobs_have_seattle_location(self, searcher):
        """Every job returned should include 'Seattle' in its location field."""
        mock_get = AsyncMock(return_value=_make_response(ARBEITNOW_SEATTLE_RESPONSE))

        with patch("httpx.AsyncClient.get", mock_get), \
             patch("asyncio.to_thread", side_effect=_no_ddg_results):
            jobs = asyncio.get_event_loop().run_until_complete(
                searcher.search_all(
                    queries=["software engineer"],
                    location="Seattle",
                    country="",
                    job_type="any",
                )
            )

        assert jobs, "No jobs returned — cannot validate location fields."
        for job in jobs:
            assert "Seattle" in job.get("location", ""), (
                f"Job location should contain 'Seattle', got: {job.get('location')!r}"
            )

    # ------------------------------------------------------------------
    # 4. Remotive is excluded when a specific city is provided
    # ------------------------------------------------------------------

    def test_remotive_excluded_when_city_provided(self, searcher):
        """
        Remotive is remote-only with no location API.
        It must be skipped when job_type='any' and a city is set,
        so remote-only listings don't pollute city-specific results.
        """
        called_urls: list = []

        async def fake_get(url, **kwargs):
            called_urls.append(url)
            return _make_response(ARBEITNOW_SEATTLE_RESPONSE)

        with patch("httpx.AsyncClient.get", fake_get), \
             patch("asyncio.to_thread", side_effect=_no_ddg_results):
            asyncio.get_event_loop().run_until_complete(
                searcher.search_all(
                    queries=["software engineer"],
                    location="Seattle",
                    country="",
                    job_type="any",
                )
            )

        assert not any("remotive" in u for u in called_urls), (
            "Remotive should be skipped when a city is given, but its URL was called."
        )

    # ------------------------------------------------------------------
    # 5. Whitespace around "Seattle" is stripped before sending to APIs
    # ------------------------------------------------------------------

    def test_location_whitespace_stripped(self, searcher):
        """'  Seattle  ' (extra spaces) should be normalised to 'Seattle'."""
        captured: dict = {}

        async def _side_effect(url, **kwargs):
            if "arbeitnow" in url:
                captured.update(kwargs.get("params", {}))
            return _make_response(ARBEITNOW_SEATTLE_RESPONSE)

        mock_get = AsyncMock(side_effect=_side_effect)

        with patch("httpx.AsyncClient.get", mock_get), \
             patch("asyncio.to_thread", side_effect=_no_ddg_results):
            asyncio.get_event_loop().run_until_complete(
                searcher.search_all(
                    queries=["software engineer"],
                    location="  Seattle  ",
                    country="",
                    job_type="any",
                )
            )

        location_sent = captured.get("location", "")
        assert "Seattle" in location_sent, (
            f"Stripped location should contain 'Seattle', got: {location_sent!r}"
        )
