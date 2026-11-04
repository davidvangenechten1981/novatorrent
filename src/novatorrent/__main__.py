import os

import uvicorn

from .app import create_app

if __name__ == "__main__":
    uvicorn.run(
        create_app(),
        host=os.environ.get("NOVA_BIND", "0.0.0.0"),
        port=int(os.environ.get("NOVA_PORT", "8080")),
        log_level="info",
        access_log=False,
    )
