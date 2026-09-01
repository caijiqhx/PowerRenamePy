# -*- coding: utf-8 -*-
"""
PowerRenamePy 命令行入口 —— 复用核心引擎，无需 GUI。

用法示例：
    # 预览（dry-run，默认不真正改名）
    python src/cli.py /path/to/dir --prefix IMG_
    python src/cli.py /path/to/dir --replace old new --number
    python src/cli.py /path/to/dir --preset preset.json
    python src/cli.py /path/to/dir --list rename_list.txt

    # 真正执行（加 --apply）
    python src/cli.py /path/to/dir --prefix IMG_ --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rename_engine import (
    RULE_CASE,
    RULE_EXT,
    RULE_LIST,
    RULE_NUMBER,
    RULE_PREFIX,
    RULE_REGEX,
    RULE_REPLACE,
    RULE_STRIP,
    RULE_SUFFIX,
    RULE_TRIM,
    STATUS_LABELS,
    STATUS_OK,
    apply_renames,
    compute_preview,
    deserialize_rules,
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
        description="批量重命名工具（命令行版）：复用核心引擎，支持预案/清单/内联规则。",
    )
    p.add_argument("directory", help="目标文件夹路径")
    # ---- 规则来源 ----
    p.add_argument("--preset", metavar="FILE", help="加载规则方案（.json，GUI 保存的文件）")
    p.add_argument("--list", metavar="FILE", help="导入重命名清单（txt/csv，原名→新名）")
    # ---- 内联规则（按固定顺序叠加：preset → 快捷规则 → list）----
    p.add_argument("--prefix", metavar="TEXT", help="添加前缀")
    p.add_argument("--suffix", metavar="TEXT", help="添加后缀（主名之后、扩展名之前）")
    p.add_argument("--replace", nargs=2, metavar=("OLD", "NEW"), help="查找并替换")
    p.add_argument("--regex", nargs=2, metavar=("PATTERN", "REPL"), help="正则替换（支持 \\1）")
    p.add_argument("--lower", action="store_true", help="转小写")
    p.add_argument("--upper", action="store_true", help="转大写")
    p.add_argument("--title", action="store_true", help="Title Case")
    p.add_argument("--ext", metavar="EXT", help="替换扩展名（可带点）")
    p.add_argument("--number", action="store_true", help="追加序列编号")
    p.add_argument("--start", type=int, default=1, help="编号起始值（默认 1）")
    p.add_argument("--step", type=int, default=1, help="编号步长（默认 1）")
    p.add_argument("--digits", type=int, default=2, help="编号位数补零（默认 2）")
    p.add_argument("--sep", default=" ", help="编号分隔符（默认空格）")
    p.add_argument("--strip", metavar="CHARS", help="移除指定字符（逐个）")
    p.add_argument("--trim", action="store_true", help="压缩连续空白")
    p.add_argument("--underscore", action="store_true",
                   help="与 --trim 搭配：空格转下划线")
    # ---- 目录筛选 ----
    p.add_argument("--recursive", action="store_true", default=True,
                   help="递归子目录（默认开，配 --no-recursive 关闭）")
    p.add_argument("--no-recursive", dest="recursive", action="store_false")
    p.add_argument("--include", metavar="TEXT", default="", help="仅包含匹配的文件名")
    p.add_argument("--exclude", metavar="TEXT", default="", help="排除匹配的文件名")
    p.add_argument("--only-files", action="store_true", default=True,
                   help="只处理文件（默认，与 --dirs 互斥）")
    p.add_argument("--dirs", dest="only_files", action="store_false",
                   help="只处理文件夹")
    p.add_argument("--regex-filter", action="store_true",
                   help="--include/--exclude 按正则匹配")
    # ---- 执行 ----
    p.add_argument("--apply", action="store_true",
                   help="真正执行重命名（默认预览 dry-run）")
    return p


def build_rules(args: argparse.Namespace) -> list:
    """按顺序组装规则：preset 方案 → 内联快捷规则 → 清单规则。"""
    rules: list = []

    if args.preset:
        text = read_text_auto_encoding(args.preset)
        rules.extend(deserialize_rules(text))

    if args.prefix is not None:
        rules.append(make_rule(RULE_PREFIX, text=args.prefix))
    if args.suffix is not None:
        rules.append(make_rule(RULE_SUFFIX, text=args.suffix))
    if args.replace:
        rules.append(make_rule(RULE_REPLACE, search=args.replace[0],
                               replace=args.replace[1]))
    if args.regex:
        rules.append(make_rule(RULE_REGEX, search=args.regex[0],
                               replace=args.regex[1]))
    if args.lower:
        rules.append(make_rule(RULE_CASE, mode="lower"))
    if args.upper:
        rules.append(make_rule(RULE_CASE, mode="upper"))
    if args.title:
        rules.append(make_rule(RULE_CASE, mode="title"))
    if args.ext is not None:
        rules.append(make_rule(RULE_EXT, text=args.ext))
    if args.number:
        rules.append(make_rule(RULE_NUMBER, start=args.start, step=args.step,
                               digits=args.digits, sep=args.sep))
    if args.strip is not None:
        rules.append(make_rule(RULE_STRIP, chars=args.strip))
    if args.trim:
        rules.append(make_rule(RULE_TRIM, underscore=args.underscore))

    if args.list:
        text = read_text_auto_encoding(args.list)
        mapping = parse_rename_list(text)
        if mapping:
            rules.append(make_rule(RULE_LIST, mapping=mapping))

    return rules


def main(argv: list | None = None) -> int:
    _fix_console()
    args = build_parser().parse_args(argv)

    if not os.path.isdir(args.directory):
        print(f"错误：目录不存在：{args.directory}", file=sys.stderr)
        return 1

    entries = load_entries(
        args.directory,
        recursive=args.recursive,
        include_files=args.only_files,
        include_dirs=not args.only_files,
        inc_text=args.include,
        exc_text=args.exclude,
        use_regex=args.regex_filter,
    )
    if not entries:
        print("提示：没有符合筛选条件的文件/文件夹。")
        return 0

    rules = build_rules(args)
    if not rules:
        print("错误：没有指定任何规则（可用 --preset / --list / --prefix 等，或 -h 查看帮助）。",
              file=sys.stderr)
        return 1

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