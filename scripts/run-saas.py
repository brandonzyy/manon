"""Launcher: start saas with admin_secret."""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SAAS_ADMIN_SECRET"] = "manon123!@#"

import uvicorn
from saas.config import settings

uvicorn.run("saas.main:app", host="0.0.0.0", port=settings.port, reload=False)
