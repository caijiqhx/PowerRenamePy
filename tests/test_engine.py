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
    build_export_text,
    compute_preview,
    flatten_tree,
    load_entries,
    load_tree,
    make_rule,
    parse_rename_list,
    serialize_rules,
    deserialize_rules,
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

    def test_list_mapping_stacks_with_pipeline(self):
        """RULE_LIST 与前后规则叠加：命中=清单覆盖前序，未命中=保留；后续规则继续叠加。"""
        m = {"a.txt": "aa.txt"}
        # 前缀在前、清单在后：命中文件直接用清单名（覆盖前缀）；未命中保留前缀
        r2 = [make_rule(RULE_PREFIX, text="pre_"), make_rule(RULE_LIST, mapping=m)]
        self.assertEqual(transform_name("a.txt", r2), "aa.txt")
        self.assertEqual(transform_name("c.txt", r2), "pre_c.txt")
        # 清单在前、前缀在后：清单名上继续叠加前缀
        r3 = [make_rule(RULE_LIST, mapping=m), make_rule(RULE_PREFIX, text="pre_")]
        self.assertEqual(transform_name("a.txt", r3), "pre_aa.txt")


class TestPreview(unittest.TestCase):
    """注意：让位检查依赖真实磁盘状态，临时目录必须贯穿整个测试生命周期
    （不能放在 with 块内即时销毁，否则 exists() 恒为 False）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    def _mk_entries(self, names):
        root = self.root
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

    def test_swap_names_both_move_ok(self):
        """a→b、b→a 互换：双方都在名单内且都会让位 -> 不算冲突。"""
        _, entries = self._mk_entries(["a.txt", "b.txt"])
        mapping = {"a.txt": "b.txt", "b.txt": "a.txt"}
        preview = compute_preview(entries, [make_rule(RULE_LIST, mapping=mapping)])
        by_old = {it.old_name: it for it in preview}
        self.assertEqual(by_old["a.txt"].status, STATUS_OK)
        self.assertEqual(by_old["a.txt"].new_name, "b.txt")
        self.assertEqual(by_old["b.txt"].status, STATUS_OK)
        self.assertEqual(by_old["b.txt"].new_name, "a.txt")

    def test_target_holds_still_conflict(self):
        """a→b，但 b 在名单内保持原名（不让位）-> 判冲突。"""
        _, entries = self._mk_entries(["a.txt", "b.txt"])
        mapping = {"a.txt": "b.txt", "b.txt": "b.txt"}
        preview = compute_preview(entries, [make_rule(RULE_LIST, mapping=mapping)])
        by_old = {it.old_name: it for it in preview}
        self.assertEqual(by_old["a.txt"].status, STATUS_CONFLICT)
        self.assertEqual(by_old["a.txt"].new_name, "b.txt")
        self.assertEqual(by_old["b.txt"].status, STATUS_UNCHANGED)

    def test_cycle_three_all_move_ok(self):
        """a→b、b→c、c→a 三角换位：全部让位 -> 全部放行。"""
        _, entries = self._mk_entries(["a.txt", "b.txt", "c.txt"])
        mapping = {"a.txt": "b.txt", "b.txt": "c.txt", "c.txt": "a.txt"}
        preview = compute_preview(entries, [make_rule(RULE_LIST, mapping=mapping)])
        self.assertEqual([it.status for it in preview],
                         [STATUS_OK, STATUS_OK, STATUS_OK])

    def test_chain_broken_cascades_conflict(self):
        """a→b、b→c、c 保持原名：c 不让位 -> b 冲突 -> a 连锁冲突。"""
        _, entries = self._mk_entries(["a.txt", "b.txt", "c.txt"])
        mapping = {"a.txt": "b.txt", "b.txt": "c.txt", "c.txt": "c.txt"}
        preview = compute_preview(entries, [make_rule(RULE_LIST, mapping=mapping)])
        by_old = {it.old_name: it for it in preview}
        self.assertEqual(by_old["c.txt"].status, STATUS_UNCHANGED)
        self.assertEqual(by_old["b.txt"].status, STATUS_CONFLICT)
        self.assertEqual(by_old["a.txt"].status, STATUS_CONFLICT)

    def test_cross_dir_same_new_name_ok(self):
        """不同目录的条目改成同名：各自目录都无占用 -> 全部 OK。"""
        for nr in ("sub1", "sub2"):
            (self.root / nr).mkdir(exist_ok=True)
            (self.root / nr / ("a.txt" if nr == "sub1" else "b.txt")).write_text("x", encoding="utf-8")
        entries = load_entries(self.root, recursive=True)
        mapping = {"a.txt": "x.txt", "b.txt": "x.txt"}
        preview = compute_preview(entries, [make_rule(RULE_LIST, mapping=mapping)])
        by_old = {it.old_name: it for it in preview}
        self.assertEqual(by_old["a.txt"].status, STATUS_OK)
        self.assertEqual(by_old["b.txt"].status, STATUS_OK)

    def test_cross_dir_same_old_holder_all_yield_ok(self):
        """同名 x.txt 在多个目录都存在：都让位成 y，另一目录内的 a.txt 换入 x -> 全部 OK。
        RULE_LIST 按纯文件名匹配，x.txt→y.txt 会命中所有目录的 x.txt，都让位。"""
        for rel in ("dir1/x.txt", "dir2/x.txt", "dir1/a.txt"):
            p = self.root / rel
            p.parent.mkdir(exist_ok=True)
            p.write_text("x", encoding="utf-8")
        entries = load_entries(self.root, recursive=True)
        mapping = {"x.txt": "y.txt", "a.txt": "x.txt"}
        preview = compute_preview(entries, [make_rule(RULE_LIST, mapping=mapping)])
        by_old = {it.old_name: it for it in preview}
        self.assertEqual(by_old["x.txt"].status, STATUS_OK)   # 每个目录的 x 都让位
        self.assertEqual(len([it for it in preview if it.old_name == "x.txt"]), 2)
        self.assertEqual(by_old["a.txt"].status, STATUS_OK)   # 换入 dir1 让出的空位


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


class TestSerializeRules(unittest.TestCase):
    def test_roundtrip(self):
        rules = [
            make_rule(RULE_PREFIX, text="pre_"),
            make_rule(RULE_REPLACE, search="old", replace="new", case_sensitive=True),
            make_rule(RULE_LIST, mapping={"a.txt": "b.txt"}),
        ]
        restored = deserialize_rules(serialize_rules(rules))
        self.assertEqual(len(restored), 3)
        for orig, r in zip(rules, restored):
            self.assertEqual(orig.rule_type, r.rule_type)
            self.assertEqual(orig.params, r.params)

    def test_unknown_type_skipped(self):
        # 序列化里混入未知类型 -> 反序列化应跳过
        rules = deserialize_rules(
            '[{"rule_type":"unknown","params":{}},'
            '{"rule_type":"prefix","params":{"text":"x"}}]')
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].rule_type, RULE_PREFIX)

    def test_invalid_json_returns_empty(self):
        self.assertEqual(deserialize_rules("not json"), [])
        self.assertEqual(deserialize_rules("{bad"), [])
        self.assertEqual(deserialize_rules('{"a":1}'), [])  # 非列表
        self.assertEqual(deserialize_rules(""), [])

    def test_missing_params_filled_with_defaults(self):
        rules = deserialize_rules('[{"rule_type":"number","params":{}}]')
        self.assertEqual(rules[0].params["start"], 1)
        self.assertEqual(rules[0].params["step"], 1)
        self.assertEqual(rules[0].params["digits"], 2)

    def test_unknown_param_filtered(self):
        rules = deserialize_rules(
            '[{"rule_type":"prefix","params":{"text":"hi","evil":"x"}}]')
        self.assertEqual(rules[0].params, {"text": "hi"})


class TestExport(unittest.TestCase):
    def _mk_entries(self, names):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for n in names:
                (root / n).write_text("x", encoding="utf-8")
            return load_entries(root, recursive=False)

    def test_template_no_rules(self):
        entries = self._mk_entries(["a.txt", "b.txt"])
        text = build_export_text(entries)
        lines = [l for l in text.splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith(" → "))
        self.assertTrue(any(l.startswith("a.txt") for l in lines))

    def test_with_rules_shows_new_names(self):
        entries = self._mk_entries(["a.txt", "b.txt"])
        r = make_rule(RULE_PREFIX, text="pre_")
        text = build_export_text(entries, [r])
        lines = [l for l in text.splitlines() if l.strip()]
        self.assertIn("a.txt → pre_a.txt", lines)
        self.assertIn("b.txt → pre_b.txt", lines)

    def test_empty(self):
        self.assertEqual(build_export_text([]), "")
        self.assertEqual(build_export_text([], []), "")


class TestLoadTree(unittest.TestCase):
    def _mk_tree(self, structure):
        """structure: dict {path: is_dir}，suffix '/' 表示目录。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, is_dir in structure.items():
                p = root / rel
                if is_dir:
                    p.mkdir(parents=True, exist_ok=True)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("x", encoding="utf-8")
            yield root

    def test_flatten_nested_structure(self):
        for root in self._mk_tree({
            "a.txt": False,
            "sub/b.txt": False,
            "sub/deep/c.txt": False,
            "sub/deep/dir/": True,
            "sub/deep/dir/e.txt": False,
        }):
            tree = load_tree(root, recursive=True)
            nodes = flatten_tree(tree)
            # root + a.txt + sub + b.txt + deep + c.txt + dir + e.txt = 8
            self.assertEqual(len(nodes), 8)
            names = [n.name for n in nodes]
            self.assertIn("a.txt", names)
            self.assertIn("sub", names)
            self.assertIn("deep", names)
            self.assertIn("dir", names)
            self.assertIn("e.txt", names)

    def test_non_recursive(self):
        for root in self._mk_tree({
            "a.txt": False,
            "sub/b.txt": False,
        }):
            tree = load_tree(root, recursive=False)
            nodes = flatten_tree(tree)
            # 根 + a.txt + sub(不展开) = 3
            self.assertEqual(len(nodes), 3)
            sub = [n for n in nodes if n.name == "sub"][0]
            self.assertEqual(sub.children, [])

    def test_renameable_defaults(self):
        for root in self._mk_tree({
            "a.txt": False,
            "sub/b.txt": False,
        }):
            tree = load_tree(root, recursive=True)
            nodes = {n.name: n for n in flatten_tree(tree)}
            self.assertTrue(nodes["a.txt"].renameable)      # 文件默认可改名
            self.assertFalse(nodes["sub"].renameable)       # 目录默认不改名
            self.assertTrue(nodes["b.txt"].renameable)
            self.assertFalse(nodes[root.name].renameable)

    def test_exclude_dirs_flag(self):
        for root in self._mk_tree({
            "a.txt": False,
            "sub/b.txt": False,
        }):
            tree = load_tree(root, recursive=True, include_dirs=False)
            nodes = {n.name: n for n in flatten_tree(tree)}
            self.assertFalse(nodes["sub"].renameable)
            self.assertTrue(nodes["a.txt"].renameable)

    def test_include_dirs_flag(self):
        for root in self._mk_tree({
            "a.txt": False,
            "sub/b.txt": False,
        }):
            tree = load_tree(root, recursive=True, include_dirs=True)
            nodes = {n.name: n for n in flatten_tree(tree)}
            self.assertTrue(nodes["sub"].renameable)

    def test_include_files_filter(self):
        for root in self._mk_tree({
            "keep.txt": False,
            "skip.txt": False,
        }):
            tree = load_tree(root, recursive=False, inc_text="keep")
            nodes = {n.name: n for n in flatten_tree(tree)}
            self.assertTrue(nodes["keep.txt"].renameable)
            self.assertFalse(nodes["skip.txt"].renameable)
            # 结构节点仍然保留
            self.assertIn("skip.txt", nodes)

    def test_exclude_filter(self):
        for root in self._mk_tree({
            "keep.txt": False,
            "skip.txt": False,
        }):
            tree = load_tree(root, recursive=False, exc_text="skip")
            nodes = {n.name: n for n in flatten_tree(tree)}
            self.assertTrue(nodes["keep.txt"].renameable)
            self.assertFalse(nodes["skip.txt"].renameable)

    def test_dir_structure_preserved_after_filter(self):
        for root in self._mk_tree({
            "sub/keep.txt": False,
            "sub/skip.txt": False,
        }):
            tree = load_tree(root, recursive=True, inc_text="keep")
            nodes = {n.name: n for n in flatten_tree(tree)}
            self.assertTrue(nodes["keep.txt"].renameable)
            self.assertFalse(nodes["skip.txt"].renameable)
            # 目录 sub 即使内部有过滤掉的条目也保留
            self.assertIn("sub", nodes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
