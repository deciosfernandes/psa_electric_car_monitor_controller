import os
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(os.environ.get("PSACC_DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

TZ = ZoneInfo("Atlantic/Azores")
