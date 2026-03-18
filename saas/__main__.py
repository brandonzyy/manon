"""python -m saas — uvicorn entry point."""
import uvicorn

from .config import settings

if __name__ == "__main__":
    uvicorn.run(
        "saas.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=False,
    )
