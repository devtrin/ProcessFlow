"""Start the backend: ``python -m app [--host 127.0.0.1] [--port 8000]``."""

import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="ProcessFlow backend")
    ap.add_argument("--host", default="127.0.0.1", help="Bind address (default localhost only)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("app.main:_app_from_env", factory=True, host=a.host, port=a.port, reload=a.reload,
                log_level="info", access_log=False)


if __name__ == "__main__":
    main()
