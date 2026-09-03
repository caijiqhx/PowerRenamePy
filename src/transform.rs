//! 名称变换流水线（规则依次应用）。

use crate::rules::{CaseMode, NumberPos, Rule, Scope};

/// 按顺序依次应用全部规则，返回新文件名。
///
/// 各规则处理的是前一条规则输出（线性流水线）；RULE_LIST（按清单重命名）
/// 例外：始终用「传入的原始文件名」做清单匹配，命中则从清单新名继续。
pub fn transform_name(name: &str, rules: &[Rule]) -> String {
    transform_name_indexed(name, rules, 0)
}

/// 带条目序号（编号规则使用，从 0 开始）。
pub fn transform_name_indexed(name: &str, rules: &[Rule], index: usize) -> String {
    let original = name.to_string();
    transform_name_original(name, rules, &original, index)
}

/// 带原始文件名的变换（内部：流水线每一步都用原始名喂给 List 规则）。
pub fn transform_name_original(name: &str, rules: &[Rule], original: &str, index: usize) -> String {
    let mut cur = name.to_string();
    for rule in rules {
        if cur.is_empty() {
            break;
        }
        cur = apply_rule_original(&cur, rule, original, index);
    }
    cur
}

/// 应用单条规则（List 规则除外）。
pub fn apply_rule(name: &str, rule: &Rule) -> String {
    apply_rule_original(name, rule, name, 0)
}

/// 应用单条规则（带原始文件名与条目序号）。
///
/// 规则应用语义：
/// - 空名直接返回
/// - 前缀/后缀/编号/扩展名/压缩空白/清单 处理整个文件名（前四个不拆分扩展名？——不，
///   后缀/编号·后缀/扩展名 内部会拆分主名与扩展名）
/// - 替换/正则/大小写/移除 支持作用范围（scope）先拆分，再拼回
pub fn apply_rule_original(name: &str, rule: &Rule, original: &str, index: usize) -> String {
    if name.is_empty() {
        return name.to_string();
    }

    match rule {
        Rule::List { mapping } => {
            // 按清单重命名：始终用原始文件名匹配；命中则采用清单新名（后续规则继续叠加），未命中保持原名
            return match mapping.get(original) {
                Some(mapped) if mapped != original => mapped.clone(),
                _ => name.to_string(),
            };
        }

        Rule::Prefix { text } => format!("{text}{name}"),

        Rule::Suffix { text } => {
            let (stem, ext) = split_ext(name);
            format!("{stem}{text}{ext}")
        }

        Rule::Number { pos, start, step, digits, sep } => {
            let num = start + (index as u32) * step;
            let width = if *digits > 0 { *digits as usize } else { 1 };
            let num_str = format!("{:0width$}", num, width = width);
            match pos {
                NumberPos::Prefix => format!("{num_str}{sep}{name}"),
                NumberPos::Suffix => {
                    let (stem, ext) = split_ext(name);
                    format!("{stem}{sep}{num_str}{ext}")
                }
            }
        }

        Rule::Ext { text } => {
            let (stem, _ext) = split_ext(name);
            let ext = text.trim();
            if ext.is_empty() {
                // 空文本 → 去掉扩展名（返回主名）
                return stem;
            }
            let ext = if ext.starts_with('.') { ext.to_string() } else { format!(".{ext}") };
            format!("{stem}{ext}")
        }

        Rule::Trim { underscore } => {
            // re.sub(r"\s+", " ", name).strip() 语义：Unicode 空白（regex crate \s 默认 Unicode）
            let out = regex::Regex::new(r"\s+")
                .map(|re| re.replace_all(name, " ").into_owned())
                .unwrap_or_else(|_| name.to_string());
            // strip 只去首尾的 ASCII/Unicode 空白
            let out = out.trim().to_string();
            if *underscore {
                out.replace(' ', "_")
            } else {
                out
            }
        }

        // 以下规则支持作用范围（scope）：先拆主名/扩展名
        _ => {
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
                Rule::Case { mode, .. } => apply_case(&target, *mode),
                Rule::Strip { chars, .. } => {
                    // 对 chars 中每个字符逐个 replace（保持原有顺序）
                    let mut out = target;
                    for ch in chars.chars() {
                        out = out.replace(ch, "");
                    }
                    out
                }
                _ => unreachable!("scope rules handled above"),
            };

            match rule.scope() {
                Scope::Full => transformed,
                Scope::Stem => format!("{transformed}{tail}"),
                Scope::Ext => format!("{stem}{transformed}"),
            }
        }
    }
}

