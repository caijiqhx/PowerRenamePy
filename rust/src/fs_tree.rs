//! 文件系统目录树加载（基于 walkdir，Rust 惯用实现）。
//!
//! 语义对齐 Python 版 load_tree / flatten_tree / load_entries：
//! - 目录节点始终保留（结构展示），renameable 决定是否参与改名
//! - 递归/深度由 walkdir 的 min_depth/max_depth 控制
//! - 排序：目录优先，其次名称不区分大小写

use std::path::{Path, PathBuf};
use walkdir::WalkDir;

/// 树节点：目录始终作为结构节点保留；renameable 决定是否参与改名。
#[derive(Debug, Clone)]
pub struct TreeNode {
    pub path: PathBuf,
    pub name: String,
    pub is_dir: bool,
    pub renameable: bool,
    pub children: Vec<TreeNode>,
}

/// 平铺条目（load_entries 返回）
#[derive(Debug, Clone)]
pub struct FileEntry {
    pub path: PathBuf,
    pub name: String,
    pub is_dir: bool,
}

/// 加载参数
#[derive(Debug, Clone)]
pub struct LoadOptions {
    pub recursive: bool,
    pub include_files: bool,
    pub include_dirs: bool,
    pub inc_text: String,
    pub exc_text: String,
    pub use_regex: bool,
}

impl Default for LoadOptions {
    fn default() -> Self {
        Self {
            recursive: true,
            include_files: true,
            include_dirs: false,
            inc_text: String::new(),
            exc_text: String::new(),
            use_regex: false,
        }
    }
}

/// 名称是否通过包含/排除筛选（对齐 Python name_ok）。
fn name_ok(name: &str, opts: &LoadOptions) -> bool {
    if !opts.inc_text.is_empty() && !match_text(name, &opts.inc_text, opts.use_regex) {
        return false;
    }
    if !opts.exc_text.is_empty() && match_text(name, &opts.exc_text, opts.use_regex) {
        return false;
    }
    true
}

fn match_text(name: &str, text: &str, use_regex: bool) -> bool {
    if use_regex {
        fancy_regex::Regex::new(text).map(|re| re.is_match(name).unwrap_or(false)).unwrap_or(false)
    } else {
        name.contains(text)
    }
}

/// 加载目录树。根节点始终保留、renameable=false；
/// 子目录仅 include_dirs 且名称筛选通过才可改名；文件仅 include_files 且筛选通过。
pub fn load_tree(dirpath: &Path, opts: &LoadOptions) -> TreeNode {
    let root_name = dirpath
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| dirpath.to_string_lossy().into_owned());

    let max_depth = if opts.recursive { usize::MAX } else { 1 }; // 深度 1 = 只读直接子项

    // 先收集全部节点 path，再建树
    let mut nodes: Vec<(PathBuf, bool)> = Vec::new(); // (path, is_dir)
    for entry in WalkDir::new(dirpath).min_depth(1).max_depth(max_depth).follow_links(false) {
        let Ok(entry) = entry else { continue }; // 权限等错误跳过（对齐 Python OSError 跳过）
        let is_dir = entry.file_type().is_dir();
        nodes.push((entry.into_path(), is_dir));
    }

    // 按（目录优先，名称不区分大小写）排序 —— 对齐 Python sorted(key=(not is_dir, name.lower))
    nodes.sort_by(|a, b| {
        let name_a = a.0.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        let name_b = b.0.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        (b.1, name_a.to_lowercase()).cmp(&(a.1, name_b.to_lowercase()))
    });
    fn insert_into(node: &mut TreeNode, path: &Path, is_dir: bool, opts: &LoadOptions) {
        let rel = match path.strip_prefix(&node.path) {
            Ok(r) => r,
            Err(_) => return,
        };
        let mut components = rel.components();
        let Some(first) = components.next() else { return };
        let name = first.as_os_str().to_string_lossy().into_owned();
        let child_full = node.path.join(&name);

        if components.next().is_none() {
            // 直接子项：文件在此决定 renameable；目录统一由 fix_dir_renameable 处理
            let renameable = !is_dir && opts.include_files && name_ok(&name, opts);
            let child = TreeNode {
                path: child_full,
                name,
                is_dir,
                renameable,
                children: Vec::new(),
            };
            if let Some(existing) = node.children.iter_mut().find(|c| c.name == child.name) {
                existing.path = child.path;
                existing.is_dir = child.is_dir;
                existing.renameable = child.renameable;
            } else {
                node.children.push(child);
            }
        } else {
            // 多层路径：递归进入/创建中间目录节点
            if let Some(existing) = node.children.iter_mut().find(|c| c.name == name) {
                insert_into(existing, path, is_dir, opts);
            } else {
                let mut mid = TreeNode {
                    path: child_full,
                    name,
                    is_dir: true,
                    renameable: false,
                    children: Vec::new(),
                };
                insert_into(&mut mid, path, is_dir, opts);
                node.children.push(mid);
            }
        }
    }

    let mut root = TreeNode {
        path: dirpath.to_path_buf(),
        name: root_name,
        is_dir: true,
        renameable: false,
        children: Vec::new(),
    };
    // walkdir 深度先序输出：父路径必然先于子路径出现，直接逐条插入即可
    for (p, is_dir) in &nodes {
        insert_into(&mut root, p, *is_dir, opts);
    }
    // 每层 children 排序：目录在前，名称不区分大小写（对齐 Python 每层 sorted）
    sort_children(&mut root);
    // 目录节点的 renameable：由 include_dirs + name_ok 决定
    fix_dir_renameable(&mut root, opts);
    root
}

