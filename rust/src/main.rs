//! PowerRenamePy Rust 版 — egui GUI 主程序。
//!
//! 布局：
//!   ┌────────────────────────────────────────────┐
//!   │ 路径输入 [加载] 递归□ 深度[ ] 含文件□ 含文件夹□ │
//!   │ 名称包含[ ] 排除[ ] 正则□                   │
//!   ├──────────────┬─────────────────────────────┤
//!   │ 规则列表      │ 预览树（原名 → 新名 + 状态） │
//!   │ [添加][删除]  │                             │
//!   │ 规则表单      │                             │
//!   ├──────────────┴─────────────────────────────┤
//!   │ 状态栏   [应用] [撤销]                      │
//!   └────────────────────────────────────────────┘

// 发布版不挂控制台窗口（避免运行时出现黑色终端）；debug 构建保留便于看输出
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::{Path, PathBuf};

use eframe::egui;

use power_rename::apply::{apply_renames, UndoManager};
use power_rename::fs_tree::{load_tree, flatten_tree, LoadOptions, TreeNode};
use power_rename::preview::{compute_preview, PreviewStatus};

/// 规则表单数据（GUI 编辑用，提交时转成引擎 Rule）
#[derive(Debug, Clone, PartialEq)]
enum RuleForm {
    Replace {
        search: String,
        replace: String,
        case_sensitive: bool,
        scope: usize, // 0=完整名 1=主名 2=扩展名
    },
    Regex {
        pattern: String,
        replace: String,
        scope: usize,
    },
    List {
        mapping: std::collections::HashMap<String, String>,
    },
}

impl RuleForm {
    fn summary(&self) -> String {
        match self {
            RuleForm::Replace { search, replace, case_sensitive, .. } => {
                let cs = if *case_sensitive { "敏感" } else { "忽略大小写" };
                format!("替换 [{search}] → [{replace}] ({cs})")
            }
            RuleForm::Regex { pattern, replace, .. } => {
                format!("正则 [{pattern}] → [{replace}]")
            }
            RuleForm::List { mapping } => format!("清单 [{} 条映射]", mapping.len()),
        }
    }

    fn to_rule(&self) -> power_rename::rules::Rule {
        let scope = match self.scope() {
            1 => power_rename::rules::Scope::Stem,
            2 => power_rename::rules::Scope::Ext,
            _ => power_rename::rules::Scope::Full,
        };
        match self {
            RuleForm::Replace { search, replace, case_sensitive, .. } => {
                power_rename::rules::Rule::Replace {
                    search: search.clone(),
                    replace: replace.clone(),
                    case_sensitive: *case_sensitive,
                    scope,
                }
            }
            RuleForm::Regex { pattern, replace, .. } => power_rename::rules::Rule::Regex {
                pattern: pattern.clone(),
                replace: replace.clone(),
                scope,
            },
            RuleForm::List { mapping } => power_rename::rules::Rule::List {
                mapping: mapping.clone(),
            },
        }
    }

    fn scope(&self) -> usize {
        match self {
            RuleForm::Replace { scope, .. } | RuleForm::Regex { scope, .. } => *scope,
            RuleForm::List { .. } => 0,
        }
    }

    fn set_scope(&mut self, s: usize) {
        match self {
            RuleForm::Replace { scope, .. } | RuleForm::Regex { scope, .. } => *scope = s,
            RuleForm::List { .. } => {}
        }
    }
}

/// 预览信息（GUI 渲染用）
#[derive(Debug, Clone)]
struct PreviewRow {
    new_name: String,
    status: PreviewStatus,
    note: String,
}

struct RenameApp {
    dir_input: String,
    recursive: bool,
    include_files: bool,
    include_dirs: bool,
    inc_text: String,
    exc_text: String,
    use_regex: bool,

    rules: Vec<RuleForm>,
    selected_rule: Option<usize>,

    tree: Option<TreeNode>,
    /// path → 预览信息（可改名节点的预览结果）
    preview_by_path: std::collections::HashMap<std::path::PathBuf, PreviewRow>,
    status_msg: String,
    undo: UndoManager,
}

