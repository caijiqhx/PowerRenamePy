//! 名称变换流水线（对齐 Python 版 transform_name / _apply_rule 语义）。

use crate::rules::{Rule, Scope};

/// 按顺序依次应用全部规则，返回新文件名。
pub fn transform_name(name: &str, rules: &[Rule]) -> String {
    let mut cur = name.to_string();
    for rule in rules {
        if cur.is_empty() {
            break;
        }
        cur = apply_rule(&cur, rule);
    }
    cur
}

/// 应用单条规则（仅 Replace / Regex，带 scope 作用范围）。
pub fn apply_rule(name: &str, rule: &Rule) -> String {
    if name.is_empty() {
        return name.to_string();
    }

    // Replace / Regex 均支持作用范围；先拆主名/扩展名
    let (stem, ext) = split_ext(name);
    let (target, tail): (String, String) = match rule.scope() {
        Scope::Full => (name.to_string(), String::new()),
        Scope::Stem => (stem.clone(), ext),
        Scope::Ext => (ext.clone(), String::new()),
    };

    let transformed = match rule {
        Rule::Replace {
            search,
            replace,
            case_sensitive,
            ..
        } => {
            if search.is_empty() {
                return name.to_string();
            }
            apply_replace(&target, search, replace, *case_sensitive)
        }
        Rule::Regex { pattern, replace, .. } => {
            if pattern.is_empty() {
                return name.to_string();
            }
            apply_regex(&target, pattern, replace)
        }
    };

    match rule.scope() {
        Scope::Full => transformed,
        Scope::Stem => format!("{transformed}{tail}"),
        Scope::Ext => format!("{stem}{transformed}"),
    }
}

fn split_ext(name: &str) -> (String, String) {
    // 对齐 Python os.path.splitext：最后一个点之后为扩展名（含点），无点时 ext 为空
    match name.rfind('.') {
        Some(pos) if pos > 0 => (name[..pos].to_string(), name[pos..].to_string()),
        _ => (name.to_string(), String::new()),
    }
}

/// 字面量查找替换；case_sensitive=false 时忽略大小写（不区分大小写的替换）。
fn apply_replace(target: &str, search: &str, replace: &str, case_sensitive: bool) -> String {
    if case_sensitive {
        return target.replace(search, replace);
    }

    // 忽略大小写：使用 regex crate（ASCII case-insensitive），替换为字面量 replace。
    // 注意：Python 版用 re.sub(re.escape(search), lambda: repl, flags=IGNORECASE)，
    // 语义为"把 search 的所有出现（不分大小写）替换成 replace"。
    let pattern = match regex::escape(search) {
        // 构建不区分大小写匹配
        p => format!("(?i:{p})"),
    };
    match regex::Regex::new(&pattern) {
        Ok(re) => re.replace_all(target, replace).into_owned(),
        Err(_) => target.to_string(),
    }
}

/// 正则替换：pattern 匹配替换为 replace。
/// 无效正则（用户输入错误）时保持原名不变（对齐 Python 的 try/except re.error → return name）。
fn apply_regex(target: &str, pattern: &str, replace: &str) -> String {
    match fancy_regex::Regex::new(pattern) {
        Ok(re) => re.replace_all(target, replace).into_owned(),
        Err(_) => target.to_string(), // 编译失败（非法正则）→ 保持原名
    }
}

impl Rule {
    fn scope(&self) -> Scope {
        match self {
            Rule::Replace { scope, .. } | Rule::Regex { scope, .. } => *scope,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rules::Rule;

    fn replace(search: &str, replace: &str) -> Rule {
        Rule::Replace {
            search: search.into(),
            replace: replace.into(),
            case_sensitive: false,
            scope: Scope::Full,
        }
    }

    fn regex(pattern: &str, replace: &str) -> Rule {
        Rule::Regex {
            pattern: pattern.into(),
            replace: replace.into(),
            scope: Scope::Full,
        }
    }

    #[test]
    fn replace_basic() {
        assert_eq!(transform_name("a.txt", &[replace("a", "b")]), "b.txt");
    }

    #[test]
    fn replace_not_found() {
        assert_eq!(transform_name("a.txt", &[replace("zzz", "b")]), "a.txt");
    }

    #[test]
    fn replace_ignore_case() {
        assert_eq!(transform_name("ABC.txt", &[replace("abc", "x")]), "x.txt");
    }

    #[test]
    fn replace_case_sensitive() {
        let r = Rule::Replace {
            search: "abc".into(),
            replace: "x".into(),
            case_sensitive: true,
            scope: Scope::Full,
        };
        assert_eq!(transform_name("ABC.txt", &[r.clone()]), "ABC.txt"); // 大小写敏感不匹配
        assert_eq!(transform_name("abc.txt", &[r]), "x.txt");
    }

    #[test]
    fn regex_basic() {
        assert_eq!(transform_name("file01.txt", &[regex(r"\d+", "#")]), "file#.txt");
    }

    #[test]
    fn regex_root_lookaround() {
        // 支持 lookbehind（Python re 支持，fancy-regex 也支持）
        assert_eq!(transform_name("abc123", &[regex(r"(?<=\d)\d", "X")]), "abc1XX");
    }

    #[test]
    fn regex_invalid_pattern_keeps_name() {
        assert_eq!(transform_name("a.txt", &[regex("[", "x")]), "a.txt");
    }

    #[test]
    fn regex_empty_pattern_keeps_name() {
        assert_eq!(transform_name("a.txt", &[regex("", "x")]), "a.txt");
    }

    #[test]
    fn scope_stem_only() {
        let r = Rule::Replace {
            search: "a".into(),
            replace: "x".into(),
            case_sensitive: false,
            scope: Scope::Stem,
        };
        // 只改主名，扩展名不动
        assert_eq!(transform_name("a.txt", &[r]), "x.txt");
    }

    #[test]
    fn scope_ext_only() {
        let r = Rule::Replace {
            search: "txt".into(),
            replace: "md".into(),
            case_sensitive: false,
            scope: Scope::Ext,
        };
        assert_eq!(transform_name("a.txt", &[r]), "a.md");
    }
}