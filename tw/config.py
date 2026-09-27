import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("TW_DATA_DIR") or ROOT / "data")
CACHE = Path(os.environ.get("TW_CACHE_DIR") or ROOT / ".cache")
TENDERS = DATA / "tenders"      # normalised TED notices (committed; reuse under Decision 2011/833/EU)
FIRM = DATA / "firm"            # fictional firm knowledge base (committed)
SPECS = DATA / "specs"          # synthetic specifications + gold requirement lists (committed)
RAW = DATA / "raw"              # raw API responses (not committed)
PRIVATE = DATA / "private"      # real tender documents downloaded by hand (never committed)


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines; existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