/// 大小写转换（lower / upper / title / capitalize）。
fn apply_case(target: &str, mode: CaseMode) -> String {
    match mode {
        CaseMode::Lower => target.to_lowercase(),
        CaseMode::Upper => target.to_uppercase(),
        // str.title() 语义：每个“词”首字母大写、其余小写；词边界 = 非字母
        CaseMode::Title => {
            let mut out = String::with_capacity(target.len());
            let mut at_word_start = true;
            for c in target.chars() {
                if c.is_alphabetic() {
                    if at_word_start {
                        out.extend(c.to_uppercase());
                        at_word_start = false;
                    } else {
                        out.extend(c.to_lowercase());
                    }
                } else {
                    at_word_start = true;
                    out.push(c);
                }
            }
            out
        }
        // capitalize() 语义：仅首字符大写，其余不变
        CaseMode::Capitalize => {
            let mut chars = target.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                None => String::new(),
            }
        }
    }
}

fn split_ext(name: &str) -> (String, String) {
    // 类似 os.path.splitext：最后一个点之后为扩展名（含点），无点时 ext 为空
    match name.rfind('.') {
        Some(pos) if pos > 0 => (name[..pos].to_string(), name[pos..].to_string()),
        _ => (name.to_string(), String::new()),
    }
}

/// 字面量查找替换；case_sensitive=false 时忽略大小写。
fn apply_replace(target: &str, search: &str, replace: &str, case_sensitive: bool) -> String {
    if case_sensitive {
        return target.replace(search, replace);
    }

    // 忽略大小写：用 RegexBuilder 构建大小写不敏感匹配（比 (?i:...) 拼接更清晰）
    let pattern = regex::escape(search);
    let re = regex::RegexBuilder::new(&pattern)
        .case_insensitive(true)
        .build();
    match re {
        Ok(re) => re.replace_all(target, replace).into_owned(),
        Err(_) => target.to_string(),
    }
}

