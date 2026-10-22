#!/usr/bin/env python3
"""本地预览静态站，并复现生产环境的 ?q= 旧链接兼容行为。

用法:
    python3 scripts/serve.py --dir site --port 8080
"""
from __future__ import annotations

import argparse
import functools
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parsed = urllib.parse.urlsplit(path)
        params = urllib.parse.parse_qs(parsed.query)
        q = params.get("q", [None])[0]
        if q:
            key = q.strip("/")
            if ".." in key.split("/"):
                return str(Path(self.directory) / "__forbidden__")
            path = f"/{key}/"
        return super().translate_path(path)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="site")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    directory = str(Path(args.dir).resolve())
    handler = functools.partial(Handler, directory=directory)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"预览：http://{args.bind}:{args.port}/  (目录: {directory})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
