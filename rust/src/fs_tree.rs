//! 文件系统目录树加载（对齐 Python 版 load_tree / flatten_tree / load_entries）。

use std::fs;
use std::path::{Path, PathBuf};

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

/// 加载目录树。根节点始终保留、renameable=false；子目录仅 include_dirs 且名称筛选通过才可改名；
/// 文件仅 include_files 且名称筛选通过才可改名；递归遍历受 recursive/深度控制。
pub fn load_tree(dirpath: &Path, opts: &LoadOptions) -> TreeNode {
    let name = dirpath
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| dirpath.to_string_lossy().into_owned());
    let root = TreeNode {
        path: dirpath.to_path_buf(),
        name,
        is_dir: true,
        renameable: false,
        children: Vec::new(),
    };
    let mut root = root;

    let max_depth = if opts.recursive { -1 } else { 0 };

    fn name_ok(name: &str, opts: &LoadOptions) -> bool {
        if !opts.inc_text.is_empty() {
            if !match_text(name, &opts.inc_text, opts.use_regex) {
                return false;
            }
        }
        if !opts.exc_text.is_empty() {
            if match_text(name, &opts.exc_text, opts.use_regex) {
                return false;
            }
        }
        true
    }

    fn walk(node: &mut TreeNode, limit_depth: i32, opts: &LoadOptions) {
        let entries = match fs::read_dir(&node.path) {
            Ok(it) => it.flatten().collect::<Vec<_>>(),
            Err(_) => return,
        };
        // 排序：目录优先，其次名称不区分大小写（对齐 Python sorted(key=(not is_dir, name.lower)))
        let mut sorted: Vec<_> = entries.iter().map(|de| (de.path(), de.file_name().to_string_lossy().into_owned())).collect();
        sorted.sort_by(|a, b| {
            let a_dir = a.0.is_dir();
            let b_dir = b.0.is_dir();
            (b_dir, a.1.to_lowercase()).cmp(&(a_dir, b.1.to_lowercase()))
        });
        for (child_path, child_name) in sorted {
            let is_dir = child_path.is_dir();
            let mut child = TreeNode {
                path: child_path,
                name: child_name.clone(),
                is_dir,
                renameable: false,
                children: Vec::new(),
            };
            if is_dir {
                child.renameable = opts.include_dirs && name_ok(&child_name, opts);
                if limit_depth != 0 {
                    walk(&mut child, limit_depth - 1, opts);
                }
            } else {
                child.renameable = opts.include_files && name_ok(&child_name, opts);
            }
            node.children.push(child);
        }
    }

    walk(&mut root, max_depth, opts);
    root
}

/// 深度展平树（先序：父节点在前，目录优先）。
pub fn flatten_tree(node: &TreeNode, out: &mut Vec<TreeNode>) {
    out.push(node.clone());
    for c in &node.children {
        flatten_tree(c, out);
    }
}

/// load_entries：加载目录条目并按筛选条件过滤（对齐 Python 版）。
fn match_text(name: &str, text: &str, use_regex: bool) -> bool {
    if use_regex {
        match fancy_regex::Regex::new(text) {
            Ok(re) => re.is_match(name).unwrap_or(false),
            Err(_) => false,
        }
    } else {
        name.contains(text)
    }
}

pub fn load_entries(dirpath: &Path, opts: &LoadOptions) -> Vec<FileEntry> {
    if !dirpath.is_dir() {
        return Vec::new();
    }
    let iter: Box<dyn Iterator<Item = PathBuf>> = if opts.recursive {
        Box::new(recursive_iter(dirpath))
    } else {
        Box::new(iterdir(dirpath))
    };

    let mut entries = Vec::new();
    for path in iter {
        let is_dir = path.is_dir();
        if is_dir && !opts.include_dirs {
            continue;
        }
        if !is_dir && !opts.include_files {
            continue;
        }
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
    entries.sort_by(|a, b| (b.is_dir, a.name.to_lowercase()).cmp(&(a.is_dir, b.name.to_lowercase())));
    entries
}

fn iterdir(dirpath: &Path) -> impl Iterator<Item = PathBuf> {
    fs::read_dir(dirpath)
        .map(|it| it.flatten().map(|de| de.path()).collect::<Vec<_>>().into_iter())
        .unwrap_or_else(|_| Vec::new().into_iter())
}

/// 递归遍历（rglob 语义，深度优先）。
fn recursive_iter(dirpath: &Path) -> impl Iterator<Item = PathBuf> {
    let mut out = Vec::new();
    fn collect(dirpath: &Path, out: &mut Vec<PathBuf>) {
        let Ok(rd) = fs::read_dir(dirpath) else { return };
        for de in rd.flatten() {
            let p = de.path();
            out.push(p.clone());
            if p.is_dir() {
                collect(&p, out);
            }
        }
    }
    collect(dirpath, &mut out);
    out.into_iter()
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
        // 目录 a 有 b/ root.txt z.txt A.txt；A.txt 排在 z.txt 前（不区分大小写）
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