#!/usr/bin/env bash
# AlphaForge 一键启动（Git Bash / WSL / Linux / macOS）
#
# 用法：
#   ./start.sh              正常启动
#   AF_PY=/path/python ./start.sh   指定解释器
#   AF_DRY_RUN=1 ./start.sh 只做环境检查，不启动进程
#
# Ctrl+C 会把前后端一起停掉。

set -uo pipefail

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"

AF_PORT="${AF_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
RELOAD=(--reload
        --reload-dir packages/core --reload-dir packages/data
        --reload-dir packages/engine --reload-dir packages/api)

echo
echo " =========================================================="
echo "   AlphaForge  -  one click launcher"
echo " =========================================================="
echo

# ---------------------------------------------------------------
# 1. 找一个真的装了 fastapi + uvicorn 的解释器
#    这台机器上默认 python 往往没装，需要按候选列表挨个试。
# ---------------------------------------------------------------
pick_python() {
    local cand
    for cand in \
        "${AF_PY:-}" \
        "$HOME/miniconda3/python" \
        "$HOME/miniconda3/bin/python" \
        "/c/ProgramData/miniconda3/python" \
        "/c/ProgramData/anaconda3/python" \
        "$HOME/anaconda3/python" \
        python python3 py
    do
        [ -n "$cand" ] || continue
        if command -v "$cand" >/dev/null 2>&1 || [ -x "$cand" ]; then
            if "$cand" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
                printf '%s' "$cand"
                return 0
            fi
        fi
    done
    return 1
}

PYEXE="$(pick_python)" || {
    echo " [X] 没找到装了 fastapi + uvicorn 的 Python。"
    echo
    echo "     装一次："
    echo '       python -m pip install "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2"'
    echo
    echo "     或者指定解释器：  AF_PY=/path/to/python ./start.sh"
    exit 1
}
echo " [OK] python   : $PYEXE"

# ---------------------------------------------------------------
# 2. node 与前端依赖
# ---------------------------------------------------------------
command -v node >/dev/null 2>&1 || {
    echo " [X] 没有 node，先装 Node 20+。"
    exit 1
}
echo " [OK] node     : $(node -v)"

if [ ! -d "packages/web/node_modules" ]; then
    echo " [!]  packages/web/node_modules 不存在，执行 npm install ..."
    (cd packages/web && npm install) || exit 1
fi

[ -f ".env" ] || echo " [!]  缺 .env：没有 AF_LLM_* 时 AI 分析会退回规则版"
[ -f "data/alphaforge.db" ] || \
    echo " [!]  缺 data/alphaforge.db，先跑：python -m alphaforge_data sync --only all --workers 12"

if [ -n "${AF_DRY_RUN:-}" ]; then
    echo
    echo " [DRY RUN] 环境检查通过，未启动进程。"
    echo "   python : $PYEXE"
    echo "   端口   : $AF_PORT / $WEB_PORT"
    exit 0
fi

# ---------------------------------------------------------------
# 3. 启动
#    热重载必须逐目录指定：监视整个工作目录会把 data/ 也算进去，
#    而 autopilot 每 30 秒写一次 autopilot.json，会把自己重启死。
# ---------------------------------------------------------------
export PYTHONPATH="$ROOT/packages/core:$ROOT/packages/data:$ROOT/packages/engine"

port_busy() {
    # 只看 LISTENING，TIME_WAIT 里的同端口连接不算占用
    netstat -ano 2>/dev/null | grep ":$1 " | grep -qi "LISTENING"
}

API_PID=""
WEB_PID=""

cleanup() {
    echo
    echo " 正在停止 ..."
    [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null
    [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null
    wait 2>/dev/null
    echo " 已停止。"
}
trap cleanup EXIT INT TERM

if port_busy "$AF_PORT"; then
    echo " [!]  端口 $AF_PORT 已被占用，后端跳过启动"
else
    "$PYEXE" -m uvicorn alphaforge_api.main:app \
        --port "$AF_PORT" --app-dir packages/api "${RELOAD[@]}" &
    API_PID=$!
    echo " [OK] backend  : http://127.0.0.1:$AF_PORT/docs"
fi

if port_busy "$WEB_PORT"; then
    echo " [!]  端口 $WEB_PORT 已被占用，前端跳过启动"
else
    (cd packages/web && npm run dev) &
    WEB_PID=$!
    echo " [OK] frontend : http://localhost:$WEB_PORT"
fi

echo
echo " 按 Ctrl+C 同时停止前后端。"
if [ -n "$WEB_PID" ]; then
    sleep 5
    command -v start >/dev/null 2>&1 && start "" "http://localhost:$WEB_PORT" 2>/dev/null || true
    command -v xdg-open >/dev/null 2>&1 && xdg-open "http://localhost:$WEB_PORT" >/dev/null 2>&1 || true
fi

wait
