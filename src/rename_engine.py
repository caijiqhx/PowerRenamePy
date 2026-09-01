# -*- coding: utf-8 -*-
"""
PowerRenamePy 核心重命名引擎。

职责：
1. 规则定义（查找替换 / 正则 / 大小写 / 前缀后缀 / 序列编号 / 改扩展名 / 字符清理）
2. 名称转换流水线：对每个文件名按顺序依次应用全部规则
3. 预览计算与冲突检测（重名 / 非法字符 / 目标已存在）
4. 两阶段执行重命名（支持 a->b、b->a 互换与链式改名），出错自动回滚
5. 撤销管理器（内存栈）
6. 目录加载与筛选

设计要点：
- 引擎层不依赖 tkinter，纯标准库，可单独测试、可复用为 CLI。
- 规则是线性流水线：第 N 条规则的输出是第 N+1 条规则的输入，
  与 Windows PowerRename 的“多模式同时应用”不同，但更直观、更好预测。
- 规则参数统一放在 RenameRule.params 字典中，字段定义见 RULE_TYPES，
  GUI 依据字段定义自动生成表单，引擎与界面解耦。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 状态常量
STATUS_OK = "ok"
STATUS_UNCHANGED = "unchanged"
STATUS_CONFLICT = "conflict"
STATUS_ERROR = "error"

STATUS_LABELS = {
    STATUS_OK: "就绪",
    STATUS_UNCHANGED: "无变化",
    STATUS_CONFLICT: "冲突",
    STATUS_ERROR: "错误",
}

# ---------------------------------------------------------------- 规则类型
RULE_REPLACE = "replace"    # 查找并替换
RULE_REGEX = "regex"        # 正则替换
RULE_CASE = "case"          # 大小写转换
RULE_PREFIX = "prefix"      # 添加前缀
RULE_SUFFIX = "suffix"      # 添加后缀（主名之后、扩展名之前）
RULE_NUMBER = "number"      # 序列编号
RULE_EXT = "ext"            # 替换扩展名
RULE_STRIP = "strip"        # 移除指定字符
RULE_TRIM = "trim"          # 压缩空白
RULE_LIST = "list"          # 按导入清单重命名（原名→新名）

# 作用范围
SCOPE_FULL = "full"
SCOPE_STEM = "stem"
SCOPE_EXT = "ext"

# Windows 文件名非法字符（含控制字符）
_INVALID_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class FieldDef:
    """规则参数字段定义，GUI 据此自动生成表单控件。"""
    key: str
    label: str
    kind: str = "str"            # str | int | bool | choice
    options: Optional[List[Tuple[str, str]]] = None  # [(value, label)]
    default: object = ""


@dataclass
class RuleTypeDef:
    id: str
    label: str
    fields: List[FieldDef]


CASE_OPTIONS = [
    ("lower", "全部小写"),
    ("upper", "全部大写"),
    ("title", "首字母大写（Title Case）"),
    ("capitalize", "仅首字符大写"),
]

SCOPE_OPTIONS = [
    (SCOPE_FULL, "整个文件名"),
    (SCOPE_STEM, "仅主名（不含扩展名）"),
    (SCOPE_EXT, "仅扩展名"),
]

POS_OPTIONS = [
    ("prefix", "作为前缀"),
    ("suffix", "作为后缀"),
]

RULE_TYPES: List[RuleTypeDef] = [
    RuleTypeDef(RULE_REPLACE, "查找并替换", [
        FieldDef("search", "查找文本", "str", default=""),
        FieldDef("replace", "替换为", "str", default=""),
        FieldDef("scope", "作用范围", "choice", SCOPE_OPTIONS, SCOPE_FULL),
        FieldDef("case_sensitive", "区分大小写", "bool", default=False),
    ]),
    RuleTypeDef(RULE_REGEX, "正则替换", [
        FieldDef("search", "正则表达式", "str", default=""),
        FieldDef("replace", "替换为（支持 \\1 捕获组）", "str", default=""),
        FieldDef("scope", "作用范围", "choice", SCOPE_OPTIONS, SCOPE_FULL),
    ]),
    RuleTypeDef(RULE_CASE, "大小写转换", [
        FieldDef("mode", "转换方式", "choice", CASE_OPTIONS, "lower"),
        FieldDef("scope", "作用范围", "choice", SCOPE_OPTIONS, SCOPE_FULL),
    ]),
    RuleTypeDef(RULE_PREFIX, "添加前缀", [
        FieldDef("text", "前缀文本", "str", default=""),
    ]),
    RuleTypeDef(RULE_SUFFIX, "添加后缀", [
        FieldDef("text", "后缀文本", "str", default=""),
    ]),
    RuleTypeDef(RULE_NUMBER, "序列编号", [
        FieldDef("pos", "插入位置", "choice", POS_OPTIONS, "suffix"),
        FieldDef("start", "起始值", "int", default=1),
        FieldDef("step", "步长", "int", default=1),
        FieldDef("digits", "位数（不足补零）", "int", default=2),
        FieldDef("sep", "分隔符", "str", default=" "),
    ]),
    RuleTypeDef(RULE_EXT, "替换扩展名", [
        FieldDef("text", "新扩展名（可带点）", "str", default="txt"),
    ]),
    RuleTypeDef(RULE_STRIP, "移除字符", [
        FieldDef("chars", "要移除的字符（逐个）", "str", default="-_"),
        FieldDef("scope", "作用范围", "choice", SCOPE_OPTIONS, SCOPE_FULL),
    ]),
    RuleTypeDef(RULE_TRIM, "压缩空白", [
        FieldDef("underscore", "用下划线代替空格", "bool", default=False),
    ]),
    RuleTypeDef(RULE_LIST, "按清单重命名", [
        FieldDef("mapping", "清单映射（原名→新名）", "map", default={}),
    ]),
]

TYPE_BY_ID = {t.id: t for t in RULE_TYPES}


@dataclass
class RenameRule:
    rule_type: str
    params: Dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------- 数据结构
@dataclass
class FileEntry:
    path: Path
    name: str
    is_dir: bool


@dataclass
class TreeNode:
    """目录树节点：目录用于体现结构（始终显示），renameable 决定是否参与改名。"""
    path: Path
    name: str
    is_dir: bool
    renameable: bool = False
    children: List["TreeNode"] = field(default_factory=list)


@dataclass
class PreviewItem:
    entry: FileEntry
    old_name: str
    new_name: str
    status: str
    note: str = ""


@dataclass
class ApplyResult:
    logs: List[Tuple[Path, Path]]  # 已完成的 (old, new)，供撤销使用
    errors: List[str]
    rolled_back: bool = False


# ---------------------------------------------------------------- 工具函数
def default_rule(rule_type: str) -> RenameRule:
    tdef = TYPE_BY_ID[rule_type]
    params = {f.key: f.default for f in tdef.fields}
    return RenameRule(rule_type, params)


def make_rule(rule_type: str, **params) -> RenameRule:
    rule = default_rule(rule_type)
    rule.params.update(params)
    return rule


# ---------------------------------------------------------------- 规则方案序列化
def serialize_rules(rules: List[RenameRule]) -> str:
    """把规则列表序列化为 JSON 字符串（含规则类型与参数），用于保存/分享方案。"""
    payload = [
        {"rule_type": r.rule_type, "params": r.params}
        for r in rules
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def deserialize_rules(text: str) -> List[RenameRule]:
    """
    从 JSON 字符串恢复规则列表。
    - 忽略未知/不支持的类型（如旧版本删除的规则）
    - 缺失的参数用缺省值补齐
    - 无效 JSON / 非列表则返回空列表
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, list):
        return []

    rules: List[RenameRule] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        rt = item.get("rule_type")
        if rt not in TYPE_BY_ID:
            continue
        rule = default_rule(rt)
        params = item.get("params")
        if isinstance(params, dict):
            # 仅接受该规则类型定义的字段，避免混入非法参数
            allowed = {f.key for f in TYPE_BY_ID[rt].fields}
            for k, v in params.items():
                if k in allowed:
                    rule.params[k] = v
        rules.append(rule)
    return rules


