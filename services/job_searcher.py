import asyncio
import os
import re
from typing import Any, Dict, List

import httpx


# Maps common country name / code variants to the ISO 3166-1 alpha-2 codes
# that Adzuna accepts in its URL path.
ADZUNA_COUNTRY_CODES: Dict[str, str] = {
    # English name → code
    "australia": "au", "austria": "at", "brazil": "br", "canada": "ca",
    "france": "fr", "germany": "de", "india": "in", "italy": "it",
    "netherlands": "nl", "new zealand": "nz", "poland": "pl", "russia": "ru",
    "singapore": "sg", "south africa": "za", "spain": "es",
    "united kingdom": "gb", "united states": "us",
    # ISO alpha-2 pass-through
    "au": "au", "at": "at", "br": "br", "ca": "ca", "de": "de", "fr": "fr",
    "gb": "gb", "in": "in", "it": "it", "nl": "nl", "nz": "nz", "pl": "pl",
    "ru": "ru", "sg": "sg", "us": "us", "za": "za", "es": "es",
    # Common aliases
    "usa": "us", "uk": "gb", "britain": "gb", "england": "gb",
    "deutschland": "de", "españa": "es", "brasil": "br", "holland": "nl",
    "america": "us",
}


class JobSearcher:
    """
    Searches multiple job boards concurrently and normalises results into a
    common schema.

    Free sources (no API key needed):
      - Remotive.io   – remote-only jobs (skipped when a specific city is given
                        or job_type is onsite/hybrid, as it has no location API)
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
        city = location.strip()

        # Remotive has NO location API and is remote-only.
        # Include it only when the user is explicitly seeking remote work,
        # or when they haven't specified a city (broad search with job_type=any).
        include_remotive = job_type == "remote" or (job_type == "any" and not city)

        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = []
            for query in queries[:3]:  # cap at 3 queries to keep latency low
                if include_remotive:
                    tasks.append(self._search_remotive(client, query, limit_per_source))
                tasks.append(
                    self._search_arbeitnow(client, query, city, country, job_type, limit_per_source)
                )
                if self.rapidapi_key:
                    tasks.append(
                        self._search_jsearch(client, query, city, country, job_type, limit_per_source)
                    )
                if self.adzuna_app_id and self.adzuna_api_key:
                    tasks.append(
                        self._search_adzuna(client, query, city, country, limit_per_source)
                    )
                tasks.append(
                    self._search_duckduckgo(query, city, country, job_type, limit_per_source)
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

        # Hard-filter by work arrangement — no silent fallback.
        if job_type != "any":
            all_jobs = [j for j in all_jobs if j.get("job_type") == job_type]

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
        city: str,
        country: str,
        job_type: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            params: Dict[str, Any] = {"search": query}
            # Combine city + country so Arbeitnow's text search has the best
            # chance of returning geographically relevant results.
            loc_parts = [p for p in [city, country] if p]
            if loc_parts:
                params["location"] = ", ".join(loc_parts)
            if job_type == "remote":
                params["remote"] = "true"
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
        city: str,
        country: str,
        job_type: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            headers = {
                "X-RapidAPI-Key": self.rapidapi_key,
                "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
            }
            # Embedding location in the query string is the most reliable way
            # to get geographically bounded results from JSearch.
            loc_parts = [p for p in [city, country] if p]
            loc_suffix = f" in {', '.join(loc_parts)}" if loc_parts else ""
            full_query = f"{query}{loc_suffix}"

            params: Dict[str, Any] = {"query": full_query, "num_pages": "1", "page": "1"}
            if job_type == "remote":
                params["remote_jobs_only"] = "true"
            # radius (km) brings results within ~50 km of the specified city
            if city:
                params["radius"] = "50"

            resp = await client.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            jobs = []
            for j in data.get("data", [])[:limit]:
                inferred_type = self._infer_job_type(j)
                job_city = j.get("job_city", "")
                job_ctry = j.get("job_country", "")
                location = ", ".join(filter(None, [job_city, job_ctry]))
                jobs.append(
                    {
                        "title": j.get("job_title", ""),
                        "company": j.get("employer_name", ""),
                        "location": location,
                        "description": j.get("job_description", "")[:600],
                        "url": j.get("job_apply_link", ""),
                        "salary": self._format_salary(j),
                        "job_type": inferred_type,
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
        city: str,
        country: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            cc = self._country_to_adzuna_code(country)
            params: Dict[str, Any] = {
                "app_id": self.adzuna_app_id,
                "app_key": self.adzuna_api_key,
                "what": query,
                "results_per_page": limit,
                "content-type": "application/json",
            }
            # `where` pins results to a city/region; `distance` (km) widens
            # the search radius around that location.
            if city:
                params["where"] = city
                params["distance"] = "50"
            resp = await client.get(
                f"https://api.adzuna.com/v1/api/jobs/{cc}/search/1",
                params=params,
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
    # DuckDuckGo (no API key — finds jobs on any site)
    # ------------------------------------------------------------------

    # Job-board domains used to filter DDG results to actual job listings.
    _JOB_DOMAINS = (
        "linkedin.com/jobs", "linkedin.com/job",
        "indeed.com", "glassdoor.com",
        "lever.co", "greenhouse.io", "ashbyhq.com",
        "jobs.workday.com", "myworkdayjobs.com",
        "smartrecruiters.com", "jobvite.com",
        "careers.", "/jobs/", "/careers/",
    )

    async def _search_duckduckgo(
        self,
        query: str,
        city: str,
        country: str,
        job_type: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Search DuckDuckGo for job postings — no API key required."""
        try:
            # Build a targeted query so DDG surfaces actual job postings.
            loc_parts = [p for p in [city, country] if p]
            loc_str = " ".join(f'"{p}"' for p in loc_parts)
            arrangement = "remote" if job_type == "remote" else ""
            search_q = " ".join(filter(None, [f'"{query}"', "job", arrangement, loc_str]))

            # DDGS.text() is synchronous; run it off the event loop.
            def _run() -> List[Dict[str, Any]]:
                from duckduckgo_search import DDGS  # lazy import

                raw = list(DDGS().text(search_q, max_results=limit * 2))
                jobs: List[Dict[str, Any]] = []
                for r in raw:
                    url = r.get("href", "")
                    # Only keep URLs that look like actual job listings.
                    if not any(d in url for d in JobSearcher._JOB_DOMAINS):
                        continue
                    title = r.get("title", "")
                    snippet = r.get("body", "")
                    text_lower = (title + " " + snippet).lower()
                    if "hybrid" in text_lower:
                        inferred = "hybrid"
                    elif "remote" in text_lower:
                        inferred = "remote"
                    else:
                        inferred = "onsite"
                    # Title is often "Role at Company | Board" — try to split.
                    company = ""
                    if " at " in title:
                        company = title.split(" at ", 1)[1].split("|")[0].strip()
                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": ", ".join(loc_parts),
                        "description": snippet[:600],
                        "url": url,
                        "salary": "",
                        "job_type": inferred,
                        "tags": [],
                        "posted_at": "",
                        "source": "DuckDuckGo",
                    })
                    if len(jobs) >= limit:
                        break
                return jobs

            return await asyncio.to_thread(_run)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _country_to_adzuna_code(country: str) -> str:
        """Map a free-text country name or code to an Adzuna-supported ISO code."""
        code = ADZUNA_COUNTRY_CODES.get(country.strip().lower(), "")
        # Fall back to "us" only if the country is blank or unrecognised
        return code or "us"

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
