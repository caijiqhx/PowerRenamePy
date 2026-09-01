# -*- coding: utf-8 -*-
"""
PowerRenamePy 引擎自动化测试（无需 GUI）。

运行（在项目根目录）：python -m tests.test_engine
覆盖：规则转换、编号索引、作用范围、冲突检测、两阶段互换重命名、撤销。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from src.rename_engine import (
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
    SCOPE_EXT,
    SCOPE_FULL,
    SCOPE_STEM,
    STATUS_CONFLICT,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNCHANGED,
    UndoManager,
    apply_renames,
    compute_preview,
    load_entries,
    make_rule,
    parse_rename_list,
    transform_name,
)


class TestTransform(unittest.TestCase):
    def test_replace(self):
        r = make_rule(RULE_REPLACE, search="old", replace="new")
        self.assertEqual(transform_name("old_file.txt", [r]), "new_file.txt")

    def test_replace_ignore_case(self):
        r = make_rule(RULE_REPLACE, search="OLD", replace="x")
        self.assertEqual(transform_name("old_file.txt", [r]), "x_file.txt")
        r2 = make_rule(RULE_REPLACE, search="OLD", replace="x", case_sensitive=True)
        self.assertEqual(transform_name("old_file.txt", [r2]), "old_file.txt")

    def test_regex_with_group(self):
        r = make_rule(RULE_REGEX, search=r"^(\d{4})-(\d{2})", replace=r"\1年\2月")
        self.assertEqual(transform_name("2024-05_report.docx", [r]), "2024年05月_report.docx")

    def test_case_lower_upper_title(self):
        r = make_rule(RULE_CASE, mode="upper")
        self.assertEqual(transform_name("Hello World.txt", [r]), "HELLO WORLD.TXT")
        r2 = make_rule(RULE_CASE, mode="title")
        self.assertEqual(transform_name("hello_world.txt", [r2]), "Hello_World.Txt")

    def test_case_scope_stem(self):
        r = make_rule(RULE_CASE, mode="upper", scope=SCOPE_STEM)
        self.assertEqual(transform_name("hello.txt", [r]), "HELLO.txt")
        r2 = make_rule(RULE_CASE, mode="upper", scope=SCOPE_EXT)
        self.assertEqual(transform_name("hello.txt", [r2]), "hello.TXT")

    def test_prefix_suffix(self):
        self.assertEqual(transform_name("a.txt", [make_rule(RULE_PREFIX, text="pre_")]), "pre_a.txt")
        self.assertEqual(transform_name("a.txt", [make_rule(RULE_SUFFIX, text="_v2")]), "a_v2.txt")

    def test_numbering_indexed(self):
        r = make_rule(RULE_NUMBER, pos="prefix", start=10, step=2, digits=3, sep="-")
        names = [transform_name(f"f{i}.txt", [r], i) for i in range(3)]
        self.assertEqual(names, ["010-f0.txt", "012-f1.txt", "014-f2.txt"])
        r2 = make_rule(RULE_NUMBER, pos="suffix", start=1, digits=2, sep=" ")
        self.assertEqual(transform_name("photo.jpg", [r2], 0), "photo 01.jpg")

    def test_ext(self):
        r = make_rule(RULE_EXT, text="png")
        self.assertEqual(transform_name("a.txt", [r]), "a.png")
        r2 = make_rule(RULE_EXT, text=".JPG")
        self.assertEqual(transform_name("a.txt", [r2]), "a.JPG")

    def test_strip(self):
        r = make_rule(RULE_STRIP, chars="-()")
        self.assertEqual(transform_name("a-(b)-c.txt", [r]), "abc.txt")

    def test_trim(self):
        r = make_rule(RULE_TRIM)
        self.assertEqual(transform_name("  a   b  c .txt ", [r]), "a b c .txt")
        r2 = make_rule(RULE_TRIM, underscore=True)
        self.assertEqual(transform_name("a b  c.txt", [r2]), "a_b_c.txt")

    def test_rule_pipeline_order(self):
        rules = [make_rule(RULE_REPLACE, search=" ", replace="_"),
                 make_rule(RULE_CASE, mode="upper")]
        self.assertEqual(transform_name("my file.txt", rules), "MY_FILE.TXT")

    def test_numbering_after_replace(self):
        rules = [make_rule(RULE_REPLACE, search=r"\.[^.]+$", replace=""),
                 make_rule(RULE_NUMBER, pos="suffix", start=1, digits=2, sep="")]
        # replace 不带正则，"\\.[^.]+$" 不会匹配，这里验证普通替换+编号
        rules = [make_rule(RULE_REPLACE, search=".txt", replace=""),
                 make_rule(RULE_NUMBER, pos="suffix", start=1, digits=2, sep="")]
        self.assertEqual(transform_name("a.txt", rules, 0), "a01")

    def test_list_mapping(self):
        mapping = {"a.txt": "aa.txt", "b.txt": "bb.txt"}
        r = make_rule(RULE_LIST, mapping=mapping)
        self.assertEqual(transform_name("a.txt", [r]), "aa.txt")
        self.assertEqual(transform_name("b.txt", [r]), "bb.txt")
        # 未匹配项保持原名
        self.assertEqual(transform_name("c.txt", [r]), "c.txt")

    def test_parse_rename_list_separators(self):
        text = (
            "# 批量改名\n"
            "photo1.jpg → wedding1.jpg\n"
            "photo2.jpg -> wedding2.jpg\n"
            "photo3.jpg\twedding3.jpg\n"
            "photo4.jpg, wedding4.jpg\n"
            "photo5.jpg; wedding5.jpg\n"
            "photo6.jpg | wedding6.jpg\n"
            "photo7.jpg  wedding7.jpg\n"
        )
        m = parse_rename_list(text)
        self.assertEqual(len(m), 7)
        self.assertEqual(m["photo1.jpg"], "wedding1.jpg")
        self.assertEqual(m["photo7.jpg"], "wedding7.jpg")
        # 忽略注释、空行、与重复覆盖
        text2 = "# 头\n\na.txt,b.txt\na.txt,xx.txt\n"
        m2 = parse_rename_list(text2)
        self.assertEqual(m2, {"a.txt": "xx.txt"})

    def test_parse_rename_list_skips_header(self):
        m = parse_rename_list("old,new\na.txt,b.txt\n")
        self.assertEqual(m, {"a.txt": "b.txt"})
        m2 = parse_rename_list("原名\t新名\na.txt\tb.txt\n")
        self.assertEqual(m2, {"a.txt": "b.txt"})

    def test_list_full_flow(self):
        """清单重命名 + 两阶段执行 + 撤销。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for n in ("photo1.jpg", "photo2.jpg"):
                (root / n).write_text("x", encoding="utf-8")
            entries = load_entries(root, recursive=False)
            mapping = {"photo1.jpg": "wedding1.jpg"}
            preview = compute_preview(entries, [make_rule(RULE_LIST, mapping=mapping)])
            self.assertEqual(preview[0].new_name, "wedding1.jpg")
            self.assertEqual(preview[0].status, STATUS_OK)
            # 未匹配项 status 应为 unchanged
            self.assertEqual(preview[1].status, STATUS_UNCHANGED)
            items = [(it.entry.path, it.entry.path.with_name(it.new_name))
                     for it in preview if it.status == STATUS_OK]
            res = apply_renames(items)
            self.assertFalse(res.rolled_back)
            self.assertEqual(len(res.logs), 1)
            self.assertTrue((root / "wedding1.jpg").exists())
            self.assertFalse((root / "photo1.jpg").exists())
            # 撤销
            um = UndoManager()
            um.push(res.logs)
            done, errors = um.undo()
            self.assertEqual(done, 1)
            self.assertTrue((root / "photo1.jpg").exists())
            self.assertFalse((root / "wedding1.jpg").exists())


