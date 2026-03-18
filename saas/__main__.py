"""python -m saas — uvicorn entry point.

Runs two servers:
  - port 3700 HTTPS (API, requires ssl/cert.pem + ssl/key.pem)
  - port 3701 HTTP  (admin console, plain HTTP for browser access via IP)
"""
import multiprocessing
import os

import uvicorn

from .config import settings

def _run_https(port, cert, key):
    uvicorn.run("saas.main:app", host="0.0.0.0", port=port, reload=False,
                ssl_certfile=cert, ssl_keyfile=key)

def _run_http(port):
    uvicorn.run("saas.main:app", host="0.0.0.0", port=port, reload=False)

if __name__ == "__main__":
    ssl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ssl")
    cert = os.path.join(ssl_dir, "cert.pem")
    key = os.path.join(ssl_dir, "key.pem")

    http_port = settings.port + 1  # 3701

    if os.path.exists(cert) and os.path.exists(key):
        # HTTPS on 3700, HTTP on 3701
        p = multiprocessing.Process(target=_run_http, args=(http_port,))
        p.start()
        _run_https(settings.port, cert, key)
    else:
        # No cert — both ports plain HTTP
        p = multiprocessing.Process(target=_run_http, args=(http_port,))
        p.start()
        uvicorn.run("saas.main:app", host="0.0.0.0", port=settings.port, reload=False)