impl RenameApp {
    fn new() -> Self {
        Self {
            dir_input: String::new(),
            recursive: true,
            include_files: true,
            include_dirs: false,
            inc_text: String::new(),
            exc_text: String::new(),
            use_regex: false,
            rules: Vec::new(),
            selected_rule: None,
            tree: None,
            preview_by_path: std::collections::HashMap::new(),
            status_msg: String::new(),
            undo: UndoManager::new(),
        }
    }

    fn load_options(&self) -> LoadOptions {
        LoadOptions {
            recursive: self.recursive,
            include_files: self.include_files,
            include_dirs: self.include_dirs,
            inc_text: self.inc_text.trim().to_string(),
            exc_text: self.exc_text.trim().to_string(),
            use_regex: self.use_regex,
        }
    }

    fn reload(&mut self) {
        self.tree = None;
        let path = PathBuf::from(self.dir_input.trim());
        if !path.is_dir() {
            self.status_msg = format!("目录不存在：{}", path.display());
            return;
        }
        let opts = self.load_options();
        // 深度限制：LoadOptions 无字段，非递归时由 recursive=false 控制；深度留待扩展
        let tree = load_tree(&path, &opts);
        // 预览行构建：展平树 + 冲突检测（可改名节点参与改名）
        let mut nodes = Vec::new();
        flatten_tree(&tree, &mut nodes);
        // 参与改名的节点 → entries（带原路径）
        let mut entries: Vec<power_rename::fs_tree::FileEntry> = Vec::new();
        for n in &nodes {
            if n.renameable {
                entries.push(power_rename::fs_tree::FileEntry {
                    path: n.path.clone(),
                    name: n.name.clone(),
                    is_dir: n.is_dir,
                });
            }
        }
        let rules: Vec<power_rename::rules::Rule> = self.rules.iter().map(|r| r.to_rule()).collect();
        let items = compute_preview(&entries, &rules);
        // 用「完整路径」做 key 查预览（文件名跨目录可能重复，路径唯一）
        let by_path: std::collections::HashMap<&Path, &power_rename::preview::PreviewItem> =
            items.iter().map(|i| (i.entry.path.as_path(), i)).collect();

        // 构建 path→预览映射（仅可改名节点）
        self.preview_by_path.clear();
        let mut stack: Vec<&TreeNode> = Vec::new();
        stack.push(&tree);
        while let Some(node) = stack.pop() {
            if let Some(p) = by_path.get(node.path.as_path()).copied() {
                self.preview_by_path.insert(
                    node.path.clone(),
                    PreviewRow {
                        new_name: p.new_name.clone(),
                        status: p.status,
                        note: p.note.clone(),
                    },
                );
            }
            for c in node.children.iter().rev() {
                stack.push(c);
            }
        }
        self.tree = Some(tree);
        let renamed = self.preview_by_path.values().filter(|r| r.status == PreviewStatus::Ok).count();
        let total = self.preview_by_path.len();
        self.status_msg = format!("共 {total} 项，可改名 {renamed} 项");
    }

    fn apply(&mut self) {
        if self.tree.is_none() {
            self.status_msg = "请先加载目录".to_string();
            return;
        }
        let rules: Vec<power_rename::rules::Rule> = self.rules.iter().map(|r| r.to_rule()).collect();
        let mut entries = Vec::new();
        if let Some(tree) = &self.tree {
            let mut nodes = Vec::new();
            flatten_tree(tree, &mut nodes);
            for n in &nodes {
                if n.renameable {
                    entries.push(power_rename::fs_tree::FileEntry {
                        path: n.path.clone(),
                        name: n.name.clone(),
                        is_dir: n.is_dir,
                    });
                }
            }
        }
        let items = compute_preview(&entries, &rules);
        let mut todo: Vec<(PathBuf, PathBuf)> = Vec::new();
        for it in &items {
            if it.status == PreviewStatus::Ok && it.new_name != it.old_name {
                todo.push((it.entry.path.clone(), it.entry.path.parent().unwrap_or(Path::new("")).join(&it.new_name)));
            }
        }
        if todo.is_empty() {
            self.status_msg = "没有可执行的改名".to_string();
            return;
        }
        let res = apply_renames(&todo);
        if res.rolled_back {
            self.status_msg = format!("改名失败，已回滚：{}", res.errors.join("；"));
        } else {
            self.undo.push(res.logs.clone());
            self.status_msg = format!("成功改名 {} 项，可撤销", res.logs.len());
        }
        self.reload();
    }

