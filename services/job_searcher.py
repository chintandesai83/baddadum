import asyncio
import os
import re
from typing import Any, Dict, List, Optional

import httpx


class JobSearcher:
    """
    Searches multiple job boards concurrently and normalises results into a
    common schema.

    Free sources (no API key needed):
      - Remotive.io   – remote-only jobs
      - Arbeitnow     – EU-focused + remote

    Optional sources (require API keys in .env):
      - JSearch via RapidAPI  (RAPIDAPI_KEY)
      - Adzuna                (ADZUNA_APP_ID + ADZUNA_API_KEY)
    """

    def __init__(self):
        self.rapidapi_key: str = os.getenv("RAPIDAPI_KEY", "")
        self.adzuna_app_id: str = os.getenv("ADZUNA_APP_ID", "")
        self.adzuna_api_key: str = os.getenv("ADZUNA_API_KEY", "")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_all(
        self,
        queries: List[str],
        location: str = "",
        country: str = "",
        job_type: str = "any",
        limit_per_source: int = 25,
    ) -> List[Dict[str, Any]]:
        """
        Fan out searches across all available sources and deduplicate by URL.
        Returns a flat list of normalised job dicts.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = []
            for query in queries[:3]:  # cap at 3 queries to keep latency low
                tasks.append(self._search_remotive(client, query, limit_per_source))
                tasks.append(
                    self._search_arbeitnow(client, query, location, limit_per_source)
                )
                if self.rapidapi_key:
                    loc_query = f"{query} {location}".strip() if location else query
                    tasks.append(self._search_jsearch(client, loc_query, country, limit_per_source))
                if self.adzuna_app_id and self.adzuna_api_key:
                    tasks.append(
                        self._search_adzuna(client, query, country or "us", limit_per_source)
                    )

            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten, deduplicate by URL
        seen_urls: set[str] = set()
        all_jobs: list[Dict[str, Any]] = []
        for result in results:
            if isinstance(result, list):
                for job in result:
                    url = job.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_jobs.append(job)

        # Apply work-arrangement filter
        if job_type != "any":
            filtered = [j for j in all_jobs if j.get("job_type") == job_type]
            # Fall back to unfiltered if too few results
            all_jobs = filtered if len(filtered) >= 5 else all_jobs

        return all_jobs

    # ------------------------------------------------------------------
    # Remotive
    # ------------------------------------------------------------------

    async def _search_remotive(
        self, client: httpx.AsyncClient, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        try:
            resp = await client.get(
                "https://remotive.io/api/remote-jobs",
                params={"search": query, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            jobs = []
            for j in data.get("jobs", []):
                jobs.append(
                    {
                        "title": j.get("title", ""),
                        "company": j.get("company_name", ""),
                        "location": j.get("candidate_required_location", "Remote"),
                        "description": self._strip_html(j.get("description", ""))[:600],
                        "url": j.get("url", ""),
                        "salary": j.get("salary", ""),
                        "job_type": "remote",
                        "tags": j.get("tags", []),
                        "posted_at": j.get("publication_date", ""),
                        "source": "Remotive",
                    }
                )
            return jobs
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Arbeitnow
    # ------------------------------------------------------------------

    async def _search_arbeitnow(
        self,
        client: httpx.AsyncClient,
        query: str,
        location: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            params: Dict[str, Any] = {"search": query}
            if location:
                params["location"] = location
            resp = await client.get(
                "https://www.arbeitnow.com/api/job-board-api",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            jobs = []
            for j in data.get("data", [])[:limit]:
                is_remote = j.get("remote", False)
                jobs.append(
                    {
                        "title": j.get("title", ""),
                        "company": j.get("company_name", ""),
                        "location": j.get("location", ""),
                        "description": self._strip_html(j.get("description", ""))[:600],
                        "url": j.get("url", ""),
                        "salary": "",
                        "job_type": "remote" if is_remote else "onsite",
                        "tags": j.get("tags", []),
                        "posted_at": str(j.get("created_at", "")),
                        "source": "Arbeitnow",
                    }
                )
            return jobs
        except Exception:
            return []

    # ------------------------------------------------------------------
    # JSearch (RapidAPI)
    # ------------------------------------------------------------------

    async def _search_jsearch(
        self,
        client: httpx.AsyncClient,
        query: str,
        country: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            headers = {
                "X-RapidAPI-Key": self.rapidapi_key,
                "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
            }
            params: Dict[str, Any] = {"query": query, "num_pages": "1", "page": "1"}
            if country:
                params["country"] = country
            resp = await client.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            jobs = []
            for j in data.get("data", [])[:limit]:
                job_type = self._infer_job_type(j)
                city = j.get("job_city", "")
                ctry = j.get("job_country", "")
                location = ", ".join(filter(None, [city, ctry]))
                jobs.append(
                    {
                        "title": j.get("job_title", ""),
                        "company": j.get("employer_name", ""),
                        "location": location,
                        "description": j.get("job_description", "")[:600],
                        "url": j.get("job_apply_link", ""),
                        "salary": self._format_salary(j),
                        "job_type": job_type,
                        "tags": [],
                        "posted_at": j.get("job_posted_at_datetime_utc", ""),
                        "source": "JSearch",
                    }
                )
            return jobs
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Adzuna
    # ------------------------------------------------------------------

    async def _search_adzuna(
        self,
        client: httpx.AsyncClient,
        query: str,
        country: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            # Adzuna uses ISO 3166-1 alpha-2 country codes (lower-case)
            cc = (country or "us").lower()[:2]
            resp = await client.get(
                f"https://api.adzuna.com/v1/api/jobs/{cc}/search/1",
                params={
                    "app_id": self.adzuna_app_id,
                    "app_key": self.adzuna_api_key,
                    "what": query,
                    "results_per_page": limit,
                    "content-type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            jobs = []
            for j in data.get("results", []):
                salary = ""
                if j.get("salary_min") and j.get("salary_max"):
                    salary = f"{j['salary_min']:,.0f} – {j['salary_max']:,.0f}"
                loc = j.get("location", {}).get("display_name", "")
                jobs.append(
                    {
                        "title": j.get("title", ""),
                        "company": j.get("company", {}).get("display_name", ""),
                        "location": loc,
                        "description": j.get("description", "")[:600],
                        "url": j.get("redirect_url", ""),
                        "salary": salary,
                        "job_type": "onsite",
                        "tags": j.get("category", {}).get("label", "").split(","),
                        "posted_at": j.get("created", ""),
                        "source": "Adzuna",
                    }
                )
            return jobs
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags and collapse whitespace."""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&[a-z]+;", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _infer_job_type(job: Dict[str, Any]) -> str:
        if job.get("job_is_remote"):
            return "remote"
        desc = (job.get("job_description", "") or "").lower()
        emp_type = (job.get("job_employment_type", "") or "").lower()
        if "hybrid" in desc or "hybrid" in emp_type:
            return "hybrid"
        if "remote" in desc or "remote" in emp_type:
            return "remote"
        return "onsite"

    @staticmethod
    def _format_salary(job: Dict[str, Any]) -> str:
        min_s = job.get("job_min_salary")
        max_s = job.get("job_max_salary")
        currency = job.get("job_salary_currency", "USD")
        period = (job.get("job_salary_period") or "YEAR").capitalize()
        if min_s and max_s:
            return f"{currency} {min_s:,.0f} – {max_s:,.0f} / {period}"
        if min_s:
            return f"{currency} {min_s:,.0f}+ / {period}"
        return ""
