//! 预览与冲突检测（对齐 Python 版 compute_preview 语义）。
//!
//! 冲突判定按「目录」分组：同名占用只在同一目录内成立，跨目录同名互不影响。
//! 组内三层判断：目标重名 → 磁盘占用 → 让位检查 + 依赖链反向传播。

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::Path;

use crate::fs_tree::FileEntry;
use crate::rules::Rule;
use crate::transform::transform_name;

/// 预览状态（对齐 Python STATUS_*）
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PreviewStatus {
    Ok,
    Unchanged,
    Conflict,
    Error,
}

/// 一条预览结果
#[derive(Debug, Clone)]
pub struct PreviewItem {
    pub entry: FileEntry,
    pub old_name: String,
    pub new_name: String,
    pub status: PreviewStatus,
    pub note: String,
}

impl PreviewItem {
    /// 是否“实际会执行改名”（对齐 Python STATUS_OK 语义）
    pub fn will_rename(&self) -> bool {
        self.status == PreviewStatus::Ok
    }
}

/// 检查 Windows 非法字符：`<>:"/\|?*` 及控制字符（对齐 Python has_invalid_chars）
pub fn has_invalid_chars(name: &str) -> bool {
    name.chars()
        .any(|c| matches!(c, '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*') || (c as u32) < 0x20)
}

