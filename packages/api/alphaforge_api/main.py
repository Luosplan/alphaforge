"""FastAPI 入口。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from alphaforge_api import __version__, auth
from alphaforge_api.routers import auth as auth_router
from alphaforge_api.routers import data as data_router
from alphaforge_api.routers import lab as lab_router
from alphaforge_api.routers import market, overview, portfolio, select, signal, system, watchlist

ROOT = Path(__file__).resolve().parents[3]
WEB_DIST = ROOT / "packages" / "web" / "dist"
API_PREFIX = "/api/v1"
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
