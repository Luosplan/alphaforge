r"""桌面入口：本机起后端，再用系统 WebView2 开一个窗口。

**为什么后端跑在线程里而不是独立进程**
打包后只带一份 Python 运行时，再开一个进程要把它整个复制一遍；线程模式下
窗口一关进程就退出，不会留下占着端口的僵尸服务，也就不需要写守护/回收逻辑。

**为什么数据目录要和 exe 分开**
行情库 793MB，塞进安装目录的话换一版 exe 就得整个覆盖一次，用户的自选、
持仓、复盘日记跟着一起没了。代码归代码，数据归 `%LOCALAPPDATA%\AlphaForge`。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
REPO_ROOT = Path(__file__).resolve().parents[3]

if not FROZEN:
    for _name in ("core", "data", "engine", "api"):
        sys.path.insert(0, str(REPO_ROOT / "packages" / _name))


def _bundle_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", REPO_ROOT))


def _web_dist() -> Path:
    if FROZEN:
        return _bundle_dir() / "web" / "dist"
    return REPO_ROOT / "packages" / "web" / "dist"


def _default_home() -> Path:
    """开发态直接复用仓库 data/，那里面已经有同步好的行情库。"""
    if FROZEN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AlphaForge"
    return REPO_ROOT


def _prepare(home: Path) -> None:
    data = home / "data"
    data.mkdir(parents=True, exist_ok=True)
    # 这五个路径都是各存储在**导入时**读的，必须在 import alphaforge_api 之前落定
    os.environ["AF_ROOT"] = str(home)
    os.environ["AF_WEB_DIST"] = str(_web_dist())
    os.environ["AF_DB"] = str(data / "alphaforge.db")
    os.environ["AF_STORE_PATH"] = str(data / "store.json")
    os.environ["AF_LAB_PATH"] = str(data / "lab.json")
    os.environ["AF_AUTOPILOT_PATH"] = str(data / "autopilot.json")
    os.environ["AF_AUTH_PATH"] = str(data / "auth.json")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServiceUnavailable(RuntimeError):
    pass


def _open_streams(home: Path) -> None:
    """双击启动时没有控制台，`sys.stdout` / `sys.stderr` 都是 None。
    uvicorn 在这里配日志会抛 `ValueError: Unable to configure formatter 'default'`，
    后端线程一声不吭就死掉，界面只能干等到超时——所以必须先把流补上。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    fp = (home / "server.log").open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = fp
    if sys.stderr is None:
        sys.stderr = fp


def _serve(port: int) -> None:
    import uvicorn

    from alphaforge_api.main import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def _wait_ready(port: int, timeout: float = 10.0) -> None:
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise ServiceUnavailable(f"{timeout:.0f}s 内没有就绪：{url}")


def _assert_web(port: int) -> None:
    """健康检查过不代表页面在：前端产物没打进包时后端照样返回 200，
    窗口里却只是一个「前端产物不存在」的 API 提示。"""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        body = resp.read()
    if b'id="app"' not in body:
        raise ServiceUnavailable("前端产物未挂载")


def _log(home: Path, text: str) -> None:
    """无控制台，启动过程和失败原因只能落文件，否则用户只看到「点了没反应」。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with (home / "launcher.log").open("a", encoding="utf-8") as fp:
        fp.write(f"{stamp} {text}\n")


def _run(home: Path, port: int, check: bool) -> None:
    threading.Thread(target=_serve, args=(port,), daemon=True, name="af-api").start()
    _wait_ready(port)
    _assert_web(port)
    _log(home, f"ready port={port} home={home}")

    if check:
        return

    import webview

    webview.create_window(
        "AlphaForge",
        f"http://127.0.0.1:{port}/",
        width=1440,
        height=900,
        min_size=(1120, 720),
    )
    webview.start(gui="edgechromium")


def main() -> None:
    parser = argparse.ArgumentParser(prog="AlphaForge")
    parser.add_argument("--home", type=Path, help="数据目录，默认 %%LOCALAPPDATA%%\\AlphaForge")
    parser.add_argument("--port", type=int, default=0, help="后端端口，默认挑空闲端口")
    parser.add_argument("--check", action="store_true", help="只做启动自检，不开窗口")
    args = parser.parse_args()

    home = args.home.expanduser().resolve() if args.home else _default_home()
    _prepare(home)
    _open_streams(home)

    try:
        _run(home, args.port or _free_port(), args.check)
    except ServiceUnavailable as exc:
        _log(home, f"fail {exc}")
        if not args.check:
            ctypes.windll.user32.MessageBoxW(0, "服务不可用", "AlphaForge", 0x10)
        sys.exit(1)


if __name__ == "__main__":
    main()