def rule_summary(rule: RenameRule) -> str:
    p = rule.params
    rt = rule.rule_type
    if rt == RULE_REPLACE:
        return f"查找「{p.get('search','')}」→「{p.get('replace','')}」"
    if rt == RULE_REGEX:
        return f"/{p.get('search','')}/ → 「{p.get('replace','')}」"
    if rt == RULE_CASE:
        mode = dict(CASE_OPTIONS).get(p.get("mode", "lower"), "小写")
        return f"转为{mode}"
    if rt == RULE_PREFIX:
        return f"前缀「{p.get('text','')}」"
    if rt == RULE_SUFFIX:
        return f"后缀「{p.get('text','')}」"
    if rt == RULE_NUMBER:
        pos = "前缀" if p.get("pos", "suffix") == "prefix" else "后缀"
        digits = int(p.get("digits", 2))
        start = int(p.get("start", 1))
        step = int(p.get("step", 1))
        return f"编号（{pos}）{start:0{digits}d} 步长 {step}"
    if rt == RULE_EXT:
        return f"扩展名改为「{p.get('text','')}」"
    if rt == RULE_STRIP:
        return f"移除字符「{p.get('chars','')}」"
    if rt == RULE_TRIM:
        return "压缩空白" + ("（转下划线）" if p.get("underscore") else "")
    if rt == RULE_LIST:
        return f"按清单重命名（{len(p.get('mapping', {}))} 项）"
    return rt


