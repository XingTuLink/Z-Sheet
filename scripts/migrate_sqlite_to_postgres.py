"""Copy a local Z-Sheet SQLite database into PostgreSQL.

Usage:
    uv run python scripts/migrate_sqlite_to_postgres.py \
        --source data/zsheet.db \
        --target postgresql://zsheet:zsheet@127.0.0.1:5432/zsheet

The target must already be migrated (``ZSHEET_DATABASE_URL=<target> alembic
upgrade head``). The tool refuses a non-empty target unless ``--replace`` is
given, copies every table with its original ids, re-synchronizes PostgreSQL
sequences and verifies row-by-row that nothing was lost.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.storage.migrate import MigrationError, copy_database  # noqa: E402


def _sqlite_url(raw: str) -> str:
    if raw.startswith("sqlite:") or "://" in raw:
        return raw
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.is_file():
        raise SystemExit(f"源库文件不存在：{path}")
    return f"sqlite:///{path.as_posix()}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Z-Sheet SQLite → PostgreSQL 数据迁移")
    parser.add_argument("--source", default="data/zsheet.db", help="源 SQLite 文件或 URL")
    parser.add_argument(
        "--target",
        default="",
        help="目标 PostgreSQL URL（省略时读取 ZSHEET_DATABASE_URL）",
    )
    parser.add_argument("--replace", action="store_true", help="允许清空非空的目标库")
    args = parser.parse_args()

    target = args.target.strip() or os.environ.get("ZSHEET_DATABASE_URL", "").strip()
    if not target:
        print("缺少目标库地址：请用 --target 传入 PostgreSQL URL。", file=sys.stderr)
        return 2
    if target.startswith("sqlite"):
        print("目标库必须是 PostgreSQL，不能是 SQLite。", file=sys.stderr)
        return 2

    try:
        report = copy_database(_sqlite_url(args.source), target, replace=args.replace)
    except MigrationError as exc:
        print(f"迁移未完成：{exc}", file=sys.stderr)
        return 1

    print(f"迁移完成，结构版本 {report.revision}，逐表校验通过：")
    for name, count in report.table_counts.items():
        print(f"  {name}: {count} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