/// 计算预览与冲突检测。
pub fn compute_preview(entries: &[FileEntry], rules: &[Rule]) -> Vec<PreviewItem> {
    let n = entries.len();
    // 每个条目只转换一次
    let transformed: Vec<String> = entries
        .iter()
        .enumerate()
        .map(|(i, e)| transform_name(&e.name, rules)) // index 参数 Python 用于编号规则，本项目无 → 忽略
        .collect();

    let mut statuses = vec![PreviewStatus::Ok; n];
    let mut notes = vec![String::new(); n];

    // 按目录分组：目录 -> 组内条目索引
    let mut by_dir: HashMap<&Path, Vec<usize>> = HashMap::new();
    for (i, e) in entries.iter().enumerate() {
        by_dir.entry(e.path.parent().unwrap_or(Path::new(""))).or_default().push(i);
    }

    for idxs in by_dir.values() {
        // 原名 -> 条目索引（组内）
        let mut index_by_old: HashMap<&str, usize> = HashMap::new();
        for &idx in idxs {
            index_by_old.insert(entries[idx].name.as_str(), idx);
        }

        let mut owners: HashMap<String, String> = HashMap::new(); // 新名 -> 声明者原名（组内）
        let mut dependents: HashMap<usize, Vec<usize>> = HashMap::new(); // 让位者 -> 依赖它的条目

        for &idx in idxs {
            let e = &entries[idx];
            let new_name = transformed[idx].clone();
            if new_name.is_empty() {
                statuses[idx] = PreviewStatus::Error;
                notes[idx] = "转换结果为空".to_string();
            } else if new_name == e.name {
                statuses[idx] = PreviewStatus::Unchanged;
                notes[idx] = "名称未变化".to_string();
            } else if has_invalid_chars(&new_name) {
                statuses[idx] = PreviewStatus::Error;
                notes[idx] = "包含非法字符".to_string();
            } else if owners.contains_key(&new_name) {
                let other = owners[&new_name].clone();
                statuses[idx] = PreviewStatus::Conflict;
                notes[idx] = format!("与「{other}」目标重名");
            } else if e.path.parent().unwrap_or(Path::new("")).join(&new_name).exists() {
                match index_by_old.get(new_name.as_str()) {
                    Some(&holder) => {
                        // 目标原名主在本目录名单内：换入依赖其让位（能否让位由传播阶段裁决）
                        dependents.entry(holder).or_default().push(idx);
                        owners.insert(new_name.clone(), e.name.clone());
                    }
                    None => {
                        statuses[idx] = PreviewStatus::Conflict;
                        notes[idx] = "磁盘上已存在同名文件".to_string();
                    }
                }
            } else {
                owners.insert(new_name.clone(), e.name.clone());
            }
        }

        // 组内冲突传播：一切不会执行改名的条目（unchanged/error/conflict）都不会让位，
        // 依赖它们让位的条目随之判冲突，并沿依赖链继续反向传播到链头。
        let mut queue: VecDeque<usize> = idxs
            .iter()
            .copied()
            .filter(|&i| statuses[i] != PreviewStatus::Ok)
            .collect();
        let mut done: HashSet<usize> = queue.iter().copied().collect();
        while let Some(holder) = queue.pop_front() {
            if let Some(dep_list) = dependents.get(&holder) {
                for &j in dep_list {
                    if done.contains(&j) {
                        continue;
                    }
                    statuses[j] = PreviewStatus::Conflict;
                    notes[j] = format!("依赖「{}」让位，但其改名无法执行", entries[holder].name);
                    done.insert(j);
                    queue.push_back(j);
                }
            }
        }
    }

    entries
        .iter()
        .enumerate()
        .map(|(i, e)| PreviewItem {
            entry: e.clone(),
            old_name: e.name.clone(),
            new_name: transformed[i].clone(),
            status: statuses[i],
            note: notes[i].clone(),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fs_tree::load_entries;
    use crate::fs_tree::LoadOptions;
    use std::fs;

    fn make_tmp(tag: &str, files: &[&str]) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "pr_prv_{tag}_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        for f in files {
            let p = dir.join(f);
            if let Some(parent) = p.parent() {
                fs::create_dir_all(parent).unwrap();
            }
            fs::write(p, "").unwrap();
        }
        dir
    }

    fn replace(search: &str, replace: &str) -> Rule {
        Rule::Replace {
            search: search.into(),
            replace: replace.into(),
            case_sensitive: false,
            scope: crate::rules::Scope::Full,
        }
    }

    fn regex(pattern: &str, replace: &str) -> Rule {
        Rule::Regex {
            pattern: pattern.into(),
            replace: replace.into(),
            scope: crate::rules::Scope::Full,
        }
    }

    #[test]
    fn no_rules_all_unchanged() {
        let dir = make_tmp("norules", &["a.txt"]);
        let entries = load_entries(&dir, &LoadOptions::default());
        let items = compute_preview(&entries, &[]);
        assert_eq!(items[0].status, PreviewStatus::Unchanged);
        assert_eq!(items[0].new_name, "a.txt");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn simple_rename_ok() {
        let dir = make_tmp("ok", &["a.txt"]);
        let entries = load_entries(&dir, &LoadOptions::default());
        let items = compute_preview(&entries, &[replace("a", "b")]);
        assert_eq!(items[0].status, PreviewStatus::Ok);
        assert_eq!(items[0].new_name, "b.txt");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn same_dir_duplicate_target_conflict() {
        // 对齐 Python test_conflict_duplicate_target：正则把两个文件都换成同名 → 目标重名
        let dir = make_tmp("dup", &["a.txt", "b.txt"]);
        let entries = load_entries(&dir, &LoadOptions::default());
        let items = compute_preview(&entries, &[regex("^.*$", "same")]);
        let statuses: Vec<_> = items.iter().map(|i| i.status).collect();
        assert!(statuses.contains(&PreviewStatus::Conflict));
        assert_eq!(statuses.iter().filter(|s| **s == PreviewStatus::Ok).count(), 1);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn hold_still_chain_conflict() {
        // 对齐 Python 实测 golden：a→b、b→c（磁盘占位链，c 保持原名）
        // → a=conflict(依赖 c 让位), b=conflict(与 a 目标重名), c=unchanged
        let dir = make_tmp("chain", &["a.txt", "b.txt", "c.txt"]);
        let entries = load_entries(&dir, &LoadOptions::default());
        let items = compute_preview(&entries, &[replace("a", "b"), replace("b", "c")]);
        let by_old: std::collections::HashMap<&str, &PreviewItem> =
            items.iter().map(|i| (i.old_name.as_str(), i)).collect();
        assert_eq!(by_old["c.txt"].status, PreviewStatus::Unchanged);
        assert_eq!(by_old["b.txt"].status, PreviewStatus::Conflict);
        assert_eq!(by_old["a.txt"].status, PreviewStatus::Conflict);
        assert!(by_old["a.txt"].note.contains("让位"));
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn cross_dir_same_new_name_ok() {
        // 不同目录改成同名：各自目录都无占用 → 全部 OK（对齐 Python 场景3）
        let dir = make_tmp("cross", &["sub1/a.txt", "sub2/a.txt"]);
        let entries = load_entries(&dir, &LoadOptions::default());
        let items = compute_preview(&entries, &[replace("a", "x")]);
        assert!(items.iter().all(|i| i.status == PreviewStatus::Ok));
        assert!(items.iter().all(|i| i.new_name == "x.txt"));
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn invalid_chars() {
        assert!(has_invalid_chars("a<b"));
        assert!(has_invalid_chars("a\\b"));
        assert!(!has_invalid_chars("a b.txt"));
    }
}