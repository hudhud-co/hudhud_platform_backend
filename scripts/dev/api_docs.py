#!/usr/bin/env python3
"""One Swagger page for all fourteen services.

    uv run python scripts/dev/api_docs.py            # serve on http://127.0.0.1:8115
    uv run python scripts/dev/api_docs.py --build docs/api   # write a static copy

The platform has no gateway (ADR-0004 designs one; it is not built), so each service
answers on its own port and documents itself at its own `/docs`. Reading fourteen tabs to
answer "what can this platform do" is the problem this solves.

**Why this aggregates server-side rather than pointing a page at fourteen origins.** No
service sets CORS headers, so a browser fetching `127.0.0.1:8102/openapi.json` from a page
served anywhere else is blocked. Adding wildcard CORS to fourteen delivery-platform APIs
to make a docs page work would be a poor trade. This process fetches the specs instead and
serves them from one origin, so nothing about the services changes.

**The merged spec keeps each path honest about where it lives.** A single `servers` entry
would be a lie — these paths are on fourteen different ports. OpenAPI allows `servers` per
path item, so every path carries its own, and "Try it out" reaches the right service.

`/health` and `/ready` are left out of the merged view: every service has both, identical,
and a merged document cannot hold fourteen copies of one path. They are still in each
service's own view in the dropdown.
"""

from __future__ import annotations

import argparse
import copy
import errno
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stack import SERVICES  # noqa: E402 — the one place ports are declared

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_PORT = 8115
PLUMBING_PATHS = ("/health", "/ready")

SWAGGER_VERSION = "5.17.14"
SWAGGER_CSS = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}/swagger-ui.css"
SWAGGER_JS = (
    f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}/swagger-ui-bundle.js"
)


def service_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def fetch_spec(port: int, *, timeout: float = 5.0) -> dict | None:
    """One service's OpenAPI document, or None when it is not answering."""
    try:
        with urllib.request.urlopen(  # noqa: S310 — fixed loopback URL
            f"http://127.0.0.1:{port}/openapi.json", timeout=timeout
        ) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def collect() -> dict[str, dict]:
    """Every service's spec, keyed by name. Services that are down are skipped."""
    found = {}
    for service in SERVICES:
        spec = fetch_spec(service.port)
        if spec is not None:
            found[service.name] = spec
    return found


# ------------------------------------------------------------------------ merging


def _namespace_refs(node: object, service: str) -> object:
    """Rewrite every local schema `$ref` to its namespaced name.

    Several services define a `HealthResponse` or a `WorkloadResponse`. They are different
    types that happen to share a name, so merging without namespacing would silently show
    one service's schema under another's operation.
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if (
                key == "$ref"
                and isinstance(value, str)
                and value.startswith("#/components/schemas/")
            ):
                name = value.removeprefix("#/components/schemas/")
                out[key] = f"#/components/schemas/{service}.{name}"
            else:
                out[key] = _namespace_refs(value, service)
        return out
    if isinstance(node, list):
        return [_namespace_refs(item, service) for item in node]
    return node


def merge(specs: dict[str, dict]) -> dict:
    """One OpenAPI document describing the whole platform."""
    merged: dict = {
        "openapi": "3.1.0",
        "info": {
            "title": "HUDHUD Platform",
            "version": "v6.3",
            "description": (
                "Every service in one document. Each path carries its own `servers` "
                "entry, because the platform has no gateway yet and these paths live on "
                "fourteen different ports — the tag on an operation tells you which "
                "service owns it. Per-service `/health` and `/ready` are omitted here; "
                "pick a single service in the dropdown to see them."
            ),
        },
        "tags": [],
        "paths": {},
        "components": {"schemas": {}},
    }

    by_port = {service.name: service.port for service in SERVICES}
    for name in sorted(specs):
        spec = copy.deepcopy(specs[name])
        port = by_port[name]
        merged["tags"].append(
            {"name": name, "description": f"{service_url(port)} · {port}"}
        )

        for schema_name, schema in (spec.get("components", {}).get("schemas") or {}).items():
            merged["components"]["schemas"][f"{name}.{schema_name}"] = _namespace_refs(
                schema, name
            )

        for path, item in (spec.get("paths") or {}).items():
            if path in PLUMBING_PATHS:
                continue
            entry = _namespace_refs(copy.deepcopy(item), name)
            # Where the path actually lives. Without this, Try-it-out would send every
            # request to whichever host happens to be serving this page.
            entry["servers"] = [{"url": service_url(port)}]
            for method, operation in entry.items():
                if method in ("servers", "parameters", "summary", "description"):
                    continue
                if not isinstance(operation, dict):
                    continue
                operation["tags"] = [name]
                if "operationId" in operation:
                    operation["operationId"] = f"{name}_{operation['operationId']}"
            if path in merged["paths"]:
                # Namespaces do not collide today; say so loudly if that ever changes.
                print(f"  warning: {path} is served by more than one service", file=sys.stderr)
            merged["paths"][path] = entry

    return merged


# --------------------------------------------------------------------------- page


def page_html(names: list[str]) -> str:
    urls = [{"name": "All services (merged)", "url": "specs/platform.json"}]
    urls += [{"name": name, "url": f"specs/{name}.json"} for name in names]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HUDHUD Platform API</title>
<link rel="stylesheet" href="{SWAGGER_CSS}">
<style>
  body {{ margin: 0; }}
  /* The topbar carries the service selector, so it stays. Only the "explore" box goes,
     which invites typing a URL over picking one of the fourteen. */
  .swagger-ui .topbar {{ background: #1b1f23; }}
  .swagger-ui .topbar .download-url-button {{ display: none; }}
  .swagger-ui .topbar-wrapper img {{ display: none; }}
  .swagger-ui .topbar-wrapper::before {{
    content: "HUDHUD Platform";
    color: #fff; font-weight: 600; font-size: 18px; margin-right: 16px;
  }}
  .swagger-ui .info {{ margin: 24px 0; }}
</style>
</head>
<body>
<div id="swagger"></div>
<script src="{SWAGGER_JS}" crossorigin></script>
<script>
  window.ui = SwaggerUIBundle({{
    dom_id: "#swagger",
    urls: {json.dumps(urls)},
    "urls.primaryName": "All services (merged)",
    deepLinking: true,
    displayOperationId: false,
    docExpansion: "none",
    filter: true,
    tryItOutEnabled: true,
  }});
</script>
</body>
</html>
"""