    fn undo(&mut self) {
        let (done, errors) = self.undo.undo();
        if done > 0 {
            self.status_msg = format!("已撤销 {done} 项");
        } else if !errors.is_empty() {
            self.status_msg = format!("撤销失败：{}", errors.join("；"));
        } else {
            self.status_msg = "没有可撤销的操作".to_string();
        }
        self.reload();
    }

    fn export_list(&mut self) {
        // 收集当前可改名条目（树中 renameable 节点）
        let mut entries = Vec::new();
        if let Some(tree) = &self.tree {
            let mut nodes = Vec::new();
            flatten_tree(tree, &mut nodes);
            for n in &nodes {
                if n.renameable {
                    entries.push(power_rename::fs_tree::FileEntry {
                        path: n.path.clone(),
                        name: n.name.clone(),
                        is_dir: n.is_dir,
                    });
                }
            }
        }
        if entries.is_empty() {
            self.status_msg = "没有可导出的条目（先加载目录）".to_string();
            return;
        }
        let rules: Vec<power_rename::rules::Rule> = self.rules.iter().map(|r| r.to_rule()).collect();
        let text = power_rename::list_io::build_export_text(&entries, &rules);
        let Some(path) = rfd::FileDialog::new()
            .add_filter("CSV", &["csv"])
            .set_file_name("rename_list.csv")
            .save_file()
        else {
            return;
        };
        // UTF-8 BOM，Excel 中文不乱码
        let mut data = vec![0xEF, 0xBB, 0xBF];
        data.extend_from_slice(text.as_bytes());
        match std::fs::write(&path, &data) {
            Ok(()) => self.status_msg = format!("已导出 {} 条到 {}", entries.len(), path.display()),
            Err(e) => self.status_msg = format!("导出失败：{e}"),
        }
    }

    fn import_list(&mut self) {
        let Some(path) = rfd::FileDialog::new()
            .add_filter("文本/CSV", &["csv", "txt"])
            .pick_file()
        else {
            return;
        };
        let raw = match std::fs::read(&path) {
            Ok(b) => b,
            Err(e) => {
                self.status_msg = format!("读取失败：{e}");
                return;
            }
        };
        // 编码探测：UTF-8 BOM → UTF-8；否则尝试 UTF-8，失败回退 GBK（Windows 常见）
        let text = decode_text(&raw);
        let mapping = power_rename::list_io::parse_rename_list(&text);
        if mapping.is_empty() {
            self.status_msg = "清单为空或格式无法识别".to_string();
            return;
        }
        // 追加一条清单规则（已有则替换最新一条）
        self.rules.push(RuleForm::List {
            mapping: mapping.clone(),
        });
        self.selected_rule = Some(self.rules.len() - 1);
        self.status_msg = format!("已导入 {} 条映射", mapping.len());
        self.reload();
    }
}

