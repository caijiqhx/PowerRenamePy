//! 规则定义（Rust 移植范围：查找替换 / 正则替换 / 按清单重命名）。

use std::collections::HashMap;

/// 规则作用范围。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scope {
    /// 完整文件名（含扩展名）
    Full,
    /// 仅主名（不含扩展名）
    Stem,
    /// 仅扩展名
    Ext,
}

/// 大小写转换模式。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaseMode {
    Lower,
    Upper,
    Title,
    Capitalize,
}

/// 序列编号插入位置。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NumberPos {
    /// 编号加在名称最前面（前缀）
    Prefix,
    /// 编号加在主名之后、扩展名之前（后缀）
    Suffix,
}

/// 单条改名规则。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Rule {
    /// 查找替换：search 按字面量替换为 replace；case_sensitive 控制大小写。
    Replace {
        search: String,
        replace: String,
        case_sensitive: bool,
        scope: Scope,
    },
    /// 正则替换：pattern 匹配（fancy-regex，支持 lookaround），替换为 replace。
    Regex {
        pattern: String,
        replace: String,
        scope: Scope,
    },
    /// 大小写转换：mode 指定转换方式，作用于 scope 范围。
    Case {
        mode: CaseMode,
        scope: Scope,
    },
    /// 添加前缀。
    Prefix {
        text: String,
    },
    /// 添加后缀（主名之后、扩展名之前）。
    Suffix {
        text: String,
    },
    /// 序列编号：按当前条目序号生成编号（起始值 + 步长），补零到指定位数。
    Number {
        pos: NumberPos,
        start: u32,
        step: u32,
        digits: u32,
        sep: String,
    },
    /// 替换扩展名。
    Ext {
        text: String,
    },
    /// 移除指定字符（逐个移除）。
    Strip {
        chars: String,
        scope: Scope,
    },
    /// 压缩空白（多个空白并为一个空格，可替换为下划线）。
    Trim {
        underscore: bool,
    },
    /// 按清单重命名：始终用「原始文件名」匹配映射，命中则采用清单新名。
    List {
        mapping: HashMap<String, String>,
    },
}

impl Rule {
    /// 规则摘要（用于 UI 列表展示）
    pub fn summary(&self) -> String {
        match self {
            Rule::Replace {
                search,
                replace,
                case_sensitive,
                ..
            } => {
                let cs = if *case_sensitive { "敏感" } else { "忽略大小写" };
                format!("替换 [{search}] → [{replace}] ({cs})")
            }
            Rule::Regex { pattern, replace, .. } => {
                format!("正则 [{pattern}] → [{replace}]")
            }
            Rule::Case { mode, .. } => {
                let m = match mode {
                    CaseMode::Lower => "全部小写",
                    CaseMode::Upper => "全部大写",
                    CaseMode::Title => "首字母大写",
                    CaseMode::Capitalize => "仅首字符大写",
                };
                format!("大小写转换（{m}）")
            }
            Rule::Prefix { text } => format!("添加前缀 [{text}]"),
            Rule::Suffix { text } => format!("添加后缀 [{text}]"),
            Rule::Number { pos, start, step, digits, sep } => {
                let p = match pos {
                    NumberPos::Prefix => "前缀",
                    NumberPos::Suffix => "后缀",
                };
                format!("序列编号（{p}，从 {start} 步进 {step}，{digits} 位，分隔[{sep}]）")
            }
            Rule::Ext { text } => format!("替换扩展名 [{text}]"),
            Rule::Strip { scope, chars } => {
                let s = match scope {
                    Scope::Full => "完整名",
                    Scope::Stem => "主名",
                    Scope::Ext => "扩展名",
                };
                format!("移除字符 [{chars}]（{s}）")
            }
            Rule::Trim { underscore } => {
                if *underscore {
                    "压缩空白（换下划线）".to_string()
                } else {
                    "压缩空白".to_string()
                }
            }
            Rule::List { mapping } => format!("清单 [{} 条映射]", mapping.len()),
        }
    }

    pub fn scope(&self) -> Scope {
        match self {
            Rule::Replace { scope, .. }
            | Rule::Regex { scope, .. }
            | Rule::Case { scope, .. }
            | Rule::Strip { scope, .. } => *scope,
            _ => Scope::Full,
        }
    }
}