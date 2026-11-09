"""`askdocs` / `python -m askdocs`: serve the API with uvicorn."""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    ap = argparse.ArgumentParser(prog="askdocs")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    import uvicorn

    from .app import create_app
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
