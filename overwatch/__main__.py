"""CLI: python -m overwatch --help."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time

from .api import APIServer
from .mcp import MCPServer
from .redfish import RedfishClient, SimulationAdapter
from .service import Overwatch
from .simulator import LabServer
from .store import Store


def write_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        file.write(content)
    os.chmod(path, 0o600)


def credentials(directory):
    directory.mkdir(parents=True, exist_ok=True)
    key_file = directory / "signing.key"
    if not key_file.exists():
        write_private(key_file, secrets.token_hex(32))
    token_file = directory / "demo-credentials.json"
    if not token_file.exists():
        write_private(token_file, json.dumps({role: secrets.token_urlsafe(32) for role in
                      ["operator", "procurement", "finance", "policy_admin", "viewer", "lab"]}, indent=2))
    return bytes.fromhex(key_file.read_text().strip()), json.loads(token_file.read_text()), token_file


def main():
    parser = argparse.ArgumentParser(description="WatcherMCP evidence-first hardware operations lab")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Run the local simulator, API and dashboard")
    demo.add_argument("--port", type=int, default=8080)
    demo.add_argument("--lab-port", type=int, default=8001)
    demo.add_argument("--data", type=Path, default=Path("data"))
    demo.add_argument("--soak-seconds", type=int, default=60)
    mock = sub.add_parser("lab", help="Run the standalone stateful Redfish lab")
    mock.add_argument("--port", type=int, default=8001)
    mock.add_argument("--data", type=Path, default=Path("data"))
    mcp = sub.add_parser("mcp", help="Run a read-only MCP stdio server")
    mcp.add_argument("--zone", choices=["fleet", "records", "market"], required=True)
    mcp.add_argument("--db", type=Path, default=Path("data/overwatch.db"))
    collect = sub.add_parser("collect", help="Read one Redfish endpoint; never executes actions")
    collect.add_argument("--url", required=True)
    collect.add_argument("--environment", choices=["simulation", "production"], default="simulation")
    collect.add_argument("--data", type=Path, default=Path("data"))
    collect.add_argument("--ca-file")
    audit = sub.add_parser("audit", help="Verify the local ledger hash chain")
    audit.add_argument("--db", type=Path, default=Path("data/overwatch.db"))
    args = parser.parse_args()
    if args.command in {"mcp", "audit"}:
        if not args.db.exists():
            parser.error("database not found; run demo or collect first")
        store = Store(args.db)
        if args.command == "mcp":
            MCPServer(store, args.zone).run(sys.stdin, sys.stdout)
        else:
            valid = store.verify_ledger()
            print(json.dumps({"ledger_valid": valid, "events": len(store.events())}))
            return 0 if valid else 1
        return 0
    key, tokens, token_file = credentials(args.data)
    if args.command == "collect":
        client = RedfishClient(args.url, environment=args.environment, ca_file=args.ca_file,
                               authorization=os.environ.get("WATCHER_REDFISH_AUTHORIZATION"))
        app = Overwatch(args.data / "overwatch.db", key, environment=args.environment)
        cases = [app.observe(**reading) for reading in client.collect()]
        print(json.dumps({"cases": cases, "environment": args.environment}, indent=2))
        return 0
    lab_port = args.lab_port if args.command == "demo" else args.port
    lab = LabServer(port=lab_port, token=tokens["lab"]).start()
    api = None
    try:
        print(f"Redfish simulation: {lab.url}", flush=True)
        print(f"Local credentials: {token_file.resolve()} (never commit this file)", flush=True)
        if args.command == "demo":
            client = RedfishClient(lab.url, lab_token=tokens["lab"])
            app = Overwatch(args.data / "overwatch.db", key, adapter=SimulationAdapter(client), soak_seconds=args.soak_seconds)
            identities = {value: {"actor": role + "-local", "role": role} for role, value in tokens.items() if role != "lab"}
            # In-flight actions survive application restarts; simulator restarts do not recreate lost remote outcomes.
            app.recover_inflight()
            api = APIServer(app, client, identities=identities, port=args.port).start()
            print(f"Operations desk: {api.url}", flush=True)
            print("Open the desk and paste the operator token. Ctrl+C stops both services.", flush=True)
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        if api:
            api.close()
        lab.close()


if __name__ == "__main__":
    raise SystemExit(main())
