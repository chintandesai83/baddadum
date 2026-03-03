import io
import re
import requests
from typing import Optional


class DocumentExtractor:
    """Extracts text from PDF, DOCX files and LinkedIn profile URLs."""

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    def extract_from_file(self, content: bytes, filename: str) -> str:
        """Dispatch to the correct extractor based on file extension."""
        name = filename.lower()
        if name.endswith(".pdf"):
            return self._extract_pdf(content)
        elif name.endswith(".docx"):
            return self._extract_docx(content)
        elif name.endswith(".doc"):
            raise ValueError(
                "Legacy .doc format is not supported. "
                "Please convert to .docx or PDF and re-upload."
            )
        else:
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError(f"Unsupported file format: {filename}")

    def _extract_pdf(self, content: bytes) -> str:
        import pdfplumber

        parts = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
        result = "\n".join(parts)
        if not result.strip():
            raise ValueError("PDF appears to contain no extractable text (may be image-based).")
        return result

    def _extract_docx(self, content: bytes) -> str:
        from docx import Document

        doc = Document(io.BytesIO(content))
        parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        parts.append(cell.text)
        result = "\n".join(parts)
        if not result.strip():
            raise ValueError("DOCX file contains no extractable text.")
        return result

    def extract_from_linkedin(self, url: str) -> str:
        """
        Attempt a best-effort extraction from a public LinkedIn profile URL.

        LinkedIn aggressively blocks scrapers and requires login for most content.
        When scraping fails we raise a descriptive error so the caller can ask the
        user to paste their profile text or upload a PDF instead.
        """
        if "linkedin.com" not in url:
            raise ValueError("URL does not appear to be a LinkedIn URL.")

        try:
            resp = requests.get(url, headers=self.HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ValueError(
                f"Could not reach LinkedIn ({exc}). "
                "LinkedIn may require login. Please download your profile as PDF "
                "from LinkedIn → Me → Settings → Data Privacy → Get a copy of your data, "
                "then upload the PDF here."
            )

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(resp.text, "html.parser")

        sections: list[str] = []

        # Name
        for tag in ("h1",):
            elem = soup.find(tag)
            if elem:
                text = elem.get_text(strip=True)
                if text:
                    sections.append(f"Name: {text}")
                    break

        # Try structured JSON-LD data LinkedIn sometimes embeds
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                import json
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("@type") in ("Person", "JobPosting"):
                    for key in ("name", "description", "jobTitle", "skills"):
                        if data.get(key):
                            sections.append(f"{key}: {data[key]}")
            except Exception:
                pass

        # Fallback: grab all readable text from main content area
        main = soup.find("main") or soup.find("div", {"id": "main-content"}) or soup.body
        if main:
            raw = main.get_text(separator="\n", strip=True)
            raw = re.sub(r"\n{3,}", "\n\n", raw)
            sections.append(raw[:6000])

        result = "\n".join(sections).strip()
        if len(result) < 100:
            raise ValueError(
                "LinkedIn profile content could not be accessed — LinkedIn requires login "
                "to view most profile details.\n\n"
                "Alternatives:\n"
                "1. Download your LinkedIn profile as PDF: LinkedIn → Me → View Profile → "
                "More → Save to PDF, then upload it here.\n"
                "2. Copy and paste your profile text into the text field."
            )
        return result
