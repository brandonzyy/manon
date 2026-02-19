"""Allow `python -m app` to start the Manon Gateway."""
from .main import app  # noqa: F401
from .config import get_settings

if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, log_level="info")