impl eframe::App for RenameApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        egui::TopBottomPanel::top("top").frame(panel_frame()).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label("目录：");
                ui.add(
                    egui::TextEdit::singleline(&mut self.dir_input)
                        .desired_width(320.0)
                        .hint_text("输入文件夹路径"),
                );
                if ui.button("加载").clicked() {
                    self.reload();
                }
                if ui.button("浏览…").clicked() {
                    if let Some(dir) = rfd::FileDialog::new().pick_folder() {
                        self.dir_input = dir.to_string_lossy().into_owned();
                        self.reload();
                    }
                }
                ui.separator();
                if ui.button("导出清单").clicked() {
                    self.export_list();
                }
                if ui.button("导入清单").clicked() {
                    self.import_list();
                }
            });
            ui.horizontal(|ui| {
                let mut opts_changed = false;
                opts_changed |= ui.checkbox(&mut self.recursive, "递归子目录").changed();
                opts_changed |= ui.checkbox(&mut self.include_files, "包含文件").changed();
                opts_changed |= ui.checkbox(&mut self.include_dirs, "包含文件夹").changed();
                ui.separator();
                ui.label("名称包含：");
                opts_changed |= ui.text_edit_singleline(&mut self.inc_text).changed();
                ui.label("排除：");
                opts_changed |= ui.text_edit_singleline(&mut self.exc_text).changed();
                opts_changed |= ui.checkbox(&mut self.use_regex, "正则").changed();
                if opts_changed && self.tree.is_some() {
                    self.reload();
                }
            });
        });

        egui::TopBottomPanel::bottom("bottom").frame(panel_frame()).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label(&self.status_msg);
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.button("撤销").clicked() {
                        self.undo();
                    }
                    if ui.button("应用").clicked() {
                        self.apply();
                    }
                });
            });
        });

        egui::SidePanel::left("rules").resizable(true).default_width(320.0).frame(panel_frame()).show(ctx, |ui| {
            ui.heading("规则");
            ui.horizontal(|ui| {
                let mut add_replace = false;
                let mut add_regex = false;
                if ui.button("+ 替换").clicked() {
                    add_replace = true;
                }
                if ui.button("+ 正则").clicked() {
                    add_regex = true;
                }
                if ui.button("删除").clicked() {
                    if let Some(idx) = self.selected_rule {
                        if idx < self.rules.len() {
                            self.rules.remove(idx);
                            self.selected_rule = None;
                        }
                    }
                }
                if add_replace {
                    self.rules.push(RuleForm::Replace {
                        search: String::new(),
                        replace: String::new(),
                        case_sensitive: false,
                        scope: 0,
                    });
                    self.selected_rule = Some(self.rules.len() - 1);
                }
                if add_regex {
                    self.rules.push(RuleForm::Regex {
                        pattern: String::new(),
                        replace: String::new(),
                        scope: 0,
                    });
                    self.selected_rule = Some(self.rules.len() - 1);
                }
            });

            // 规则列表
            let mut to_select: Option<usize> = None;
            for (i, r) in self.rules.iter().enumerate() {
                let selected = self.selected_rule == Some(i);
                if ui.selectable_label(selected, r.summary()).clicked() {
                    to_select = Some(i);
                }
            }
            if let Some(i) = to_select {
                self.selected_rule = Some(i);
            }

            ui.separator();

            // 规则表单
            if let Some(idx) = self.selected_rule {
                if idx < self.rules.len() {
                    ui.label(format!("规则 #{}", idx + 1));
                    let mut changed = false;
                    match &mut self.rules[idx] {
                        RuleForm::Replace { search, replace, case_sensitive, .. } => {
                            ui.horizontal(|ui| {
                                ui.label("查找：");
                                changed |= ui.text_edit_singleline(search).changed();
                            });
                            ui.horizontal(|ui| {
                                ui.label("替换为：");
                                changed |= ui.text_edit_singleline(replace).changed();
                            });
                            changed |= ui.checkbox(case_sensitive, "大小写敏感").changed();
                        }
                        RuleForm::Regex { pattern, replace, .. } => {
                            ui.horizontal(|ui| {
                                ui.label("正则：");
                                changed |= ui.text_edit_singleline(pattern).changed();
                            });
                            ui.horizontal(|ui| {
                                ui.label("替换为：");
                                changed |= ui.text_edit_singleline(replace).changed();
                            });
                        }
                        RuleForm::List { mapping } => {
                            ui.label(format!("按清单重命名：{} 条映射（导入自 CSV/文本）", mapping.len()));
                            ui.label("清单规则按「原始文件名」匹配，命中则采用清单新名。");
                        }
                    }
                    // 作用范围（List 规则不适用）
                    if !matches!(self.rules[idx], RuleForm::List { .. }) {
                        let scopes = ["完整文件名", "主名（不含扩展名）", "扩展名"];
                        let mut scope = self.rules[idx].scope();
                        ui.horizontal(|ui| {
                            ui.label("作用范围：");
                            for (si, label) in scopes.iter().enumerate() {
                                if ui.selectable_label(scope == si, *label).clicked() {
                                    scope = si;
                                    changed = true;
                                }
                            }
                        });
                        self.rules[idx].set_scope(scope);
                    }

                    // 规则变化 → 实时刷新预览
                    if changed {
                        self.reload();
                    }
                }
            } else if self.rules.is_empty() {
                ui.label("（还没有规则，点上方添加）");
            } else {
                ui.label("（选择一条规则编辑）");
            }
        });

        egui::CentralPanel::default().frame(panel_frame()).show(ctx, |ui| {
            ui.heading("预览");
            egui::ScrollArea::both().auto_shrink([false, false]).show(ui, |ui| {
                if let Some(tree) = &self.tree {
                    let mut expanded = std::collections::HashSet::new();
                    render_tree_node(ui, tree, &self.preview_by_path, &mut expanded, true);
                } else {
                    ui.label("（未加载目录）");
                }
            });
        });
    }
}

