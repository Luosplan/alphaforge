"""把世界陆地栅格化成点阵，供总览页「全球指数」地图做底图。

为什么是点阵而不是矢量轮廓：底图只是给 16 个指数点位提供空间参照，
点阵没有国界线，不会踩到「边界画错」的合规红线；同时它比一整份
world.json（1MB）轻两个量级，可以直接内联进前端产物、离线可用。

数据源用 `world-atlas` 的 `land-110m`（Natural Earth 的陆地图层，只含
陆地、不含任何行政区划），55KB。生成物是**一次性提交**的常量，
平时不用重跑；换分辨率或换数据源时才需要 `python scripts/build_world_mask.py`。

用法：
    python scripts/build_world_mask.py            # 用缓存，没有就下载
    python scripts/build_world_mask.py --cols 140 # 换密度
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "land-110m.json"
OUT = ROOT / "packages" / "web" / "src" / "assets" / "worldDots.ts"

SRC = "https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json"

# 南极洲在 110m 数据里是一大块平直拖尾，留着会把整张图往下坠，
# 而全球主要交易所最南只到悉尼（-33.9），砍掉它不影响任何点位。
# 上界留到 80°：76°–78° 一带是加拿大北极群岛与北地群岛，压到 78 会让它们
# 挤在画布第一行、看起来像一条没来由的横线。
LAT_MIN, LAT_MAX = -56.0, 80.0


def load_topology() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(SRC, headers={"User-Agent": "alphaforge/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    CACHE.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))


def decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    """TopoJSON 的弧段是「量化 + 增量编码」的，要还原成经纬度折线。"""
    scale = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    out: list[list[tuple[float, float]]] = []
    for arc in topo["arcs"]:
        x = y = 0
        pts: list[tuple[float, float]] = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * scale[0] + tx, y * scale[1] + ty))
        out.append(pts)
    return out


def ring_points(ring: list[int], arcs: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    """把一个环的弧段索引拼成闭合折线。负索引表示该弧段反向使用。

    拼完还要「解开跨越 180° 经线」的接缝：斐济、楚科奇这类跨日界线的
    地物在数据里是从 -180 直接跳到 +179 的，照原样做射线法会得到一条
    横贯全图的假边，把整个太平洋判成陆地。做法是让经度连续（允许超出
    ±180），判定时再把待测点平移 ±360 各试一次。
    """
    pts: list[tuple[float, float]] = []
    for idx in ring:
        arc = arcs[~idx][::-1] if idx < 0 else arcs[idx]
        if pts and arc and pts[-1] == arc[0]:
            arc = arc[1:]
        pts.extend(arc)
    if not pts:
        return pts

    out: list[tuple[float, float]] = [pts[0]]
    for lon, lat in pts[1:]:
        while lon - out[-1][0] > 180.0:
            lon -= 360.0
        while lon - out[-1][0] < -180.0:
            lon += 360.0
        out.append((lon, lat))

    # 闭合边也要跟着解开，否则首尾相差一整圈会补出一条横贯全图的假边，
    # 奇偶判定会被它掀翻（表现为非洲与欧亚大陆整块消失）
    close = out[0][0]
    while close - out[-1][0] > 180.0:
        close -= 360.0
    while close - out[-1][0] < -180.0:
        close += 360.0
    out.append((close, out[0][1]))
    return out


def collect_rings(topo: dict) -> list[list[tuple[float, float]]]:
    arcs = decode_arcs(topo)
    geom = topo["objects"]["land"]
    shapes = geom["geometries"] if geom.get("type") == "GeometryCollection" else [geom]
    rings: list[list[tuple[float, float]]] = []
    for shape in shapes:
        gtype, arcs_idx = shape.get("type"), shape.get("arcs") or []
        if gtype == "Polygon":
            rings.extend(ring_points(r, arcs) for r in arcs_idx)
        elif gtype == "MultiPolygon":
            for poly in arcs_idx:
                rings.extend(ring_points(r, arcs) for r in poly)
    return [r for r in rings if len(r) >= 4]


def inside(lon: float, lat: float, rings: list[list[tuple[float, float]]]) -> bool:
    """奇偶规则判断点是否在陆地内（孔洞会被自动排除）。

    环的经度已被 `ring_points` 解开成连续值，可能与待测点相差一整圈，
    所以三个经度窗都试：原值、+360、-360。命中任一即算在内。
    """
    for shift in (0.0, 360.0, -360.0):
        x = lon + shift
        hit = False
        for ring in rings:
            for i in range(len(ring) - 1):
                x1, y1 = ring[i]
                x2, y2 = ring[i + 1]
                if (y1 > lat) != (y2 > lat):
                    xin = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
                    if x < xin:
                        hit = not hit
        if hit:
            return True
    return False


def build(cols: int, rows: int) -> tuple[str, int]:
    topo = load_topology()
    rings = collect_rings(topo)
    # 先用经纬度包围盒粗筛，避掉近万个格子 × 全部环的逐点扫描。
    # 注意：环解开后经度可能整体落在 -377..-170 这种区间，所以过滤也要
    # 跟着试三个经度窗，否则「落在另一圈里的欧亚大陆」会被整块筛掉。
    boxes = [(min(p[0] for p in r), max(p[0] for p in r),
              min(p[1] for p in r), max(p[1] for p in r)) for r in rings]
    paired = list(zip(rings, boxes))
    shifts = (0.0, 360.0, -360.0)

    step_lat = (LAT_MAX - LAT_MIN) / rows
    parts: list[str] = []
    for row in range(rows):
        lat = LAT_MAX - (row + 0.5) * step_lat
        by_lat = [(r, b) for r, b in paired if b[2] <= lat <= b[3]]
        for col in range(cols):
            lon = -180.0 + (col + 0.5) * step_lat
            candidates = [r for r, b in by_lat
                          if any(b[0] <= lon + s <= b[1] for s in shifts)]
            if candidates and inside(lon, lat, candidates):
                # 每个点画成一条零长线段，靠 round linecap 渲染成圆点，
                # 比圆形的 arc 指令省一半字符
                parts.append(f"M{col * step_lat:.1f} {row * step_lat:.1f}h.01")

    # 路径坐标系：x 从 0 到 360，y 从 0 到 rows*step（约 136），由组件缩放到容器
    return "".join(parts), len(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=120)
    args = ap.parse_args()
    rows = round(args.cols * (LAT_MAX - LAT_MIN) / 360.0)

    path, count = build(args.cols, rows)
    span_x = 360.0
    span_y = rows * (LAT_MAX - LAT_MIN) / rows

    header = (
        "// 由 scripts/build_world_mask.py 生成，请勿手改。\n"
        "// 数据源：world-atlas land-110m（Natural Earth 陆地图层，不含行政区划）。\n"
        "// 每个点是一条零长线段，靠 stroke-linecap=\"round\" 渲染成圆点。\n"
        f"export const WORLD_DOTS = '{path}'\n"
        f"export const WORLD_DOTS_W = {span_x}\n"
        f"export const WORLD_DOTS_H = {span_y:.1f}\n"
        f"export const WORLD_LAT_MIN = {LAT_MIN}\n"
        f"export const WORLD_LAT_MAX = {LAT_MAX}\n"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(header, encoding="utf-8")
    print(f"{count} dots → {OUT.relative_to(ROOT)} "
          f"({OUT.stat().st_size / 1024:.1f}KB, {args.cols}×{rows} grid)")


if __name__ == "__main__":
    main()
