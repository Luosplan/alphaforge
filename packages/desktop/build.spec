# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

**onedir 而不是 onefile**
onefile 每次启动都要把整个运行环境解压到 temp，这个体量要几十秒，而且
「自解压 → 写 temp → 执行」的行为很容易被杀软判成可疑。onedir 只是目录大，
启动是即时的。用户拿到的是 `dist/AlphaForge/` 整个文件夹，双击里面的 exe。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent.parent
PKGS = ROOT / "packages"

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "webview.platforms.edgechromium",
    "alphaforge_api.routers.auth",
    "alphaforge_api.routers.data",
    "alphaforge_api.routers.lab",
    "alphaforge_api.routers.market",
    "alphaforge_api.routers.overview",
    "alphaforge_api.routers.portfolio",
    "alphaforge_api.routers.select",
    "alphaforge_api.routers.signal",
    "alphaforge_api.routers.system",
    "alphaforge_api.routers.watchlist",
]

# pypinyin 的词典是数据文件不是 .py，不显式收集的话股票名转拼音会在运行时 KeyError
datas = [(str(PKGS / "web" / "dist"), "web/dist")] + collect_data_files("pypinyin")

a = Analysis(
    [str(PKGS / "desktop" / "alphaforge_desktop" / "app.py")],
    pathex=[str(PKGS / n) for n in ("core", "data", "engine", "api", "desktop")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 引擎层是纯标准库实现（见 ADR 0001），科学计算栈一律不参与。
    # pywebview 在 Windows 上会依次探测 Qt 后端，conda 环境里装着 PySide6 时
    # 会把整个 Qt 拖进产物（GB 级）；显式排除后它才回落到 WebView2。
    excludes=[
        "PySide6", "PySide2", "PyQt5", "PyQt6", "shiboken6", "qtpy",
        "IPython", "traitlets", "jupyter_client", "jupyter_core",
        "matplotlib", "numpy", "pandas", "scipy",
        "tkinter", "pytest", "playwright",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AlphaForge",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(PKGS / "desktop" / "alphaforge.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AlphaForge",
)