def has_invalid_chars(name: str) -> bool:
    return bool(_INVALID_NAME_RE.search(name))


# ---------------------------------------------------------------- 清单解析
# 分隔符：箭头、Tab、逗号、分号、竖线、2+ 连续空格（CSV 制表符）。支持行尾注释（#）。
_RENAME_LIST_SEPARATORS = re.compile(r"\s*[→,;|\t]\s*|->|=>|,|\t|\s{2,}")
_RENAME_LIST_COMMENT_RE = re.compile(r"^\s*#|#\s*$")


def read_text_auto_encoding(path, fallback: str = "utf-8") -> str:
    """读取文本文件，自动识别编码（优先 UTF-8-sig / UTF-8，失败回退 GBK，再回退指定编码）。"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk", fallback):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode(fallback, errors="replace")


def parse_rename_list(text: str) -> Dict[str, str]:
    """
    解析「原名→新名」清单文本，返回 {原名: 新名} 映射。
    每行一条；分隔符支持 → / -> / => / Tab / 逗号 / 分号 / 竖线 / 连续 2+ 空格。
    文件第一行若含表头字样（old/原名/旧名/from）则跳过。
    未匹配清单的文件保持原名。（引擎层不处理文件系统，仅做文本解析）
    """
    mapping: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _RENAME_LIST_COMMENT_RE.search(line):
            continue
        # 兼容第一行表头（常见：old,new 等）
        if not mapping and re.match(r"^(old|from|原名|旧名|源名)\b", line, re.IGNORECASE):
            continue
        parts = _RENAME_LIST_SEPARATORS.split(line, maxsplit=1)
        if len(parts) == 2:
            old, new = parts[0].strip(), parts[1].strip()
            if old and new:
                mapping[old] = new
    return mapping


# ---------------------------------------------------------------- 名称转换
def _split_ext(name: str) -> Tuple[str, str]:
    return os.path.splitext(name)  # ("main", ".txt")；无扩展名时 ext == ""


def _apply_rule(name: str, rule: RenameRule, index: int, original_name: Optional[str] = None) -> str:
    rt = rule.rule_type
    p = rule.params
    if not name:
        return name

    if rt == RULE_PREFIX:
        return str(p.get("text", "")) + name

    if rt == RULE_SUFFIX:
        stem, ext = _split_ext(name)
        return stem + str(p.get("text", "")) + ext

    if rt == RULE_NUMBER:
        num = int(p.get("start", 1)) + index * int(p.get("step", 1))
        digits = max(1, int(p.get("digits", 2)))
        num_str = str(num).zfill(digits)
        sep = str(p.get("sep", " "))
        if p.get("pos", "suffix") == "prefix":
            return num_str + sep + name
        stem, ext = _split_ext(name)
        return stem + sep + num_str + ext

    if rt == RULE_EXT:
        stem, _ = _split_ext(name)
        ext = str(p.get("text", "")).strip()
        if ext and not ext.startswith("."):
            ext = "." + ext
        return stem + ext

    if rt == RULE_TRIM:
        out = re.sub(r"\s+", " ", name).strip()
        if p.get("underscore"):
            out = out.replace(" ", "_")
        return out

    if rt == RULE_LIST:
        # 按清单重命名：始终用「原始文件名」匹配清单。命中则采用清单新名
        # （后续规则继续在其上叠加）；未命中则保持当前流水线值。
        key = original_name if original_name is not None else name
        mapped = str(p.get("mapping", {}).get(key, key))
        return mapped if mapped != key else name

    # 以下规则支持作用范围
    scope = p.get("scope", SCOPE_FULL)
    stem, ext = _split_ext(name)

    if scope == SCOPE_STEM:
        target, tail = stem, ext
    elif scope == SCOPE_EXT:
        target, tail = ext, ""
    else:
        target, tail = name, ""

    if rt == RULE_REPLACE:
        search = str(p.get("search", ""))
        if not search:
            return name
        repl = str(p.get("replace", ""))
        if p.get("case_sensitive"):
            target = target.replace(search, repl)
        else:
            target = re.sub(re.escape(search), lambda _m: repl, target, flags=re.IGNORECASE)

    elif rt == RULE_REGEX:
        pattern = str(p.get("search", ""))
        if not pattern:
            return name
        try:
            target = re.sub(pattern, str(p.get("replace", "")), target)
        except re.error:
            return name

    elif rt == RULE_CASE:
        mode = p.get("mode", "lower")
        if mode == "upper":
            target = target.upper()
        elif mode == "title":
            target = target.title()
        elif mode == "capitalize":
            target = target[:1].upper() + target[1:]
        else:
            target = target.lower()

    elif rt == RULE_STRIP:
        for ch in str(p.get("chars", "")):
            target = target.replace(ch, "")

    else:
        return name

    # 拼回（full 时 tail 为空，target 即完整名）
    if scope == SCOPE_STEM:
        return target + tail
    if scope == SCOPE_EXT:
        return stem + target
    return target


def transform_name(name: str, rules: List[RenameRule], index: int = 0) -> str:
    """按顺序依次应用全部规则，返回新文件名。

    各规则处理的是前一条规则输出（线性流水线）；RULE_LIST（按清单重命名）
    例外：始终用「传入的原始文件名」做清单匹配，命中则从清单新名继续。
    """
    original = name
    for rule in rules:
        name = _apply_rule(name, rule, index, original_name=original)
        if not name:
            break
    return name


# ---------------------------------------------------------------- 导出清单
def build_export_text(entries: List[FileEntry], rules: Optional[List[RenameRule]] = None) -> str:
    """
    生成供「导入清单」使用的清单文本。

    - rules 为空：每行「原名 → 」（模板，填上新名即可导入）
    - rules 非空：每行「原名 → 新名」，新名为规则预览结果（忽略 conflict/error 项）
    返回文本以 \n 结尾；无条目时返回空字符串。
    """
    if not entries:
        return ""
    if rules:
        preview = compute_preview(entries, rules)
        lines = []
        for it in preview:
            if it.status == STATUS_OK:
                lines.append(f"{it.old_name} → {it.new_name}")
            else:
                lines.append(f"{it.old_name} → ")
    else:
        lines = [f"{e.name} → " for e in entries]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 预览与冲突检测
def compute_preview(entries: List[FileEntry], rules: List[RenameRule]) -> List[PreviewItem]:
    results: List[PreviewItem] = []
    new_name_owners: Dict[str, str] = {}
    old_set = {e.name for e in entries}

    for i, e in enumerate(entries):
        new_name = transform_name(e.name, rules, i)
        status = STATUS_OK
        note = ""

        if not new_name:
            status = STATUS_ERROR
            note = "转换结果为空"
        elif new_name == e.name:
            status = STATUS_UNCHANGED
            note = "名称未变化"
        elif has_invalid_chars(new_name):
            status = STATUS_ERROR
            note = "包含非法字符"
        elif new_name in new_name_owners:
            status = STATUS_CONFLICT
            note = f"与「{new_name_owners[new_name]}」目标重名"
        elif (e.path.parent / new_name).exists() and new_name not in old_set:
            status = STATUS_CONFLICT
            note = "磁盘上已存在同名文件"
        else:
            new_name_owners[new_name] = e.name

        results.append(PreviewItem(e, e.name, new_name, status, note))

    return results


# ---------------------------------------------------------------- 执行与撤销
def apply_renames(items: List[Tuple[Path, Path]]) -> ApplyResult:
    """
    两阶段执行重命名：
      阶段一：所有 old -> 临时名（同目录，UUID 后缀）
      阶段二：所有临时名 -> new
    这样任意 a->b、b->a 互换或链式改名都能成功，不会因中间名被占用而失败。
    任一阶段出错则尽力回滚所有已完成的改名。
    """
    staged: List[Tuple[Path, Path, Path]] = []  # (old, tmp, new)
    logs: List[Tuple[Path, Path]] = []
    errors: List[str] = []

    try:
        for old, new in items:
            if old == new:
                continue
            tmp = old.with_name(old.name + f".__pr_{uuid.uuid4().hex[:6]}")
            old.rename(tmp)
            staged.append((old, tmp, new))

        for old, tmp, new in staged:
            tmp.rename(new)
            logs.append((old, new))

    except OSError as exc:
        errors.append(f"{getattr(exc, 'filename', '')} : {exc.strerror or exc}")
        # 尽力回滚
        for old, tmp, new in reversed(staged):
            final = tmp.parent / new.name
            try:
                if final.exists():
                    final.rename(old)
                elif tmp.exists():
                    tmp.rename(old)
            except OSError:
                pass
        return ApplyResult(logs=[], errors=errors, rolled_back=True)

    return ApplyResult(logs=logs, errors=errors)


class UndoManager:
    """内存撤销栈：每次成功应用压入一条 (old, new) 列表；undo 反向恢复。"""

    def __init__(self) -> None:
        self.stack: List[List[Tuple[Path, Path]]] = []

    def push(self, logs: List[Tuple[Path, Path]]) -> None:
        if logs:
            self.stack.append(logs)

    @property
    def can_undo(self) -> bool:
        return bool(self.stack)

    def undo(self) -> Tuple[int, List[str]]:
        """撤销最近一次应用，返回 (成功条数, 错误列表)。"""
        if not self.stack:
            return (0, [])
        logs = self.stack.pop()
        errors: List[str] = []
        done = 0
        for old, new in reversed(logs):
            try:
                if new.exists():
                    new.rename(old)
                    done += 1
                else:
                    errors.append(f"找不到目标：{new}")
            except OSError as exc:
                errors.append(f"{new} : {exc.strerror or exc}")
        return done, errors


# ---------------------------------------------------------------- 目录加载
def load_tree(
    dirpath,
    recursive: bool = True,
    include_files: bool = True,
    include_dirs: bool = False,
    inc_text: str = "",
    exc_text: str = "",
    use_regex: bool = False,
) -> TreeNode:
    """
    以树形结构加载目录（体现子目录层级）。

    - 目录节点始终保留（便于展示结构），是否参与改名由 include_dirs 决定。
    - 文件/文件夹是否“可参与改名”由 include_files/include_dirs 及名称筛选决定。
    - inc_text/exc_text 作用于名称（目录按自身名筛选；递归时不因目录未匹配而剪枝）。
    筛选后无内容的空目录也会保留（结构展示）。
    """
    root = TreeNode(p := Path(dirpath), p.name or str(p), True, renameable=False)

    def name_ok(name: str) -> bool:
        if inc_text:
            try:
                hit = re.search(inc_text, name) if use_regex else inc_text in name
            except re.error:
                hit = False
            if not hit:
                return False
        if exc_text:
            try:
                hit = re.search(exc_text, name) if use_regex else exc_text in name
            except re.error:
                hit = False
            if hit:
                return False
        return True

    def walk(node: TreeNode, limit_depth: int) -> None:
        try:
            with os.scandir(node.path) as it:
                entries = list(it)  # DirEntry：is_dir() 复用缓存，避免重复 stat
        except OSError:
            return
        paths = sorted(entries, key=lambda d: (not d.is_dir(), d.name.lower()))
        for de in paths:
            try:
                is_dir = de.is_dir()
            except OSError:
                continue
            child_path = node.path / de.name
            child = TreeNode(child_path, de.name, is_dir)
            if is_dir:
                child.renameable = include_dirs and name_ok(de.name)
                if limit_depth != 0:
                    walk(child, limit_depth - 1)
                node.children.append(child)
            else:
                child.renameable = include_files and name_ok(de.name)
                node.children.append(child)

    walk(root, -1 if recursive else 0)
    return root


def flatten_tree(node: TreeNode) -> List[TreeNode]:
    """深度遍历树，返回全部节点（含目录结构节点与可改名文件）。"""
    out: List[TreeNode] = []
    _walk(node, out)
    return out


def _walk(node: TreeNode, out: List[TreeNode]) -> None:
    out.append(node)
    for c in node.children:
        _walk(c, out)


def load_entries(
    dirpath,
    recursive: bool = True,
    include_files: bool = True,
    include_dirs: bool = False,
    inc_text: str = "",
    exc_text: str = "",
    use_regex: bool = False,
) -> List[FileEntry]:
    """加载目录条目并按筛选条件过滤（文件名匹配）。"""
    p = Path(dirpath)
    if not p.is_dir():
        return []

    def match(name: str, text: str) -> bool:
        if use_regex:
            try:
                return re.search(text, name) is not None
            except re.error:
                return False
        return text in name

    entries: List[FileEntry] = []
    try:
        iterator = p.rglob("*") if recursive else p.iterdir()
        for path in iterator:
            try:
                is_dir = path.is_dir()
            except OSError:
                continue
            if is_dir and not include_dirs:
                continue
            if not is_dir and not include_files:
                continue
            name = path.name
            if inc_text and not match(name, inc_text):
                continue
            if exc_text and match(name, exc_text):
                continue
            entries.append(FileEntry(path, name, is_dir))
    except OSError:
        return []

    entries.sort(key=lambda e: (e.is_dir, e.name.lower()))
    return entries