# -------------------------------------------------------------------------- serve


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's interface
        path = self.path.split("?")[0]
        # Collected per request: a service restarted with a new route shows up on reload
        # rather than on a restart of this process.
        specs = collect()

        if path in ("/", "/index.html"):
            self._send(page_html(sorted(specs)).encode(), "text/html; charset=utf-8")
            return
        if path == "/specs/platform.json":
            self._send(json.dumps(merge(specs)).encode(), "application/json")
            return
        if path.startswith("/specs/") and path.endswith(".json"):
            name = path.removeprefix("/specs/").removesuffix(".json")
            if name in specs:
                self._send(json.dumps(specs[name]).encode(), "application/json")
                return
        self._send(b'{"error":"not found"}', "application/json", status=404)

    def log_message(self, *args) -> None:  # noqa: ARG002 — quiet by default
        return


def command_serve(args: argparse.Namespace) -> int:
    specs = collect()
    if not specs:
        print("No service answered. Start the platform with `make up` or `make docker-up`.")
        return 1

    # Bind before announcing anything, so a failure is not preceded by a banner
    # claiming the server is up.
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        # Almost always a previous `make api-docs` still running in another terminal.
        # A traceback here says nothing a reader can act on.
        print(
            f"\nPort {args.port} is already in use — another api-docs is probably running."
            f"\n  find it:  lsof -nP -iTCP:{args.port} -sTCP:LISTEN"
            f"\n  stop it:  pkill -f api_docs.py"
            f"\n  or serve elsewhere:  make api-docs PORT={args.port + 1}"
        )
        return 1

    missing = [s.name for s in SERVICES if s.name not in specs]
    # Flushed: this banner is the only confirmation the server started, and a redirected
    # stdout would otherwise hold it until the process ends.
    print(
        f"Serving {len(specs)} of {len(SERVICES)} services "
        f"on http://127.0.0.1:{args.port}",
        flush=True,
    )
    if missing:
        print(f"  not answering: {', '.join(missing)}", flush=True)
    print("  Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


def command_build(args: argparse.Namespace) -> int:
    specs = collect()
    if not specs:
        print("No service answered. Start the platform with `make up` or `make docker-up`.")
        return 1
    out = Path(args.build)
    if not out.is_absolute():
        out = REPO_ROOT / out
    (out / "specs").mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page_html(sorted(specs)), encoding="utf-8")
    (out / "specs" / "platform.json").write_text(
        json.dumps(merge(specs), indent=2), encoding="utf-8"
    )
    for name, spec in specs.items():
        (out / "specs" / f"{name}.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    merged_paths = len(merge(specs)["paths"])
    print(f"Wrote {out}/index.html — {len(specs)} services, {merged_paths} paths")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DOCS_PORT)
    parser.add_argument(
        "--build",
        metavar="DIR",
        help="write a static copy instead of serving (open DIR/index.html)",
    )
    args = parser.parse_args()
    return command_build(args) if args.build else command_serve(args)


if __name__ == "__main__":
    sys.exit(main())
