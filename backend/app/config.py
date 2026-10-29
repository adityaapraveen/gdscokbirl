from pathlib import Path
import os

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SNAPSHOT = DATA_DIR / "elexon-january-2026.json.gz"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'windlens.sqlite'}")
DECISION_FROM = "2026-01-01"
DECISION_TO = "2026-01-31"
