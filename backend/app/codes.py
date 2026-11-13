"""兑换码生成器。

用法（在 backend/ 目录下）：
    python -m app.codes 10                  # 生成 10 个一次性码
    python -m app.codes 5 --uses 3          # 每个码可用 3 次
    python -m app.codes --list              # 查看已生成的码
    python -m app.codes 1 --note "内测群"    # 打备注，方便回溯

码本身不进数据库以外的任何地方；发出去之后谁兑的都能在 redemptions 里查到。
"""

from __future__ import annotations

import argparse
import secrets
import sys

from . import auth, db

# 去掉容易看错的 0/O/1/I
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _block(n: int = 4) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def _unique_code() -> str:
    while True:
        code = f"QNTY-{_block()}-{_block()}"
        if not any(row["code"] == code for row in auth.list_codes()):
            return code


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Tether Pro 兑换码")
    parser.add_argument("count", nargs="?", type=int, default=0, help="生成多少个")
    parser.add_argument("--uses", type=int, default=1, help="每个码可用次数，默认 1")
    parser.add_argument("--note", default="", help="备注")
    parser.add_argument("--list", action="store_true", help="列出已有兑换码")
    args = parser.parse_args()

    db.init_db()

    if args.list or args.count <= 0:
        rows = auth.list_codes()
        if not rows:
            print("还没有兑换码。用 `python -m app.codes 10` 生成。")
            return 0
        print(f"{'CODE':22} {'PLAN':6} {'USED':>8}  {'NOTE'}")
        for row in rows:
            print(f"{row['code']:22} {row['plan']:6} {row['used']}/{row['max_uses']:>5}  {row['note']}")
        return 0

    for _ in range(args.count):
        print(auth.create_code(_unique_code(), plan="pro", max_uses=args.uses, note=args.note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