/// 递归渲染预览树：目录用 CollapsingHeader（可折叠），文件/可改名节点显示状态。
fn render_tree_node(
    ui: &mut egui::Ui,
    node: &power_rename::fs_tree::TreeNode,
    by_path: &std::collections::HashMap<std::path::PathBuf, PreviewRow>,
    _expanded: &mut std::collections::HashSet<std::path::PathBuf>,
    default_open: bool,
) {
    if node.is_dir {
        let children = &node.children;
        egui::CollapsingHeader::new(format!("📁 {}", node.name))
            .default_open(default_open)
            .show(ui, |ui| {
                for child in children {
                    render_tree_node(ui, child, by_path, _expanded, false);
                }
            });
    } else {
        let row = by_path.get(&node.path);
        let (new_name, status, note) = match row {
            Some(r) => (r.new_name.clone(), r.status, r.note.clone()),
            None => (node.name.clone(), PreviewStatus::Unchanged, String::new()),
        };
        let color = match status {
            PreviewStatus::Ok => egui::Color32::from_rgb(0x2e, 0x8b, 0x57),
            PreviewStatus::Conflict => egui::Color32::from_rgb(0xc0, 0x39, 0x2b),
            PreviewStatus::Error => egui::Color32::from_rgb(0x8b, 0x00, 0x00),
            PreviewStatus::Unchanged => egui::Color32::GRAY,
        };
        let arrow = if status == PreviewStatus::Ok { " → " } else { "   " };
        let mut text = String::from("📄 ");
        text.push_str(&node.name);
        text.push_str(arrow);
        text.push_str(&new_name);
        if !note.is_empty() {
            text.push_str("  (");
            text.push_str(&note);
            text.push(')');
        }
        ui.colored_label(color, text);
    }
}

fn main() -> eframe::Result {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1000.0, 700.0]).with_title("PowerRename Py — 批量重命名工具 (Rust)"),
        ..Default::default()
    };
    eframe::run_native(
        "PowerRenamePy Rust",
        options,
        Box::new(|cc| {
            install_chinese_font(&cc.egui_ctx);
            install_light_theme(&cc.egui_ctx);
            Ok(Box::new(RenameApp::new()))
        }),
    )
}