/// 递归排序每层 children（目录优先，名称不区分大小写）。
fn sort_children(node: &mut TreeNode) {
    node.children.sort_by(|a, b| {
        (b.is_dir, a.name.to_lowercase()).cmp(&(a.is_dir, b.name.to_lowercase()))
    });
    for c in &mut node.children {
        sort_children(c);
    }
}

/// 递归修正目录节点的 renameable（目录筛选用自身名字判定）。
fn fix_dir_renameable(node: &mut TreeNode, opts: &LoadOptions) {
    for c in &mut node.children {
        if c.is_dir {
            c.renameable = opts.include_dirs && name_ok(&c.name, opts);
            fix_dir_renameable(c, opts);
        }
    }
}

/// 深度展平树（先序：父节点在前，目录优先）。
pub fn flatten_tree(node: &TreeNode, out: &mut Vec<TreeNode>) {
    out.push(node.clone());
    for c in &node.children {
        flatten_tree(c, out);
    }
}

/// load_entries：加载目录条目并按筛选条件过滤（对齐 Python 版）。
pub fn load_entries(dirpath: &Path, opts: &LoadOptions) -> Vec<FileEntry> {
    if !dirpath.is_dir() {
        return Vec::new();
    }
    let max_depth = if opts.recursive { usize::MAX } else { 1 };
    let mut entries = Vec::new();
    for entry in WalkDir::new(dirpath).min_depth(1).max_depth(max_depth).follow_links(false) {
        let Ok(entry) = entry else { continue };
        let is_dir = entry.file_type().is_dir();
        if is_dir && !opts.include_dirs {
            continue;
        }
        if !is_dir && !opts.include_files {
            continue;
        }
        let path = entry.into_path();
        let name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        if !opts.inc_text.is_empty() && !match_text(&name, &opts.inc_text, opts.use_regex) {
            continue;
        }
        if !opts.exc_text.is_empty() && match_text(&name, &opts.exc_text, opts.use_regex) {
            continue;
        }
        entries.push(FileEntry { path, name, is_dir });
    }
    entries.sort_by(|a, b| {
        (b.is_dir, a.name.to_lowercase()).cmp(&(a.is_dir, b.name.to_lowercase()))
    });
    entries
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn mkdir(dir: &Path, rel: &str) -> PathBuf {
        let p = dir.join(rel);
        fs::create_dir_all(&p).unwrap();
        p
    }
    fn touch(dir: &Path, rel: &str) -> PathBuf {
        let p = dir.join(rel);
        fs::write(&p, "").unwrap();
        p
    }

    // 结构： /a/b/x.txt + /a/root.txt + /a/z.txt + /a/A.txt
    fn setup(tmp: &Path) {
        mkdir(tmp, "a/b");
        touch(tmp, "a/b/x.txt");
        touch(tmp, "a/root.txt");
        touch(tmp, "a/z.txt");
        touch(tmp, "a/A.txt");
    }

    #[test]
    fn load_tree_basic() {
        let tmp = std::env::temp_dir().join(format!("pr_ftree_{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        setup(&tmp);
        let opts = LoadOptions::default(); // recursive, include_files
        let root = load_tree(&tmp.join("a"), &opts);
        let names: Vec<&str> = root.children.iter().map(|c| c.name.as_str()).collect();
        assert!(names.contains(&"b"));
        assert!(names.contains(&"root.txt"));
        assert!(names.contains(&"A.txt"));
        assert!(names.contains(&"z.txt"));
        // 文件可改名、目录在 include_dirs=false 下不可改名
        let f = root.children.iter().find(|c| c.name == "root.txt").unwrap();
        assert!(f.renameable);
        let d = root.children.iter().find(|c| c.name == "b").unwrap();
        assert!(!d.renameable);
        // 目录 b 的子节点
        assert!(d.children.iter().any(|c| c.name == "x.txt"));
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn load_tree_include_dirs() {
        let tmp = std::env::temp_dir().join(format!("pr_ftree_dirs_{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        setup(&tmp);
        let opts = LoadOptions { include_dirs: true, ..Default::default() };
        let root = load_tree(&tmp.join("a"), &opts);
        let d = root.children.iter().find(|c| c.name == "b").unwrap();
        assert!(d.renameable); // include_dirs=true 时目录可改名
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn load_tree_non_recursive() {
        let tmp = std::env::temp_dir().join(format!("pr_ftree_nonrec_{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        setup(&tmp);
        let opts = LoadOptions { recursive: false, ..Default::default() };
        let root = load_tree(&tmp.join("a"), &opts);
        // 不递归：不进入 b/
        let d = root.children.iter().find(|c| c.name == "b").unwrap();
        assert!(d.children.is_empty());
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn load_tree_name_filter() {
        let tmp = std::env::temp_dir().join(format!("pr_ftree_filter_{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        setup(&tmp);
        let opts = LoadOptions { inc_text: "root".into(), ..Default::default() };
        let root = load_tree(&tmp.join("a"), &opts);
        let names: Vec<&str> = root.children.iter().map(|c| c.name.as_str()).collect();
        assert!(names.contains(&"root.txt"));
        // 节点保留但重命名为 false（Python 语义：保留结构，仅标记不可改名）
        assert!(root.children.iter().any(|c| c.name == "z.txt" && !c.renameable));
        assert!(root.children.iter().any(|c| c.name == "A.txt" && !c.renameable));
        assert!(root.children.iter().any(|c| c.name == "root.txt" && c.renameable));
        let d = root.children.iter().find(|c| c.name == "b").unwrap();
        assert!(d.children.iter().any(|c| c.name == "x.txt" && !c.renameable));
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn flatten_tree_preorder() {
        let tmp = std::env::temp_dir().join(format!("pr_ftree_flat_{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        setup(&tmp);
        let root = load_tree(&tmp.join("a"), &LoadOptions::default());
        let mut out = Vec::new();
        flatten_tree(&root, &mut out);
        assert_eq!(out.len(), 6); // a + b + 4 files
        assert_eq!(out[0].name, "a"); // 根在前
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn load_entries_basic() {
        let tmp = std::env::temp_dir().join(format!("pr_fentries_{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        setup(&tmp);
        let opts = LoadOptions::default();
        let entries = load_entries(&tmp.join("a"), &opts);
        // 默认不含目录（include_dirs=false），含 4 个文件
        assert!(entries.iter().all(|e| !e.is_dir));
        assert_eq!(entries.len(), 4);
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn load_entries_include_dirs() {
        let tmp = std::env::temp_dir().join(format!("pr_fentries_dirs_{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        setup(&tmp);
        let opts = LoadOptions { include_dirs: true, ..Default::default() };
        let entries = load_entries(&tmp.join("a"), &opts);
        assert!(entries.iter().any(|e| e.is_dir && e.name == "b"));
        fs::remove_dir_all(&tmp).unwrap();
    }
}