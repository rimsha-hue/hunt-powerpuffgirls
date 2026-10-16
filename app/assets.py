"""Paths to files shipped with the app (sample data used by the demo and "Try with sample data")."""

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLE_RESUME = FIXTURES_DIR / "demo_resume.docx"
DEMO_PROFILE = FIXTURES_DIR / "demo_profile.json"
