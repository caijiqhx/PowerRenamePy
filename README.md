# PowerRename Py

一个类似 Windows PowerToys **PowerRename** 的批量重命名工具，使用 **Python + tkinter** 实现，纯标准库、零第三方依赖、跨平台（Windows / macOS / Linux）。

界面布局：顶部工具栏（文件夹路径 + 筛选选项）、左侧规则编辑面板、右侧实时预览表格、底部应用/撤销操作栏。

## 功能特性

- **规则流水线**：支持按顺序叠加多条规则，规则可增删、上移/下移、清空
- **规则方案**：将当前规则保存为 `.json` 方案文件，随时加载复用（清单映射一并保存）
- **规则类型**
  | 类型 | 说明 |
  |---|---|
  | 查找并替换 | 支持忽略大小写，可指定作用范围（整个文件名 / 仅主名 / 仅扩展名） |
  | 正则替换 | Python `re.sub` 语法，支持 `\1` 捕获组 |
  | 大小写转换 | 全部小写 / 全部大写 / Title Case / 仅首字符大写 |
  | 添加前缀 | 在文件名最前面插入文本 |
  | 添加后缀 | 在主名之后、扩展名之前插入文本 |
  | 序列编号 | 起始值、步长、位数补零、前缀或后缀插入、自定义分隔符 |
  | 替换扩展名 | 一键批量改扩展名 |
  | 移除字符 | 逐个移除指定字符（如 `-()`） |
  | 压缩空白 | 合并连续空白；可选转下划线 |
  | 按清单重命名 | 导入 `原名→新名` 清单（txt/csv），按映射逐项改名；未匹配项保持原名 |
- **导出文件清单**：一键导出当前列表为 `原名 → 新名` 清单（无规则时为模板，回填后可直接导入），与「导入清单」形成闭环
- **树形展示**：预览以目录树呈现，子目录可折叠展开，体现完整目录结构；选中"包含文件"（及勾选"包含文件夹"后的目录）参与改名，结构节点始终保留
- **实时预览**：输入规则即时刷新，状态着色区分 就绪 / 冲突 / 无变化 / 错误，附说明
- **冲突与安全检查**：目标重名、目标已在磁盘存在、非法字符（`<>:"/\|?*`）、空结果
- **两阶段重命名**：先全部改临时名再改目标名，支持 `a↔b` 互换和链式改名，出错自动回滚
- **撤销**：内存栈记录每次应用，一键恢复
- **目录筛选**：包含/排除关键词（支持正则）、包含文件/包含文件夹、是否递归子目录
- 右键预览行可"打开所在文件夹"定位文件（目录节点可"打开文件夹"）

## 目录结构

```
PowerRenamePy/
├── src/                     # 源码
│   ├── main.py              # 程序入口（python src/main.py）
│   ├── cli.py               # 命令行入口（python src/cli.py，复用引擎，无需 GUI）
│   ├── ui.py                # tkinter 图形界面
│   └── rename_engine.py     # 核心引擎：规则、转换、预览、冲突检测、执行、撤销
├── tests/
│   ├── test_engine.py       # 引擎自动化测试（47 个用例）
│   └── test_cli.py          # CLI 自动化测试（8 个用例）
├── scripts/                 # 构建与工具脚本
│   ├── build_arm64.sh       # ARM64 Linux 目标机一键打包脚本
│   ├── build_arm64_docker.bat  # Windows 上一键执行 Docker 模拟打包
│   └── make_icon.py         # 生成 assets/icon.ico（纯标准库，无需 Pillow）
├── assets/
│   └── icon.ico             # 应用图标
├── build/                   # PyInstaller 中间产物（自动生成，勿手动编辑）
├── dist/                    # 打包产物：dist/PowerRenamePy.exe（Windows）
├── dist-arm64/              # 打包产物：ARM64 Linux（由脚本生成）
├── Dockerfile.arm64         # Docker buildx 模拟 ARM64 打包
├── PowerRenamePy.spec       # PyInstaller 打包配置（Windows）
├── .gitignore
└── README.md
```

## 运行方式

### 方式一：直接运行 exe（推荐）

`dist/PowerRenamePy.exe` 为 PyInstaller 单文件打包，**双击即可运行**，目标机器无需安装 Python。

### 方式二：源码运行

```bash
python src/main.py
```

> **要求**：Python 3.10+，且需自带 tkinter。
> Windows 官方 python.org 安装包、Anaconda / Miniconda 均默认包含 tkinter；
> 部分精简版（如某些嵌入式发行版）不带，需改用完整版 Python。

### 方式三：命令行（CLI，无需 GUI）

精简版：只支持**按清单重命名**。复用核心引擎，默认**预览**（dry-run），加 `--apply` 才真正执行：

```bash
python src/cli.py /path/to/dir --list rename_list.txt            # 预览（不实际改名）
python src/cli.py /path/to/dir --list rename_list.txt --apply    # 真正执行
```

> 清单文件每行一条「原名 → 新名」，支持分隔符 `→` / `->` / `=>` / Tab / 逗号 / 分号 / 竖线 / 连续空格，
> 自动识别 UTF-8 / GBK 编码；未匹配清单的文件保持原名。

## 打包为 exe（在 Windows 上）

```bash
# 1. 用带 tkinter 的 Python 创建打包环境并安装 PyInstaller
python -m venv pyinstaller-env
pyinstaller-env\Scripts\pip install pyinstaller

# 2. 生成图标（可选，已内置 assets/icon.ico）
pyinstaller-env\Scripts\python scripts/make_icon.py

# 3. 打包：单文件、无控制台窗口、带图标（--paths src 指向源码目录）
pyinstaller-env\Scripts\pyinstaller --onefile --windowed --name PowerRenamePy ^
    --icon assets/icon.ico --paths src --clean --noconfirm src/main.py

# 产物在 dist/PowerRenamePy.exe
```

