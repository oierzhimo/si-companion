"""Developer utility to exercise manual v1 event bundles between test folders.

This intentionally does not update settings.json or the running desktop pet.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from si_pet.sync_v1 import Ledger, SyncError


def write_new(path, data):
    path = Path(path)
    if path.exists():
        raise SyncError(f"Refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Si offline sync protocol test utility")
    parser.add_argument("--ledger", type=Path, required=True,
                        help="Path to an isolated sync-v1.json ledger")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a ledger seeded from a legacy settings file")
    init.add_argument("--settings", type=Path, required=True)
    export = commands.add_parser("export", help="Write a new manual event bundle")
    export.add_argument("output", type=Path)
    importing = commands.add_parser("import", help="Merge a manual event bundle")
    importing.add_argument("input", type=Path)
    commands.add_parser("status", help="Print derived counters without modifying settings")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if args.ledger.exists():
                raise SyncError("Ledger already exists")
            settings = json.loads(args.settings.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise SyncError("Invalid legacy settings")
            ledger = Ledger(args.ledger)
            ledger.seed_legacy(settings)
            print(json.dumps({"device_id": ledger.device_id,
                              "migration_event": next(iter(ledger.events))}, ensure_ascii=False))
            return 0
        if not args.ledger.is_file():
            raise SyncError("Ledger missing; run init first")
        ledger = Ledger(args.ledger)
        if args.command == "export":
            write_new(args.output, ledger.export())
            print(args.output)
        elif args.command == "import":
            print(json.dumps({"added": ledger.import_bundle(args.input.read_bytes()),
                              "summary": ledger.summary()}, ensure_ascii=False))
        else:
            print(json.dumps(ledger.summary(), ensure_ascii=False))
        return 0
    except (SyncError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"sync error: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
