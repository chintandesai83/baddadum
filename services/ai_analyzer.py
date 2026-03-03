import json
import os
import re
from typing import Any, Dict, List

from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"


class AIAnalyzer:
    """Uses Claude to parse profiles and score job matches."""

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY environment variable is required. "
                "Copy .env.example to .env and add your key."
            )
        self.client = Anthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # Profile parsing
    # ------------------------------------------------------------------

    def parse_profile(self, text: str) -> Dict[str, Any]:
        """Convert raw resume / profile text into a structured dict."""
        prompt = f"""You are an expert resume parser. Analyse the following resume or LinkedIn profile text and extract structured information.

Profile text:
\"\"\"
{text[:8000]}
\"\"\"

Return a single JSON object with exactly these fields (use empty strings / empty arrays when information is missing):
{{
  "name": "full name",
  "current_title": "most recent job title",
  "summary": "2-3 sentence professional summary you write based on the profile",
  "skills": ["list of technical and soft skills"],
  "experience_years": <integer total years of professional experience>,
  "experience": [
    {{
      "title": "job title",
      "company": "company name",
      "duration": "e.g. Jan 2020 – Mar 2023",
      "description": "one-sentence description of responsibilities"
    }}
  ],
  "education": [
    {{
      "degree": "e.g. Bachelor of Science",
      "field": "e.g. Computer Science",
      "institution": "university / college name",
      "year": "graduation year"
    }}
  ],
  "languages": ["spoken languages"],
  "certifications": ["certification names"],
  "industries": ["industries this person has worked in"],
  "desired_job_titles": ["3-5 realistic job titles this person could apply for right now"]
}}

Return ONLY valid JSON with no markdown fences, no explanation."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        return self._parse_json(response.content[0].text)

    # ------------------------------------------------------------------
    # Search query generation
    # ------------------------------------------------------------------

    def generate_search_queries(
        self, profile: Dict[str, Any], preferences: Dict[str, Any]
    ) -> List[str]:
        """Produce a short list of optimised job-board search queries."""
        desired_titles: List[str] = profile.get("desired_job_titles") or []
        current_title: str = profile.get("current_title", "")
        skills: List[str] = profile.get("skills", [])[:8]
        user_titles: List[str] = preferences.get("job_titles", [])

        queries: list[str] = []

        # 1. Titles the user explicitly wants (highest priority)
        for t in user_titles:
            if t and t not in queries:
                queries.append(t)

        # 2. AI-suggested desired titles
        for t in desired_titles[:3]:
            if t and t not in queries:
                queries.append(t)

        # 3. Current title as fallback
        if current_title and current_title not in queries:
            queries.append(current_title)

        # 4. Top-skills query
        if skills:
            skill_q = " ".join(skills[:4])
            if skill_q not in queries:
                queries.append(skill_q)

        return queries[:5] if queries else ["software engineer"]

    # ------------------------------------------------------------------
    # Job scoring & ranking
    # ------------------------------------------------------------------

    def score_and_rank_jobs(
        self, profile: Dict[str, Any], jobs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Score each job 0-100 for fit and return sorted list."""
        if not jobs:
            return []

        # Build a compact profile summary for the prompt
        profile_summary = (
            f"Name: {profile.get('name', 'Candidate')}\n"
            f"Current title: {profile.get('current_title', '')}\n"
            f"Experience: {profile.get('experience_years', 0)} years\n"
            f"Skills: {', '.join(profile.get('skills', [])[:15])}\n"
            f"Industries: {', '.join(profile.get('industries', []))}\n"
            f"Education: {self._education_summary(profile)}"
        )

        # Work in batches of 20 to stay within token limits
        all_scored: list[Dict[str, Any]] = []
        for batch_start in range(0, len(jobs), 20):
            batch = jobs[batch_start : batch_start + 20]
            scored_batch = self._score_batch(profile_summary, batch, batch_start)
            all_scored.extend(scored_batch)

        all_scored.sort(key=lambda j: j.get("match_score", 0), reverse=True)
        return all_scored

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _score_batch(
        self,
        profile_summary: str,
        jobs: List[Dict[str, Any]],
        offset: int,
    ) -> List[Dict[str, Any]]:
        jobs_text = "\n\n".join(
            f"Job {offset + i + 1}: {j.get('title', 'N/A')} at "
            f"{j.get('company', 'N/A')} | {j.get('location', '')} | "
            f"{j.get('description', '')[:300]}"
            for i, j in enumerate(jobs)
        )

        prompt = f"""You are an expert recruiter. Score the following job listings for fit with the candidate.

Candidate:
{profile_summary}

Job listings:
{jobs_text}

For each job return a score 0-100 (100 = perfect fit) and a one-sentence reason.
Return ONLY a JSON array like:
[{{"job_index": {offset + 1}, "score": 87, "reason": "Strong match on Python and 5 years backend experience."}}, ...]
No markdown, no extra text."""

        try:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            scores: list[Dict[str, Any]] = self._parse_json(response.content[0].text)
            if not isinstance(scores, list):
                raise ValueError("Expected JSON array")
        except Exception:
            # Graceful degradation: assign descending scores
            scores = [
                {"job_index": offset + i + 1, "score": max(80 - i * 3, 30), "reason": "Keyword match"}
                for i in range(len(jobs))
            ]

        result: list[Dict[str, Any]] = []
        for item in scores:
            idx = item.get("job_index", 0) - offset - 1
            if 0 <= idx < len(jobs):
                job = dict(jobs[idx])
                job["match_score"] = item.get("score", 50)
                job["match_reason"] = item.get("reason", "")
                result.append(job)
        return result

    @staticmethod
    def _education_summary(profile: Dict[str, Any]) -> str:
        edu = profile.get("education", [])
        if not edu:
            return ""
        first = edu[0]
        return f"{first.get('degree', '')} in {first.get('field', '')} from {first.get('institution', '')}"

    @staticmethod
    def _parse_json(text: str) -> Any:
        """Strip markdown fences and parse JSON."""
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError(f"Could not parse AI response as JSON: {cleaned[:200]}")