/// 安装定制的浅色主题：浅灰背景、白色面板、控件带边框、按钮有悬停/按下反馈。
fn install_light_theme(ctx: &egui::Context) {
    let mut visuals = egui::Visuals::light();

    // 背景：浅灰而非纯白，避免"一片白"
    visuals.panel_fill = egui::Color32::from_rgb(0xF2, 0xF3, 0xF5);
    visuals.window_fill = egui::Color32::WHITE;
    visuals.extreme_bg_color = egui::Color32::from_rgb(0xE8, 0xEA, 0xED);

    // 控件边框 + 圆角，让按钮/输入框有轮廓
    let border = egui::Color32::from_rgb(0xC8, 0xCB, 0xCF);
    let accent = egui::Color32::from_rgb(0x2F, 0x6F, 0xD5); // 蓝色强调

    for w in [
        &mut visuals.widgets.inactive,
        &mut visuals.widgets.hovered,
        &mut visuals.widgets.active,
    ] {
        w.bg_stroke = egui::Stroke::new(1.0, border);
        w.corner_radius = egui::CornerRadius::same(4);
    }
    // 按钮/控件：白底 + 边框；悬停轻微变蓝、按下蓝色边框
    visuals.widgets.inactive.bg_fill = egui::Color32::WHITE;
    visuals.widgets.hovered.bg_stroke = egui::Stroke::new(1.0, accent);
    visuals.widgets.hovered.weak_bg_fill = egui::Color32::from_rgb(0xE8, 0xF0, 0xFB);
    visuals.widgets.active.bg_stroke = egui::Stroke::new(1.5, accent);
    visuals.widgets.active.bg_fill = egui::Color32::from_rgb(0xD6, 0xE4, 0xF7);

    // 选中项背景
    visuals.selection.bg_fill = egui::Color32::from_rgb(0xCF, 0xE0, 0xF8);

    ctx.set_visuals(visuals);
}

/// 面板统一样式：白底 + 浅灰边框 + 内边距，让各区域有清晰分隔。
fn panel_frame() -> egui::Frame {
    egui::Frame::new()
        .fill(egui::Color32::WHITE)
        .stroke(egui::Stroke::new(1.0, egui::Color32::from_rgb(0xD0, 0xD3, 0xD7)))
        .inner_margin(egui::Margin::same(8))
        .corner_radius(egui::CornerRadius::same(0))
}

/// 安装中文字体（egui 默认字体不含中文，需加载系统字体，否则中文显示为乱码/方框）。
fn install_chinese_font(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();

    // 按优先级尝试常见的 Windows 中文字体文件
    let candidates = [
        "C:/Windows/Fonts/msyh.ttc",    // 微软雅黑
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simhei.ttf",  // 黑体
        "C:/Windows/Fonts/simsun.ttc",  // 宋体
        "C:/Windows/Fonts/simkai.ttf",  // 楷体
    ];
    let mut installed = false;
    for path in candidates {
        if let Ok(bytes) = std::fs::read(path) {
            let mut font_data = egui::FontData::from_owned(bytes);
            // 中文字形基线偏高（CJK 字体常见）：整体下移 0.2 字号，与英文基线对齐
            font_data.tweak = egui::FontTweak {
                y_offset_factor: 0.2,
                ..Default::default()
            };
            fonts.font_data.insert(
                "chinese".to_owned(),
                font_data.into(),
            );
            installed = true;
            break;
        }
    }
    if !installed {
        // 非 Windows 或找不到中文字体：静默跳过（英文界面仍可用）
        return;
    }

    // 把中文字体放进比例字体族的最高优先级（fallback 顺序：中文 → Ubuntu-Light 等）
    fonts
        .families
        .entry(egui::FontFamily::Proportional)
        .or_default()
        .insert(0, "chinese".to_owned());
    fonts
        .families
        .entry(egui::FontFamily::Monospace)
        .or_default()
        .push("chinese".to_owned());

    ctx.set_fonts(fonts);
}

/// 文本解码：UTF-8 BOM / UTF-8 优先，失败回退 GBK（Windows 常见中文编码）。
fn decode_text(raw: &[u8]) -> String {
    if raw.starts_with(&[0xEF, 0xBB, 0xBF]) {
        return String::from_utf8_lossy(&raw[3..]).into_owned();
    }
    match std::str::from_utf8(raw) {
        Ok(s) => s.to_string(),
        Err(_) => {
            // GBK 回退（encoding_rs 标准库）
            let (text, _, _) = encoding_rs::GBK.decode(raw);
            text.into_owned()
        }
    }
}
