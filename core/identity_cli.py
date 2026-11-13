"""Local identity administration CLI (Phase 4F-1).

Examples:
  python -m core.identity_cli bootstrap-admin
  python -m core.identity_cli recover-admin
  python -m core.identity_cli list-principals
  python -m core.identity_cli create-credential --principal svc-qq --kind service_token
  python -m core.identity_cli rotate-credential --credential cred_xxx
  python -m core.identity_cli revoke-credential --credential cred_xxx
"""

from __future__ import annotations

import argparse
import json
import sys

from . import identity, store


def _init() -> None:
    store.init_db()
    identity.init_schema()


def _print(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Möbius identity admin")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bootstrap-admin", help="首次创建 admin 并生成一次性 bootstrap token")
    p.add_argument("--ttl", type=int, default=900)

    p = sub.add_parser("recover-admin", help="为现有 admin 生成一次性 recovery token")
    p.add_argument("--ttl", type=int, default=900)
    p.add_argument("--principal", default="")
    p.add_argument("--create-new", action="store_true",
                   help="没有可恢复 admin 时显式创建新 admin")

    sub.add_parser("list-principals")
    sub.add_parser("map-legacy-key")

    p = sub.add_parser("create-credential")
    p.add_argument("--principal", required=True)
    p.add_argument("--kind", default="service_token")

    p = sub.add_parser("rotate-credential")
    p.add_argument("--credential", required=True)

    p = sub.add_parser("revoke-credential")
    p.add_argument("--credential", required=True)

    p = sub.add_parser("disable-principal")
    p.add_argument("--principal", required=True)

    p = sub.add_parser("purge-sessions")

    args = parser.parse_args(argv)
    try:
        _init()
        if args.command == "bootstrap-admin":
            result = identity.bootstrap_admin(ttl=args.ttl, actor="identity-cli")
            result["warning"] = "token 只显示一次；请立即使用并妥善保管"
        elif args.command == "recover-admin":
            result = identity.recover_admin(
                ttl=args.ttl, actor="identity-cli",
                principal_id=(args.principal or None),
                create_new=args.create_new,
            )
            result["warning"] = "token 只显示一次；仅允许本机使用；消费后会重新启用目标 admin"
        elif args.command == "list-principals":
            result = {"principals": identity.list_principals()}
        elif args.command == "map-legacy-key":
            mapped = identity.map_configured_legacy_key(actor="identity-cli")
            result = mapped or {"mapped": False, "reason": "core.api_key 未配置"}
        elif args.command == "create-credential":
            result = identity.create_credential(
                principal_id=args.principal, kind=args.kind, actor="identity-cli")
            result["warning"] = "secret 只显示一次"
        elif args.command == "rotate-credential":
            result = identity.rotate_credential(args.credential, actor="identity-cli")
            result["warning"] = "新 secret 只显示一次；旧 credential 已撤销"
        elif args.command == "revoke-credential":
            result = identity.revoke_credential(args.credential, actor="identity-cli")
        elif args.command == "disable-principal":
            result = identity.disable_principal(args.principal, actor="identity-cli")
        elif args.command == "purge-sessions":
            result = {"purged": identity.purge_expired_sessions(actor="identity-cli")}
        else:
            parser.error("unknown command")
            return 2
    except identity.IdentityError as exc:
        print(f"ERROR {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
