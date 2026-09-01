# -*- coding: utf-8 -*-
"""
CLI 入口自动化测试（精简版：只覆盖「按清单重命名」）。

运行（在项目根目录）：python -m tests.test_cli
覆盖：预览 dry-run / 真正执行 / 清单缺失 / 清单无有效条目 / .py 入口
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


def run_cli(argv):
    """调用 cli.main()，返回 (exit_code, stdout, stderr)。argparse 缺参抛 SystemExit。"""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse 用法错误
            code = exc.code if isinstance(exc.code, int) else 2
    return code, buf_out.getvalue(), buf_err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for n in ("a.txt", "b.txt"):
            (self.root / n).write_text("x", encoding="utf-8")
        self.dir = str(self.root)

    def _mk_list(self, text):
        lst = self.root / "rename_list.txt"
        lst.write_text(text, encoding="utf-8")
        return str(lst)

    def test_dry_run_does_not_rename(self):
        lst = self._mk_list("a.txt -> aa.txt\nb.txt -> bb.txt\n")
        code, out, err = run_cli([self.dir, "--list", lst])
        self.assertEqual(code, 0, err)
        self.assertIn("aa.txt", out)
        self.assertIn("预览模式（dry-run）", out)
        # 文件未变
        self.assertTrue((self.root / "a.txt").exists())
        self.assertFalse((self.root / "aa.txt").exists())

    def test_apply_renames(self):
        lst = self._mk_list("a.txt -> aa.txt\nb.txt -> bb.txt\n")
        code, out, err = run_cli([self.dir, "--list", lst, "--apply"])
        self.assertEqual(code, 0, err)
        self.assertIn("已重命名 2 项", out)
        self.assertTrue((self.root / "aa.txt").exists())
        self.assertTrue((self.root / "bb.txt").exists())
        self.assertFalse((self.root / "a.txt").exists())

    def test_partial_match_keeps_unmatched(self):
        """清单只命中部分文件：未匹配项保持原名、跳过。"""
        lst = self._mk_list("a.txt -> aa.txt\n")
        code, out, err = run_cli([self.dir, "--list", lst, "--apply"])
        self.assertEqual(code, 0, err)
        self.assertIn("已重命名 1 项", out)
        self.assertTrue((self.root / "aa.txt").exists())
        self.assertTrue((self.root / "b.txt").exists())  # 未匹配保持原名

    def test_gbk_list_encoding(self):
        """GBK 编码清单也能解析（自动编码检测）。"""
        lst = self.root / "gbk_list.txt"
        lst.write_bytes("a.txt -> 甲.txt\n".encode("gbk"))
        code, out, err = run_cli([self.dir, "--list", str(lst), "--apply"])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "甲.txt").exists())

    def test_missing_list_file(self):
        code, _, err = run_cli([self.dir, "--list", str(self.root / "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("读取清单失败", err)

    def test_list_required(self):
        """不带 --list 报错。"""
        code, _, err = run_cli([self.dir])
        self.assertEqual(code, 2)  # argparse 用法错误
        self.assertIn("--list", err)

    def test_empty_list(self):
        lst = self._mk_list("# 只有注释\n")
        code, _, err = run_cli([self.dir, "--list", lst])
        self.assertEqual(code, 1)
        self.assertIn("清单中没有有效", err)

    def test_missing_dir(self):
        lst = self._mk_list("a.txt -> aa.txt\n")
        code, _, err = run_cli([str(self.root / "nope"), "--list", lst])
        self.assertEqual(code, 1)
        self.assertIn("目录不存在", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)