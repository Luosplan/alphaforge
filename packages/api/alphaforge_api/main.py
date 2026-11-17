"""FastAPI 入口。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from alphaforge_api import __version__
from alphaforge_api.routers import data as data_router
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

for module in (system, market, watchlist, portfolio, select, signal, overview, data_router):
    app.include_router(module.router, prefix=API_PREFIX)


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


if WEB_DIST.exists():
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