## 打包为 ARM64 Linux 可执行文件

> **为什么不能直接交叉编译**：PyInstaller 不支持交叉编译——Windows/x86 上产出的
> 始终是当前平台的二进制。ARM64 Linux 版本必须在 ARM64 环境（真机 / 虚拟机 /
> Docker qemu 模拟）里打包。本项目代码为纯标准库 + tkinter，已通过平台分支适配
> （右键"打开所在文件夹"在 Linux 上使用 `xdg-open`），可放心在 ARM64 Linux 运行。

### 方式 A：在目标 ARM64 Linux 机器上打包（推荐，产物最干净）

把整个项目目录拷到目标机（`scp -r PowerRenamePy user@host:~`），然后：

```bash
cd PowerRenamePy
chmod +x scripts/build_arm64.sh
./scripts/build_arm64.sh          # 缺 tkinter 时脚本会给出安装命令
# 或自动安装依赖:
./scripts/build_arm64.sh --install-deps
```

脚本会自动：检测架构 → 创建独立 venv → 安装 PyInstaller → 打包。
产物：`dist/PowerRenamePy`（单文件 aarch64 ELF）。

```bash
file dist/PowerRenamePy   # 应输出 ... ELF 64-bit LSB executable, ARM aarch64 ...
./dist/PowerRenamePy      # 运行（需图形环境）
```

### 方式 B：在 Windows 上借助 Docker 模拟打包（无需 ARM 机器）

前置条件：安装并启动 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
（自带 buildx 与 qemu，可模拟运行 ARM64 容器）。

```bat
scripts\build_arm64_docker.bat
```

产物：`dist-arm64\PowerRenamePy`。过程为：`docker buildx build --platform linux/arm64`
构建镜像 → 容器内安装 tcl/tk 并跑 PyInstaller → 从镜像提取可执行文件。

### 运行注意事项（ARM64 Linux）

- **需要图形环境**：tkinter 是 GUI，需 X11/Wayland 桌面。无显示器的服务器可通过
  `ssh -X` 转发，或使用 VNC；纯 headless 环境无法弹窗。
- 非法字符校验按 Windows 规则（`<>:"/\|?*`），在 Linux 上偏严格但不会出错——
  仅为避免将来把文件拷回 Windows 时遇到非法名。
- 如果目标机不方便打包，临时方案仍是**源码运行**：`sudo apt install python3-tk && python3 src/main.py`（任何架构通用）。

## 使用步骤

1. 输入或浏览选择目标文件夹，点击「加载」；可勾选子文件夹/包含文件/包含文件夹，并用"包含/排除"过滤列表
2. 在左侧选择规则类型 → 点击「添加」→ 在下方"规则参数"区填写 → 点「保存修改」
3. **按清单重命名**：点击工具栏「导入清单…」，选择 txt/csv 文件，每行一条 `原名 → 新名`；
   支持分隔符 `→` / `->` / `=>` / Tab / 逗号 / 分号 / 竖线 / 连续空格，自动识别 UTF-8 / GBK 编码。
   导入后自动添加一条"按清单重命名"规则；未匹配清单的文件保持原名
4. **导出文件清单**：点击工具栏「导出清单…」→ 无规则时导出 `原名 → ` 模板，有规则时导出含新名的清单。
   可回填新名后再「导入清单…」，形成「导出→填名→导入→应用」闭环
5. **保存/加载规则方案**：点「保存方案…」将当前规则存为 `.json`，下次「加载方案…」一键恢复
6. 右侧预览以**树形**展示目录结构（可折叠展开），状态着色：绿色=就绪，红色=冲突/错误，灰色=无变化、蓝色加粗=目录节点（不参与改名，除非勾选"包含文件夹"）
7. 确认无误后点击「应用重命名」；如需恢复，点击「撤销上次」

## 实现原理

- **规则是线性流水线**：每个文件名按规则顺序依次转换，第 N 条规则的输出是第 N+1 条的输入（与 PowerRename 的多模式同时应用不同，更直观可预测）。
- **两阶段执行**：
  ```
  阶段一：old ──改名──> 临时名（同目录 + UUID 后缀）
  阶段二：临时名 ──改名──> new
  ```
  因此 `a→b, b→a` 互换、链式改名都能成功；任一阶段抛错则自动回滚所有已完成的改动。
- **冲突检测在预览阶段完成**：目标重名、目标被磁盘上其他文件占用（不在本次列表内）、非法字符、空结果都会被标红并跳过。

## 测试

```bash
python -m unittest tests.test_engine   # 47 个用例：规则转换 / 编号索引 / 作用范围 / 冲突 / 互换重命名 / 撤销 / 清单映射 / 方案序列化 / 导出清单 / 树形加载 / 跨目录同名
python -m unittest tests.test_cli      # 8 个用例：dry-run / 执行 / 部分命中 / GBK 清单 / 缺参 / 空清单 / 目录不存在
```

## 说明与限制

- 撤销栈仅保存在内存中，程序退出后无法恢复（GUI）
- 引擎与 GUI 解耦：`src/rename_engine.py` 不依赖 tkinter，CLI（`src/cli.py`）在无图形环境也能用
- 不处理超长路径 / 跨卷移动，仅支持同一目录内改名
