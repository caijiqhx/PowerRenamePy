//! 规则定义（Rust 移植范围：查找替换 / 正则替换 / 按清单重命名）。

use std::collections::HashMap;

/// 规则作用范围（对齐 Python 版 scope 语义）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scope {
    /// 完整文件名（含扩展名）
    Full,
    /// 仅主名（不含扩展名）
    Stem,
    /// 仅扩展名
    Ext,
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
            Rule::List { mapping } => {
                format!("清单 [{} 条映射]", mapping.len())
            }
        }
    }

    pub fn scope(&self) -> Scope {
        match self {
            Rule::Replace { scope, .. } | Rule::Regex { scope, .. } => *scope,
            Rule::List { .. } => Scope::Full,
        }
    }
}