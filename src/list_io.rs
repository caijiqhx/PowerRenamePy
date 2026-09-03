//! 清单导入导出。
//!
//! - 导出：标准 CSV（逗号分隔 + 双引号转义），表头「原名,新名」；
//!   rules 为空时第二列预填原名（模板），有规则时填预览新名（冲突/错误留空）。
//! - 导入：优先按标准 CSV 解析，拆不出两列回退通用分隔符（→/->/=>/Tab/分号/竖线/连续 2+ 空格）；
//!   跳过空行、# 注释、表头行（old/原名/旧名/源名）。

use std::collections::HashMap;

use crate::fs_tree::FileEntry;
use crate::preview::{compute_preview, PreviewStatus};
use crate::rules::Rule;

/// 生成导出清单文本（以 \n 结尾；无条目返回空字符串）。
pub fn build_export_text(entries: &[FileEntry], rules: &[Rule]) -> String {
    if entries.is_empty() {
        return String::new();
    }
    let mut out = String::new();
    out.push_str("原名,新名\n");
    if rules.is_empty() {
        for e in entries {
            out.push_str(&csv_escape(&e.name));
            out.push(',');
            out.push_str(&csv_escape(&e.name));
            out.push('\n');
        }
    } else {
        let items = compute_preview(entries, rules);
        for it in &items {
            out.push_str(&csv_escape(&it.old_name));
            out.push(',');
            let new_name = if it.status == PreviewStatus::Ok { &it.new_name } else { "" };
            out.push_str(&csv_escape(new_name));
            out.push('\n');
        }
    }
    out
}

/// 标准 CSV 字段转义（含逗号/引号/换行时加双引号）。
fn csv_escape(field: &str) -> String {
    if field.contains(',') || field.contains('"') || field.contains('\n') || field.contains('\r') {
        let mut s = String::with_capacity(field.len() + 2);
        s.push('"');
        for c in field.chars() {
            if c == '"' {
                s.push('"');
            }
            s.push(c);
        }
        s.push('"');
        s
    } else {
        field.to_string()
    }
}

/// 解析清单文本，返回 {原名: 新名} 映射。
pub fn parse_rename_list(text: &str) -> HashMap<String, String> {
    let mut mapping = HashMap::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        // 跳过 # 注释
        if trimmed.starts_with('#') || trimmed.ends_with('#') {
            continue;
        }

        // 优先按 CSV 拆（两列）
        let fields = parse_csv_line(trimmed);
        let (old, new) = if fields.len() >= 2 {
            (fields[0].trim(), fields[1].trim())
        } else {
            // 回退通用分隔符：→ / -> / => / Tab / 分号 / 竖线 / 连续 2+ 空格
            match split_generic(trimmed) {
                Some((o, n)) => (o.trim(), n.trim()),
                None => continue,
            }
        };

        if old.is_empty() || new.is_empty() {
            continue;
        }
        // 跳过表头（old/from/原名/旧名/源名 开头）
        if mapping.is_empty() && is_header(old) {
            continue;
        }
        mapping.insert(old.to_string(), new.to_string());
    }
    mapping
}

/// 解析单行 CSV（处理双引号包裹字段）。
fn parse_csv_line(line: &str) -> Vec<String> {
    let mut fields = Vec::new();
    let mut cur = String::new();
    let mut chars = line.chars().peekable();
    let mut in_quotes = false;
    while let Some(c) = chars.next() {
        if in_quotes {
            if c == '"' {
                if chars.peek() == Some(&'"') {
                    cur.push('"');
                    chars.next();
                } else {
                    in_quotes = false;
                }
            } else {
                cur.push(c);
            }
        } else if c == '"' {
            in_quotes = true;
        } else if c == ',' {
            fields.push(cur.trim().to_string());
            cur.clear();
        } else {
            cur.push(c);
        }
    }
    fields.push(cur.trim().to_string());
    fields
}

/// 通用分隔符拆分：→ / -> / => / Tab / 分号 / 竖线 / 连续 2+ 空格。
fn split_generic(line: &str) -> Option<(&str, &str)> {
    // 双字符分隔符：-> / =>
    let bytes: Vec<(usize, char)> = line.char_indices().collect();
    for i in 0..bytes.len().saturating_sub(1) {
        let two = format!("{}{}", bytes[i].1, bytes[i + 1].1);
        if two == "->" || two == "=>" {
            return Some((&line[..bytes[i].0], &line[bytes[i + 1].0 + bytes[i + 1].1.len_utf8()..]));
        }
    }
    // 单字符分隔符：→ / Tab / 分号 / 竖线
    for (idx, c) in bytes {
        if c == '→' || c == '\t' || c == ';' || c == '|' {
            return Some((&line[..idx], &line[idx + c.len_utf8()..]));
        }
    }
    // 连续 2+ 空格
    let b = line.as_bytes();
    for i in 0..b.len().saturating_sub(1) {
        if b[i] == b' ' && b[i + 1] == b' ' {
            return Some((&line[..i], &line[i + 2..]));
        }
    }
    None
}

fn is_header(old: &str) -> bool {
    let lower = old.to_lowercase();
    lower == "old" || lower == "from" || lower == "原名" || lower == "旧名" || lower == "源名" || lower.starts_with("old ") || lower.starts_with("from ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn fe(name: &str) -> FileEntry {
        FileEntry { path: PathBuf::from("D:").join(name), name: name.to_string(), is_dir: false }
    }

    #[test]
    fn export_template_no_rules() {
        let text = build_export_text(&[fe("a.txt"), fe("b,c.txt")], &[]);
        assert!(text.starts_with("原名,新名\n"));
        assert!(text.contains("a.txt,a.txt\n"));
        assert!(text.contains("\"b,c.txt\",\"b,c.txt\"\n")); // 含逗号自动加引号
    }

    #[test]
    fn export_with_rules_new_names() {
        let rules = [Rule::Replace {
            search: "a".into(),
            replace: "x".into(),
            case_sensitive: false,
            scope: crate::rules::Scope::Full,
        }];
        let text = build_export_text(&[fe("a.txt")], &rules);
        assert!(text.contains("a.txt,x.txt\n"));
    }

    #[test]
    fn export_empty() {
        assert_eq!(build_export_text(&[], &[]), "");
    }

    #[test]
    fn parse_standard_csv() {
        let m = parse_rename_list("原名,新名\na.txt,b.txt\n\"c,d.txt\",e.txt\n");
        assert_eq!(m.get("a.txt").map(|s| s.as_str()), Some("b.txt"));
        assert_eq!(m.get("c,d.txt").map(|s| s.as_str()), Some("e.txt"));
        assert_eq!(m.len(), 2); // 表头跳过
    }

    #[test]
    fn parse_generic_separators() {
        let m = parse_rename_list("a.txt → b.txt\nc.txt -> d.txt\ne.txt\tf.txt\ng.txt => h.txt");
        assert_eq!(m.get("a.txt").map(|s| s.as_str()), Some("b.txt"));
        assert_eq!(m.get("c.txt").map(|s| s.as_str()), Some("d.txt"));
        assert_eq!(m.get("e.txt").map(|s| s.as_str()), Some("f.txt"));
        assert_eq!(m.get("g.txt").map(|s| s.as_str()), Some("h.txt"));
    }

    #[test]
    fn parse_skips_comments_and_empty() {
        let m = parse_rename_list("# comment\n\na.txt,b.txt\n");
        assert_eq!(m.len(), 1);
        assert_eq!(m.get("a.txt").map(|s| s.as_str()), Some("b.txt"));
    }
}