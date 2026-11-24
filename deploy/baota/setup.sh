#!/usr/bin/env bash
# 服务器端初始化：建 venv、装依赖、检查产物。不启动进程。
#
#   cd /www/wwwroot/alphaforge && bash deploy/baota/setup.sh
#   指定解释器：AF_PY=/www/server/pyporject_evn/versions/3.12.0/bin/python3 bash deploy/baota/setup.sh
#
# 启动交给宝塔「Python 项目」或 Supervisor，见 deploy/baota/README.md。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
echo "项目目录：$ROOT"

PY_BIN="${AF_PY:-python3}"
if ! command -v "$PY_BIN" >/dev/null 2>&1; then
    echo "[X] 找不到 $PY_BIN。用 AF_PY=<python 路径> 指定，或在宝塔「Python 项目管理器」里先装一个 3.11+ 的版本。"
    exit 1
fi

"$PY_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    sys.exit(f"[X] 需要 Python 3.11+，当前是 {sys.version.split()[0]}")
print(f"[OK] Python {sys.version.split()[0]}：{sys.executable}")
PY

if [ ! -d venv ]; then
    echo "[..] 创建 venv"
    "$PY_BIN" -m venv venv
fi

echo "[..] 安装依赖"
./venv/bin/python -m pip install -q --upgrade pip
./venv/bin/python -m pip install -q -r requirements.txt
echo "[OK] 依赖装好"

if [ ! -f packages/web/dist/index.html ]; then
    echo "[X] 缺 packages/web/dist/index.html。"
    echo "    在本地执行 npm --prefix packages/web run build，把 packages/web/dist 整个目录传上来；"
    echo "    或在服务器装 Node 20 后执行 cd packages/web && npm ci && npm run build。"
    exit 1
fi
echo "[OK] 前端产物存在"

if [ ! -f data/alphaforge.db ]; then
    echo "[!] 缺 data/alphaforge.db。两种补法："
    echo "    ① 从本地传上来（先关服务，连 -wal/-shm 一起传，或先做一次 wal_checkpoint）"
    echo "    ② 在服务器首次全量同步：./venv/bin/python -m alphaforge_data sync --only all --workers 8"
    echo "       需要 PYTHONPATH，可直接用：PYTHONPATH=packages/core:packages/data:packages/engine ./venv/bin/python -m alphaforge_data sync --only all --workers 8"
else
    du -h data/alphaforge.db | awk '{print "[OK] 行情库 " $1}'
fi

if [ ! -f data/auth.json ]; then
    echo "[!] 没有 data/auth.json，首次登录用内置账号 admin / admin，登录后会强制改密码。"
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "[!] 已从 .env.example 生成 .env，记得填 AF_LLM_API_KEY 并改 AF_CORS_ORIGINS。"
fi

echo
echo "下一步："
echo "  1. 宝塔「Python 项目管理器」新建项目，启动命令见 deploy/baota/README.md 第六节"
echo "  2. Nginx 反向代理到 127.0.0.1:8000，超时 600 秒（deploy/baota/nginx.conf）"
echo "  3. 浏览器打开域名，用 admin / admin 登录并立刻改密码"
