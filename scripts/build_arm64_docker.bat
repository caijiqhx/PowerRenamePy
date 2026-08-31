@echo off
rem =============================================================================
rem build_arm64_docker.bat — 在 Windows 上借助 Docker Desktop 的 qemu 模拟，
rem                          打包 PowerRenamePy 的 Linux/aarch64 可执行文件。
rem
rem 前置条件:
rem   1. 已安装 Docker Desktop 并启动（Windows 设置里无需额外配置，
rem      Docker Desktop 自带 buildx + binfmt/qemu 模拟）
rem   2. 网络可拉取官方镜像
rem
rem 产物: dist-arm64\PowerRenamePy  (aarch64 ELF，单文件)
rem =============================================================================
setlocal
rem 回到项目根目录（本脚本位于 scripts/ 下）
cd /d "%~dp0.."

where docker >nul 2>&1
if errorlevel 1 (
  echo [ERR] 未找到 docker，请先安装并启动 Docker Desktop:
  echo        https://www.docker.com/products/docker-desktop/
  exit /b 1
)

echo [1/3] 用 buildx 构建 linux/arm64 镜像（首次会拉取镜像，可能较慢）...
docker buildx build --platform linux/arm64 -t powerrenamepy-arm64 -f Dockerfile.arm64 . || goto :err

echo [2/3] 从镜像提取可执行文件...
docker rm -f powerrenamepy-arm64-extract >nul 2>&1
docker create --name powerrenamepy-arm64-extract powerrenamepy-arm64 || goto :err
if not exist "dist-arm64" mkdir "dist-arm64"
docker cp powerrenamepy-arm64-extract:/PowerRenamePy "dist-arm64\PowerRenamePy" || goto :err
docker rm -f powerrenamepy-arm64-extract >nul 2>&1

echo [3/3] 完成!
echo.
echo 产物: %cd%\dist-arm64\PowerRenamePy
echo 验证: 把文件传到 ARM64 Linux 上执行  file PowerRenamePy
echo        应输出 ... ELF 64-bit LSB executable, ARM aarch64 ...
echo 运行: 目标机需有图形环境 (X11/Wayland)
exit /b 0

:err
echo.
echo [ERR] 打包失败。常见原因:
echo   - Docker Desktop 未启动 (打开 Docker Desktop 等状态变 Running)
echo   - 网络无法拉取镜像 (检查代理/镜像源)
echo   - 老版本 Docker 需手动启用 buildx:  docker buildx create --use
exit /b 1
