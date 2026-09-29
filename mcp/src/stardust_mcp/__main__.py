"""Run a Stardust MCP server: ``stardust-mcp reporting --core-url http://127.0.0.1:8080``."""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from .core import CoreClient
from .reporting import build_reporting_server


def main() -> None:
    p = argparse.ArgumentParser(prog="stardust-mcp", description=__doc__)
    p.add_argument("server", choices=["reporting"], help="which server to run (§23.3; more to come)")
    p.add_argument("--core-url", default=os.environ.get("STARDUST_CORE_URL", "http://127.0.0.1:8080"),
                   help="Stardust Core's base URL (env STARDUST_CORE_URL)")
    p.add_argument("--host", default=os.environ.get("STARDUST_MCP_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("STARDUST_MCP_PORT", "8090")))
    p.add_argument("--public-url", default=os.environ.get("STARDUST_MCP_PUBLIC_URL"),
                   help="URL clients use to reach this server (default http://HOST:PORT/mcp)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     [%(name)s] %(message)s")
    public_url = args.public_url or f"http://{args.host}:{args.port}/mcp"
    server = build_reporting_server(CoreClient(args.core_url), public_url=public_url)
    uvicorn.run(server.streamable_http_app(host=args.host), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
