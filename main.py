"""
LinkedIn Job Matcher – FastAPI backend
======================================
Endpoints:
  GET  /              – serve the single-page frontend
  POST /api/extract   – extract profile from file upload, LinkedIn URL or pasted text
  POST /api/search    – search jobs and return top-10 ranked matches
"""

import os
from typing import List, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from services.ai_analyzer import AIAnalyzer
from services.extractor import DocumentExtractor
from services.job_searcher import JobSearcher

app = FastAPI(title="LinkedIn Job Matcher", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

extractor = DocumentExtractor()
analyzer = AIAnalyzer()
job_searcher = JobSearcher()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class JobSearchRequest(BaseModel):
    profile: dict
    location: str = ""
    country: str = ""
    job_type: str = "any"  # any | remote | hybrid | onsite
    job_titles: List[str] = []
    additional_preferences: str = ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open("static/index.html", encoding="utf-8") as fh:
        return HTMLResponse(content=fh.read())


@app.post("/api/extract")
async def extract_profile(
    file: Optional[UploadFile] = File(None),
    linkedin_url: Optional[str] = Form(None),
    profile_text: Optional[str] = Form(None),
):
    """
    Accept one of:
      - a PDF or DOCX resume file
      - a LinkedIn profile URL
      - pasted plain text

    Returns the AI-structured profile JSON plus a short preview of the raw text.
    """
    if not file and not linkedin_url and not profile_text:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one input: a file, a LinkedIn URL, or profile text.",
        )

    try:
        if file:
            content = await file.read()
            if not content:
                raise HTTPException(400, "Uploaded file is empty.")
            raw_text = extractor.extract_from_file(content, file.filename or "upload")
        elif linkedin_url:
            raw_text = extractor.extract_from_linkedin(linkedin_url.strip())
        else:
            raw_text = (profile_text or "").strip()

        if len(raw_text.strip()) < 50:
            raise HTTPException(
                400,
                "Not enough text could be extracted. "
                "Please try uploading a PDF or pasting your profile text.",
            )

        profile = analyzer.parse_profile(raw_text)
        return {
            "success": True,
            "profile": profile,
            "preview": raw_text[:400],
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Processing error: {exc}")


@app.post("/api/search")
async def search_jobs(request: JobSearchRequest):
    """
    Generate search queries from the profile + user preferences,
    fan out to multiple job boards, score every listing with Claude,
    and return the top-10 matches.
    """
    try:
        queries = analyzer.generate_search_queries(
            request.profile,
            {
                "location": request.location,
                "country": request.country,
                "job_type": request.job_type,
                "job_titles": request.job_titles,
            },
        )

        raw_jobs = await job_searcher.search_all(
            queries=queries,
            location=request.location,
            country=request.country,
            job_type=request.job_type,
        )

        if not raw_jobs:
            return {
                "success": True,
                "jobs": [],
                "total_found": 0,
                "message": (
                    "No jobs found for this search. "
                    "Try selecting 'Any' for work arrangement or broadening your location."
                ),
            }

        ranked = analyzer.score_and_rank_jobs(request.profile, raw_jobs)
        return {
            "success": True,
            "jobs": ranked[:10],
            "total_found": len(raw_jobs),
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Job search error: {exc}")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