/// 正则替换：pattern 匹配替换为 replace。
/// 无效正则（用户输入错误）时保持原名不变（捕获错误 → 返回原名）。
fn apply_regex(target: &str, pattern: &str, replace: &str) -> String {
    match fancy_regex::Regex::new(pattern) {
        Ok(re) => re.replace_all(target, replace).into_owned(),
        Err(_) => target.to_string(), // 编译失败（非法正则）→ 保持原名
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
        // 支持 lookbehind（fancy-regex）
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

    // -------------------------------------------------------- A3~A9 规则

    fn case(scope: Scope, mode: CaseMode) -> Rule {
        Rule::Case { mode, scope }
    }

    #[test]
    fn case_lower_full() {
        assert_eq!(transform_name("Hello World.TXT", &[case(Scope::Full, CaseMode::Lower)]), "hello world.txt");
    }
    #[test]
    fn case_upper_stem() {
        assert_eq!(transform_name("hello.TXT", &[case(Scope::Stem, CaseMode::Upper)]), "HELLO.TXT");
    }
    #[test]
    fn case_title_keeps_rest_lower() {
        // str.title() 语义：词首大写、词中大写转小写；. 后是词边界（→ .Txt）
        assert_eq!(transform_name("hello WORLD.txt", &[case(Scope::Full, CaseMode::Title)]), "Hello World.Txt");
        // 数字非词边界（a1b2 → A1B2，b 不大写）
        assert_eq!(transform_name("a1b2.txt", &[case(Scope::Full, CaseMode::Title)]), "A1B2.Txt");
    }
    #[test]
    fn case_capitalize_only_first() {
        assert_eq!(transform_name("hello WORLD", &[case(Scope::Full, CaseMode::Capitalize)]), "Hello WORLD");
    }

    #[test]
    fn prefix_basic() {
        let r = Rule::Prefix { text: "pre_".into() };
        assert_eq!(transform_name("a.txt", &[r]), "pre_a.txt");
    }

    #[test]
    fn suffix_before_ext() {
        let r = Rule::Suffix { text: "_bkp".into() };
        assert_eq!(transform_name("a.txt", &[r.clone()]), "a_bkp.txt");
        assert_eq!(transform_name("noext", &[r]), "noext_bkp");
    }

    #[test]
    fn number_suffix_default() {
        let r = Rule::Number {
            pos: NumberPos::Suffix,
            start: 1,
            step: 1,
            digits: 2,
            sep: " ".into(),
        };
        assert_eq!(transform_name_indexed("a.txt", &[r.clone()], 0), "a 01.txt");
        assert_eq!(transform_name_indexed("a.txt", &[r], 9), "a 10.txt");
    }
    #[test]
    fn number_prefix_padding_and_step() {
        let r = Rule::Number {
            pos: NumberPos::Prefix,
            start: 10,
            step: 5,
            digits: 3,
            sep: "-".into(),
        };
        assert_eq!(transform_name_indexed("a.txt", &[r.clone()], 1), "015-a.txt");
        assert_eq!(transform_name_indexed("a.txt", &[r], 2), "020-a.txt");
    }

    #[test]
    fn ext_replace_without_dot() {
        let r = Rule::Ext { text: "md".into() };
        assert_eq!(transform_name("a.txt", &[r]), "a.md");
    }
    #[test]
    fn ext_replace_with_dot() {
        let r = Rule::Ext { text: ".md".into() };
        assert_eq!(transform_name("a.dat", &[r]), "a.md");
    }
    #[test]
    fn ext_empty_removes_extension() {
        let r = Rule::Ext { text: "".into() };
        assert_eq!(transform_name("a.txt", &[r]), "a");
    }

    #[test]
    fn strip_chars_full() {
        let r = Rule::Strip { chars: "-_".into(), scope: Scope::Full };
        assert_eq!(transform_name("a-b_c.txt", &[r]), "abc.txt");
    }
    #[test]
    fn strip_chars_scope_ext() {
        let r = Rule::Strip { chars: "t".into(), scope: Scope::Ext };
        assert_eq!(transform_name("a.txt", &[r]), "a.x");
    }
    #[test]
    fn strip_chars_scope_stem() {
        let r = Rule::Strip { chars: "a1".into(), scope: Scope::Stem };
        assert_eq!(transform_name("a1b2.txt", &[r]), "b2.txt");
    }

    #[test]
    fn trim_collapse_spaces() {
        let r = Rule::Trim { underscore: false };
        assert_eq!(transform_name("  a   b \t c  ", &[r]), "a b c");
    }
    #[test]
    fn trim_underscore_replaces_space() {
        let r = Rule::Trim { underscore: true };
        assert_eq!(transform_name("a   b c", &[r]), "a_b_c");
    }

    #[test]
    fn pipeline_combines_rules() {
        // prefix → suffix → number(padded) → trim，多规则流水线
        let rules = [
            Rule::Prefix { text: "p_".into() },
            Rule::Suffix { text: "_s".into() },
            Rule::Number { pos: NumberPos::Suffix, start: 1, step: 1, digits: 2, sep: "-".into() },
            Rule::Trim { underscore: false },
        ];
        assert_eq!(transform_name_indexed("a b.txt", &rules, 0), "p_a b_s-01.txt");
    }
}