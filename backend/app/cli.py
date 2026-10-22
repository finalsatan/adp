"""运维命令：python -m app.cli <子命令>。"""

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.db import create_engine, create_sessionmaker
from app.core.errors import UsernameExistsError, WeakPasswordError
from app.iam.admin_bootstrap import create_admin
from app.main import create_app


def _export_openapi() -> None:
    schema = create_app().openapi()
    json.dump(schema, sys.stdout, sort_keys=True, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _read_password(password_stdin: bool) -> str | None:
    """读一次密码；两次交互输入不一致时返回 None。"""
    if password_stdin:
        return sys.stdin.readline().rstrip("\n")

    first = getpass.getpass("请输入管理员密码：")
    second = getpass.getpass("请再次输入密码：")
    if first != second:
        print("两次输入的密码不一致", file=sys.stderr)
        return None
    return first


async def _run_create_admin(username: str, password: str) -> int:
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)
    try:
        async with create_sessionmaker(engine)() as session:
            try:
                user = await create_admin(session, username, password)
            except WeakPasswordError as exc:
                reasons = ", ".join(exc.detail["reasons"]) if exc.detail else ""
                print(f"密码不符合要求：{reasons}", file=sys.stderr)
                return 1
            except UsernameExistsError:
                print(f"用户名已存在：{username.strip().lower()}", file=sys.stderr)
                return 1
            print(f"已创建系统管理员：{user.username}")
            return 0
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="ADP 运维命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("export-openapi", help="把 OpenAPI 文档写到标准输出")

    create_admin_parser = subparsers.add_parser("create-admin", help="创建初始系统管理员")
    create_admin_parser.add_argument("--username", required=True, help="管理员用户名")
    create_admin_parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读一行密码（自动化部署用）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "export-openapi":
        _export_openapi()
        return 0
    if args.command == "create-admin":
        password = _read_password(args.password_stdin)
        if password is None:
            return 1
        return asyncio.run(_run_create_admin(args.username, password))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