class TestPreview(unittest.TestCase):
    def _mk_entries(self, names):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for n in names:
                (root / n).write_text("x", encoding="utf-8")
            return [root / n for n in names], load_entries(root, recursive=False)

    def test_ok_and_unchanged(self):
        paths, entries = self._mk_entries(["a.txt", "b.txt"])
        r = make_rule(RULE_PREFIX, text="new_")
        preview = compute_preview(entries, [r])
        self.assertEqual(preview[0].status, STATUS_OK)
        self.assertEqual(preview[0].new_name, "new_a.txt")
        self.assertEqual(preview[1].status, STATUS_OK)

    def test_conflict_duplicate_target(self):
        _, entries = self._mk_entries(["a.txt", "b.txt"])
        # 正则把两个文件名都替换为 same -> 目标重名冲突
        r = make_rule(RULE_REGEX, search=r"^.*$", replace="same")
        preview = compute_preview(entries, [r])
        statuses = [it.status for it in preview]
        self.assertIn(STATUS_CONFLICT, statuses)
        self.assertEqual(statuses.count(STATUS_OK), 1)

    def test_invalid_chars(self):
        _, entries = self._mk_entries(["a.txt"])
        r = make_rule(RULE_PREFIX, text="a:b")
        preview = compute_preview(entries, [r])
        self.assertEqual(preview[0].status, STATUS_ERROR)

    def test_unchanged_detected(self):
        _, entries = self._mk_entries(["a.txt"])
        r = make_rule(RULE_PREFIX, text="")
        preview = compute_preview(entries, [r])
        self.assertEqual(preview[0].status, STATUS_UNCHANGED)


