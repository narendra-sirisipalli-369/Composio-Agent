"""Keep tests isolated from live credentials and the research database."""
import os
import tempfile
from pathlib import Path

TEST_DIR = Path(tempfile.mkdtemp(prefix="fieldnote-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DIR / 'research.db'}"
os.environ["TAVILY_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
