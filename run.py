import os
import sys

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("EXAMFORGE_HOST", "127.0.0.1")
    port = int(os.environ.get("EXAMFORGE_PORT", "8000"))
    uvicorn.run("app.main:app_factory", host=host, port=port, factory=True, reload="--reload" in sys.argv)
