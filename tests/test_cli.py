# -*- coding: utf-8 -*-
"""
CLI 入口自动化测试。

运行（在项目根目录）：python -m tests.test_cli
覆盖：预览 dry-run / 真正执行 / 方案加载 / 清单导入 / 内联规则 / 目录筛选 / .py 入口
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

# cli.py 是入口脚本（平级导入 rename_engine），把 src/ 加进 sys.path 后作为顶层模块导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cli  # noqa: E402
from rename_engine import RULE_LIST, RULE_PREFIX, make_rule, serialize_rules  # noqa: E402


def run_cli(argv):
    """调用 cli.main()，返回 (exit_code, stdout, stderr)。"""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        code = cli.main(argv)
    return code, buf_out.getvalue(), buf_err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for n in ("a.txt", "b.txt"):
            (self.root / n).write_text("x", encoding="utf-8")
        self.dir = str(self.root)

    def test_dry_run_does_not_rename(self):
        code, out, err = run_cli([self.dir, "--prefix", "IMG_"])
        self.assertEqual(code, 0, err)
        self.assertIn("IMG_a.txt", out)
        self.assertIn("预览模式（dry-run）", out)
        # 文件未变
        self.assertTrue((self.root / "a.txt").exists())
        self.assertFalse((self.root / "IMG_a.txt").exists())

    def test_apply_renames(self):
        code, out, err = run_cli([self.dir, "--prefix", "IMG_", "--apply"])
        self.assertEqual(code, 0, err)
        self.assertIn("已重命名 2 项", out)
        self.assertTrue((self.root / "IMG_a.txt").exists())
        self.assertTrue((self.root / "IMG_b.txt").exists())
        self.assertFalse((self.root / "a.txt").exists())

    def test_preset_and_list(self):
        # 方案文件：前缀
        preset = self.root / "p.json"
        preset.write_text(serialize_rules([make_rule(RULE_PREFIX, text="P_")]),
                          encoding="utf-8")
        # 清单文件：改名
        lst = self.root / "m.txt"
        lst.write_text("a.txt -> aa.txt\n", encoding="utf-8")
        code, out, err = run_cli([self.dir, "--preset", str(preset),
                                  "--list", str(lst), "--apply"])
        self.assertEqual(code, 0, err)
        # 前缀 + 清单：命中 a.txt -> aa.txt（清单覆盖前缀）；b.txt 未命中 -> P_b.txt
        self.assertTrue((self.root / "aa.txt").exists())
        self.assertTrue((self.root / "P_b.txt").exists())
        self.assertFalse((self.root / "a.txt").exists())

    def test_inline_rules_number(self):
        code, out, err = run_cli([self.dir, "--prefix", "N_", "--number",
                                  "--start", "10", "--step", "2", "--digits", "3",
                                  "--apply"])
        self.assertEqual(code, 0, err)
        names = sorted(p.name for p in self.root.iterdir() if p.is_file())
        # 前缀 + 后缀编号叠加：N_a.txt -> N_a 010.txt？否——编号规则插入主名后/扩展名前
        # 实际顺序：prefix("N_") -> number(suffix)  => "N_a 010.txt"
        self.assertEqual(names, ["N_a 010.txt", "N_b 012.txt"], names)

    def test_filter_include_exclude(self):
        code, out, err = run_cli([self.dir, "--include", "a", "--prefix", "X_"])
        self.assertEqual(code, 0, err)
        self.assertIn("X_a.txt", out)
        self.assertNotIn("X_b.txt", out)

    def test_missing_dir(self):
        code, _, err = run_cli([str(self.root / "nope"), "--prefix", "X"])
        self.assertEqual(code, 1)
        self.assertIn("目录不存在", err)

    def test_no_rules(self):
        code, _, err = run_cli([self.dir])
        self.assertEqual(code, 1)
        self.assertIn("没有指定任何规则", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)