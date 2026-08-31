#!/usr/bin/env bash
# =============================================================================
# build_arm64.sh — 在 ARM64 (aarch64) Linux 机器上一键打包 PowerRenamePy
#
# 用法:
#   ./scripts/build_arm64.sh          # 检测依赖，缺 tkinter 时给出安装提示
#   ./scripts/build_arm64.sh --install-deps  # 检测到缺依赖时自动调用 sudo 安装
#
# 产物:
#   dist/PowerRenamePy              # 单文件 ELF 可执行文件 (aarch64)
#
# 原理: PyInstaller 不支持交叉编译，必须在目标架构环境内打包。
#       本脚本在你的 ARM64 Linux 上创建独立 venv 并完成打包。
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

# ---------- 1. 架构检测 -------------------------------------------------------
ARCH="$(uname -m)"
case "$ARCH" in
  aarch64|arm64)
    echo "[OK] 当前架构: $ARCH (aarch64)"
    ;;
  *)
    echo "[WARN] 当前架构是 '$ARCH'，不是 aarch64。"
    echo "       PyInstaller 不支持交叉编译，请在目标 ARM64 机器上运行本脚本。"
    ;;
esac

# ---------- 2. Python 检测 ----------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERR] 未找到 python3，请先安装 Python 3.10+："
  echo "      Debian/Ubuntu: sudo apt install python3 python3-venv python3-tk"
  echo "      Fedora/RHEL  : sudo dnf install python3 python3-tkinter"
  echo "      Arch         : sudo pacman -S python python-tkinter"
  exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
  echo "[ERR] python3 缺少 tkinter 模块。"
  if [[ "${1:-}" == "--install-deps" ]]; then
    echo "      正在尝试自动安装..."
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y python3-tk
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y python3-tkinter
    elif command -v pacman >/dev/null 2>&1; then
      sudo pacman -S --noconfirm python-tkinter
    else
      echo "[ERR] 无法识别的包管理器，请手动安装 tkinter 后重试。"
      exit 1
    fi
  else
    echo "      请先安装（Debian/Ubuntu: sudo apt install python3-tk；"
    echo "      Fedora/RHEL: sudo dnf install python3-tkinter；Arch: sudo pacman -S python-tkinter）"
    echo "      或使用参数 --install-deps 自动安装。"
    exit 1
  fi
fi
echo "[OK] python3 $(python3 --version 2>&1 | awk '{print $2}') + tkinter 就绪"

# ---------- 3. 独立打包环境 ----------------------------------------------------
VENV=".build-venv-arm64"
if [[ ! -d "$VENV" ]]; then
  echo "[..] 创建虚拟环境 $VENV ..."
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip -q
echo "[..] 安装 PyInstaller ..."
pip install -q pyinstaller
echo "[OK] PyInstaller $(pyinstaller --version)"

# ---------- 4. 打包 -------------------------------------------------------------
echo "[..] 打包 (onefile, aarch64) ..."
# 注意: Linux 打包不需要 --windowed / --icon（那两个参数仅 Windows 生效）
pyinstaller --onefile --name PowerRenamePy --clean --noconfirm --paths src src/main.py

# ---------- 5. 输出 -------------------------------------------------------------
OUT="dist/PowerRenamePy"
echo ""
echo "====================== 打包完成 ======================"
echo "产物: $(pwd)/$OUT"
ls -lh "$OUT" | awk '{print "大小:", $5}'
echo "验证架构（在 Linux 上执行）:"
echo "  file $OUT     # 应输出 ... ELF 64-bit LSB executable, ARM aarch64 ..."
echo "运行:"
echo "  ./$OUT        # 需要图形环境 (X11/Wayland)；远程服务器请加 -X 转发或用 VNC"
echo "======================================================"