class TestApplyAndUndo(unittest.TestCase):
    def test_apply_basic_and_undo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("1", encoding="utf-8")
            (root / "b.txt").write_text("2", encoding="utf-8")
            entries = load_entries(root, recursive=False)
            r = make_rule(RULE_PREFIX, text="new_")
            preview = compute_preview(entries, [r])
            items = [(it.entry.path, it.entry.path.with_name(it.new_name))
                     for it in preview if it.status == STATUS_OK]
            res = apply_renames(items)
            self.assertFalse(res.rolled_back)
            self.assertEqual(len(res.logs), 2)
            self.assertTrue((root / "new_a.txt").exists())
            self.assertTrue((root / "new_b.txt").exists())
            self.assertFalse((root / "a.txt").exists())

            um = UndoManager()
            um.push(res.logs)
            done, errors = um.undo()
            self.assertEqual(done, 2)
            self.assertFalse(errors)
            self.assertTrue((root / "a.txt").exists())
            self.assertTrue((root / "b.txt").exists())
            self.assertFalse((root / "new_a.txt").exists())

    def test_swap_rename(self):
        """a.txt <-> b.txt 互换：普通顺序改名会失败，两阶段必须成功。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("1", encoding="utf-8")
            (root / "b.txt").write_text("2", encoding="utf-8")
            res = apply_renames([
                (root / "a.txt", root / "b.txt"),
                (root / "b.txt", root / "a.txt"),
            ])
            self.assertFalse(res.rolled_back, f"errors: {res.errors}")
            self.assertEqual(len(res.logs), 2)
            self.assertTrue((root / "a.txt").exists())
            self.assertTrue((root / "b.txt").exists())

    def test_apply_error_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("1", encoding="utf-8")
            (root / "b.txt").write_text("2", encoding="utf-8")
            (root / "target.txt").write_text("3", encoding="utf-8")  # 占用目标名
            # 第一条成功改名到 tmp；第二条目标被占用 -> OSError -> 回滚
            res = apply_renames([
                (root / "a.txt", root / "x.txt"),
                (root / "b.txt", root / "target.txt"),
            ])
            self.assertTrue(res.rolled_back)
            # 回滚后应全部保持原样
            self.assertTrue((root / "a.txt").exists())
            self.assertTrue((root / "b.txt").exists())
            self.assertFalse((root / "x.txt").exists())


class TestLoadEntries(unittest.TestCase):
    def test_load_and_filter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pic1.jpg").write_text("1", encoding="utf-8")
            (root / "pic2.jpg").write_text("1", encoding="utf-8")
            (root / "doc.txt").write_text("1", encoding="utf-8")
            sub = root / "sub"
            sub.mkdir()
            (sub / "deep.png").write_text("1", encoding="utf-8")

            # recursive=True：3 个顶层文件 + 子目录里的 deep.png = 4；文件夹默认排除
            all_ = load_entries(root, recursive=True)
            self.assertEqual(len(all_), 4)
            self.assertTrue(all(not e.is_dir for e in all_))

            inc = load_entries(root, recursive=False, inc_text="pic")
            self.assertEqual([e.name for e in inc], ["pic1.jpg", "pic2.jpg"])

            exc = load_entries(root, recursive=False, exc_text=".txt")
            self.assertEqual([e.name for e in exc], ["pic1.jpg", "pic2.jpg"])

            dirs = load_entries(root, recursive=False, include_dirs=True, include_files=False)
            self.assertEqual([e.name for e in dirs], ["sub"])

            regex = load_entries(root, recursive=False, inc_text=r"pic\d", use_regex=True)
            self.assertEqual([e.name for e in regex], ["pic1.jpg", "pic2.jpg"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
