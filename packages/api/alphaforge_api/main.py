"""FastAPI 入口。"""

from __future__ import annotations

import os
from pathlib import Path

# AF_ROOT / AF_WEB_DIST 由桌面壳注入：PyInstaller 的 onedir 产物里本文件位于
# `_internal/alphaforge_api/`，`parents[3]` 会指到 exe 目录之外，`.env` 与前端
# 产物一起找不到，服务起来就是一个只有 API 的空页面。
ROOT = Path(os.environ.get("AF_ROOT") or Path(__file__).resolve().parents[3])
WEB_DIST = Path(os.environ.get("AF_WEB_DIST") or (ROOT / "packages" / "web" / "dist"))
API_PREFIX = "/api/v1"


def _load_dotenv() -> None:
    """把仓库根目录的 `.env` 读进环境变量。

    为什么必须自己读：`python -m uvicorn` 不会加载 `.env`，而 `.env.example`
    又明写着「复制为 .env 后按需修改」——用户照着做、填好 `AF_LLM_API_KEY`，
    结果 AI 分析一直走规则版，还找不到原因。缺的是这一小段胶水，不是文档。

    规则两条：
    - **已存在的环境变量优先**。命令行 `export` 的临时覆盖要能压过文件，
      否则排查问题时改不回来。
    - 解析失败一律忽略。一个手写的配置文件不该有能力让服务起不来。
    """
    path = ROOT / ".env"
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")
    except Exception:                                    # noqa: BLE001
        pass


# 必须在下面这些 import 之前调用：`store.py` / `lab_store.py` / `db.py` 里的
# `AF_STORE_PATH` / `AF_LAB_PATH` / `AF_DB` 都是在**导入那一刻**读环境变量的，
# 放到 import 之后再加载 `.env`，这些路径配置会被静默忽略。
_load_dotenv()

from fastapi import FastAPI, Request                          # noqa: E402
from fastapi.middleware.cors import CORSMiddleware            # noqa: E402
from fastapi.responses import FileResponse, JSONResponse      # noqa: E402
from fastapi.staticfiles import StaticFiles                   # noqa: E402

from alphaforge_api import __version__, auth                  # noqa: E402
from alphaforge_api.routers import auth as auth_router        # noqa: E402
from alphaforge_api.routers import data as data_router        # noqa: E402
from alphaforge_api.routers import lab as lab_router          # noqa: E402
from alphaforge_api.routers import (                          # noqa: E402
    market, overview, portfolio, select, signal, system, watchlist,
)

CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "AF_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000",
    ).split(",")
    if o.strip()
]

app = FastAPI(
    title="AlphaForge API",
    version=__version__,
    description="AlphaForge 阿尔法熔炉 · 个人量化工作台 API",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth_router, system, market, watchlist, portfolio, select, signal,
               overview, lab_router, data_router):
    app.include_router(module.router, prefix=API_PREFIX)


@app.middleware("http")
async def require_login(request: Request, call_next):
    """统一登录守卫。

    放在中间件而不是每个路由器上挂 `Depends`：新增接口不会被顺手漏掉，
    「默认拒绝」比「记得加依赖」可靠。静态资源与 SPA 外壳不拦（见 auth.is_public），
    否则未登录时连登录页都加载不出来。
    """
    if request.method == "OPTIONS" or auth.is_public(request.url.path):
        return await call_next(request)
    if auth.current_user(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)
    return JSONResponse(
        status_code=401,
        content={"code": 401, "msg": "未登录或登录已过期", "data": None},
    )


@app.on_event("startup")
def _load_reference_data():
    """启动时把行业清单注入字段词典。

    行业的可选值是运行期数据（跟着标的表变），所以不能写死在 `fields.py` 里，
    否则前端条件构造器的行业下拉要么是空的，要么是过期的。
    """
    try:
        from alphaforge_core.fields import set_industries
        from alphaforge_api.deps import market as _market

        set_industries(_market().repo.industries())
    except Exception:                       # noqa: BLE001 - 数据没同步也要能起服务
        pass

    # 上次关掉服务时如果是开着的，重启后接着跑——否则「自动驾驶」会被一次
    # 重启静默关停，而用户第二天打开页面才发现今天一天没盯盘。
    try:
        from alphaforge_api.autopilot import get_pilot

        pilot = get_pilot()
        if pilot.status().get("enabled"):
            pilot.start()
    except Exception:                       # noqa: BLE001
        pass


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"code": 1001, "msg": str(exc), "data": None})


@app.get("/api/health", include_in_schema=False)
def api_health():
    return {"code": 0, "msg": "ok", "data": {"status": "UP", "version": __version__}}


# 判断用 assets 目录而不是 dist 本身：`vite build` 会先清空 dist 再写产物，
# 这中间重启服务的话 dist 存在但 assets 还没生成，StaticFiles 会直接抛异常把启动打断。
if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        target = WEB_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(WEB_DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def root():
        return {
            "code": 0,
            "msg": "AlphaForge API 已启动；前端构建产物不存在，请运行 npm --prefix packages/web run build，或使用 Vite 开发服务器 5173 端口",
            "data": {"docs": "/api/docs", "version": __version__},
        }
