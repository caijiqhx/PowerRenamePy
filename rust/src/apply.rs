//! 执行重命名与撤销（对齐 Python 版 apply_renames / UndoManager 语义）。
//!
//! 两阶段改名：所有 old -> 临时名（同目录），再所有临时名 -> new。
//! 这样任意 a->b、b->a 互换或链式改名都能成功；任一阶段出错则尽力回滚。

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static TMP_SEQ: AtomicU64 = AtomicU64::new(0);

/// 执行结果
pub struct ApplyResult {
    pub logs: Vec<(PathBuf, PathBuf)>, // (old, new) 成功改名的记录
    pub errors: Vec<String>,
    pub rolled_back: bool,
}

/// 两阶段执行重命名列表。
/// items: (old_path, new_path) 的列表。
pub fn apply_renames(items: &[(PathBuf, PathBuf)]) -> ApplyResult {
    let mut staged: Vec<(PathBuf, PathBuf, PathBuf)> = Vec::new(); // (old, tmp, new)
    let mut logs: Vec<(PathBuf, PathBuf)> = Vec::new();
    let mut errors: Vec<String> = Vec::new();

    let r = (|| -> Result<(), String> {
        // 阶段一：old -> tmp（同目录，带唯一后缀）
        for (old, new) in items {
            if old == new {
                continue;
            }
            let suffix = format!(
                ".__pr_{}_{:x}",
                std::process::id(),
                TMP_SEQ.fetch_add(1, Ordering::Relaxed)
            );
            let tmp = old.with_file_name(format!(
                "{}{}",
                old.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default(),
                suffix
            ));
            std::fs::rename(old, &tmp).map_err(|e| format!("{} : {}", old.display(), e))?;
            staged.push((old.clone(), tmp, new.clone()));
        }

        // 阶段二：tmp -> new
        for (old, tmp, new) in &staged {
            std::fs::rename(tmp, new).map_err(|e| format!("{} : {}", new.display(), e))?;
            logs.push((old.clone(), new.clone()));
        }
        Ok(())
    })();

    if let Err(msg) = r {
        errors.push(msg);
        // 尽力回滚：reverse(staged)
        for (old, tmp, new) in staged.iter().rev() {
            let final_path = tmp.parent().unwrap_or(Path::new("")).join(
                new.file_name().unwrap_or_default(),
            );
            if final_path.exists() {
                let _ = std::fs::rename(&final_path, old);
            } else if tmp.exists() {
                let _ = std::fs::rename(tmp, old);
            }
        }
        return ApplyResult { logs: Vec::new(), errors, rolled_back: true };
    }

    ApplyResult { logs, errors, rolled_back: false }
}

/// 内存撤销栈。
#[derive(Default)]
pub struct UndoManager {
    stack: VecDeque<Vec<(PathBuf, PathBuf)>>,
}

impl UndoManager {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push(&mut self, logs: Vec<(PathBuf, PathBuf)>) {
        if !logs.is_empty() {
            self.stack.push_back(logs);
        }
    }

    pub fn can_undo(&self) -> bool {
        !self.stack.is_empty()
    }

    /// 撤销最近一次应用，返回 (成功条数, 错误列表)。
    pub fn undo(&mut self) -> (usize, Vec<String>) {
        let Some(logs) = self.stack.pop_back() else { return (0, Vec::new()) };
        let mut errors = Vec::new();
        let mut done = 0;
        for (old, new) in logs.iter().rev() {
            if new.exists() {
                match std::fs::rename(new, old) {
                    Ok(()) => done += 1,
                    Err(e) => errors.push(format!("{} : {}", new.display(), e)),
                }
            } else {
                errors.push(format!("找不到目标：{}", new.display()));
            }
        }
        (done, errors)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn make_tmp(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "pr_app_{tag}_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn apply_basic() {
        let dir = make_tmp("basic");
        fs::write(dir.join("a.txt"), "1").unwrap();
        fs::write(dir.join("b.txt"), "2").unwrap();
        let items = vec![
            (dir.join("a.txt"), dir.join("new_a.txt")),
            (dir.join("b.txt"), dir.join("new_b.txt")),
        ];
        let res = apply_renames(&items);
        assert!(!res.rolled_back);
        assert_eq!(res.logs.len(), 2);
        assert!(dir.join("new_a.txt").exists());
        assert!(dir.join("new_b.txt").exists());
        assert!(!dir.join("a.txt").exists());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn swap_names_both_work() {
        // a<->b 互换：两阶段能成功
        let dir = make_tmp("swap");
        fs::write(dir.join("a.txt"), "1").unwrap();
        fs::write(dir.join("b.txt"), "2").unwrap();
        let items = vec![
            (dir.join("a.txt"), dir.join("b.txt")),
            (dir.join("b.txt"), dir.join("a.txt")),
        ];
        let res = apply_renames(&items);
        assert!(!res.rolled_back);
        assert_eq!(res.logs.len(), 2);
        assert_eq!(fs::read_to_string(dir.join("a.txt")).unwrap(), "2");
        assert_eq!(fs::read_to_string(dir.join("b.txt")).unwrap(), "1");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn undo_restores() {
        let dir = make_tmp("undo");
        fs::write(dir.join("a.txt"), "1").unwrap();
        let items = vec![(dir.join("a.txt"), dir.join("new_a.txt"))];
        let res = apply_renames(&items);
        assert_eq!(res.logs.len(), 1);
        let mut um = UndoManager::new();
        um.push(res.logs);
        assert!(um.can_undo());
        let (done, errors) = um.undo();
        assert_eq!(done, 1);
        assert!(errors.is_empty());
        assert!(dir.join("a.txt").exists());
        assert!(!dir.join("new_a.txt").exists());
        assert!(!um.can_undo());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn undo_no_logs() {
        let mut um = UndoManager::new();
        let (done, errors) = um.undo();
        assert_eq!(done, 0);
        assert!(errors.is_empty());
    }
}