# AlphaForge 常用命令
# 依赖：Python 3.11+ / Node 20+
# 说明：PY 可覆盖解释器路径，例如 make dev PY=/usr/bin/python3

PY ?= python
WEB := packages/web
PORT ?= 8000
# 数据层与引擎层的 import 根：Windows 用 `;`，POSIX 用 `:`
PYSEP := $(if $(filter Windows_NT,$(OS)),;,:)
export PYTHONPATH := packages/core$(PYSEP)packages/data$(PYSEP)packages/engine

.DEFAULT_GOAL := help
.PHONY: help install dev api web build check layers audit docker sync status

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## 安装前后端依赖
	$(PY) -m pip install -q "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2"
	cd $(WEB) && npm ci

sync: ## 全量同步真实行情（首次约 19 分钟，之后增量约 1 分钟）
	$(PY) -u -m alphaforge_data sync --only all --workers 12

status: ## 查看行情库状态（标的数 / 日线数 / 最近同步）
	$(PY) -m alphaforge_data status

# 热重载必须逐目录指定，**不能靠默认的「监视当前工作目录」**：
# 默认会把 `data/` 也监视上，而自动驾驶每 5 分钟写一次 autopilot.json、
# 收盘任务还写 lab.json——每次写盘都触发一次 reload，等于自己把自己重启死。
# 这大概就是有人把手动启动命令里的 --reload 摘掉的原因；摘掉之后又变成
# 「改了后端不生效」，两种都是坑，所以这里把四个代码目录写死。
RELOAD := --reload \
	--reload-dir packages/core --reload-dir packages/data \
	--reload-dir packages/engine --reload-dir packages/api

api: ## 仅启动后端（热重载 packages/*，:$(PORT)）
	$(PY) -m uvicorn alphaforge_api.main:app --port $(PORT) --app-dir packages/api $(RELOAD)

web: ## 仅启动前端（Vite，:5173，/api 代理到 :$(PORT)）
	cd $(WEB) && npm run dev

dev: ## 提示前后端分离开发时需开两个终端
	@echo "终端 1: make api"
	@echo "终端 2: make web   → http://localhost:5173"

build: ## 构建前端产物（供后端单进程托管）
	cd $(WEB) && npm run build

check: ## 全量检查：四层架构边界 + 前端类型
	$(PY) scripts/check_layers.py
	cd $(WEB) && npx vue-tsc --noEmit

layers: ## 只跑四层架构边界检查（core ← data ← engine ← api）
	$(PY) scripts/check_layers.py --list

serve: build ## 构建后以单进程托管前后端（:$(PORT)）
	$(PY) -m uvicorn alphaforge_api.main:app --port $(PORT) --app-dir packages/api

audit: ## 三档断点布局审计（需先 make serve 或已有服务在 :$(PORT)）
	node scripts/audit-layout.cjs

docker: ## 构建并启动容器
	docker compose -f deploy/docker-compose.yml up --build

clean: ## 清理构建产物与缓存
	rm -rf $(WEB)/dist $(WEB)/node_modules/.vite
	find packages -name __pycache__ -type d -prune -exec rm -rf {} +
