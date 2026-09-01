# -*- coding: utf-8 -*-
"""
PowerRenamePy 命令行入口（精简版）—— 只支持「按清单重命名」。

用法示例：
    # 预览（dry-run，默认不真正改名）
    python src/cli.py /path/to/dir --list rename_list.txt

    # 真正执行
    python src/cli.py /path/to/dir --list rename_list.txt --apply

清单文件每行一条「原名 → 新名」，支持 → / -> / => / Tab / 逗号 / 分号 / 竖线 / 连续空格；
自动识别 UTF-8 / GBK 编码。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rename_engine import (
    RULE_LIST,
    STATUS_LABELS,
    STATUS_OK,
    apply_renames,
    compute_preview,
    load_entries,
    make_rule,
    parse_rename_list,
    read_text_auto_encoding,
)


def _fix_console() -> None:
    """Windows 控制台默认 GBK，强制 UTF-8 输出避免中文乱码。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="PowerRenamePy",
        description="批量重命名工具（命令行版）：按清单重命名。",
    )
    p.add_argument("directory", help="目标文件夹路径")
    p.add_argument("--list", required=True, metavar="FILE",
                   help="重命名清单文件（txt/csv，每行「原名→新名」）")
    # ---- 执行 ----
    p.add_argument("--apply", action="store_true",
                   help="真正执行重命名（默认预览 dry-run）")
    return p


def main(argv: list | None = None) -> int:
    _fix_console()
    args = build_parser().parse_args(argv)

    if not os.path.isdir(args.directory):
        print(f"错误：目录不存在：{args.directory}", file=sys.stderr)
        return 1

    try:
        text = read_text_auto_encoding(args.list)
    except OSError as exc:
        print(f"错误：读取清单失败：{exc}", file=sys.stderr)
        return 1

    mapping = parse_rename_list(text)
    if not mapping:
        print("错误：清单中没有有效的「原名→新名」条目。", file=sys.stderr)
        return 1

    entries = load_entries(args.directory, recursive=True)
    if not entries:
        print("提示：目录中没有可处理的文件。")
        return 0

    rules = [make_rule(RULE_LIST, mapping=mapping)]
    preview = compute_preview(entries, rules)
    ok = [it for it in preview if it.status == STATUS_OK]

    print(f"共 {len(preview)} 项 | 将重命名 {len(ok)} 项"
          f" | 冲突 {sum(1 for x in preview if x.status == 'conflict')}"
          f" | 无变化 {sum(1 for x in preview if x.status == 'unchanged')}"
          f" | 错误 {sum(1 for x in preview if x.status == 'error')}")
    print("-" * 60)
    for it in preview:
        arrow = " -> " if it.new_name != it.old_name else "    "
        print(f"{it.old_name}{arrow}{it.new_name}"
              f"  [{STATUS_LABELS.get(it.status, it.status)}]")

    if not args.apply:
        print("-" * 60)
        print("预览模式（dry-run）：未做任何修改。加 --apply 真正执行。")
        return 0

    if not ok:
        print("没有可执行的重命名（就绪项为 0）。", file=sys.stderr)
        return 1

    items = [(it.entry.path, it.entry.path.with_name(it.new_name)) for it in ok]
    result = apply_renames(items)
    if result.rolled_back:
        print("错误：重命名过程中出错，已自动回滚全部改动：", file=sys.stderr)
        for e in result.errors[:10]:
            print(f"  {e}", file=sys.stderr)
        return 1
    if result.errors:
        print("警告：部分项失败：", file=sys.stderr)
        for e in result.errors[:10]:
            print(f"  {e}", file=sys.stderr)
    print(f"已重命名 {len(result.logs)} 项。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())