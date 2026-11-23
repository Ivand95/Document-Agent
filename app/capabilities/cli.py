"""Capability registry from the server shell.

    PYTHONPATH=app uv run python -m capabilities.cli generate-key     # a new CAPABILITY_SECRET_KEY
    PYTHONPATH=app uv run python -m capabilities.cli rotate-secrets   # re-encrypt with the first key
    PYTHONPATH=app uv run python -m capabilities.cli sync-native      # copy native tools into the registry
    PYTHONPATH=app uv run python -m capabilities.cli list

Rotating the key: put the new key first and the old one after it, comma-separated
(CAPABILITY_SECRET_KEY=new,old), restart, run rotate-secrets, then remove the old key.
"""
import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m capabilities.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate-key")
    sub.add_parser("rotate-secrets")
    sub.add_parser("sync-native")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    if args.command == "generate-key":
        from capabilities.secrets import generate_key

        print(generate_key())
        return 0

    from capabilities import registry

    if args.command == "rotate-secrets":
        print(f"Re-encrypted {registry.rotate_secrets()} secrets with the first key.")
    elif args.command == "sync-native":
        report = registry.sync_native_tools(by="cli")
        for label in ("added", "updated", "missing"):
            print(f"{label}: {', '.join(report[label]) or '-'}")
    elif args.command == "list":
        for cap in registry.list_capabilities():
            state = "on " if cap["enabled"] else "off"
            ops = registry.list_operations(cap["id"])
            enabled = sum(1 for o in ops if o["enabled"])
            print(f"[{state}] {cap['key']:<30} {cap['kind']:<12} {cap['data_class']:<10} ops {enabled}/{len(ops)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
