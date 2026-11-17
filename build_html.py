# -*- coding: utf-8 -*-
"""把 SRS Markdown 渲染为自包含 HTML（侧边栏目录 + 样式化表格 + 打印友好）。"""
import re
import sys
from pathlib import Path

import markdown

BASE = Path(__file__).resolve().parent
SRC = BASE / "SRS-AlphaForge-v1.1.md"
OUT = BASE / "SRS-AlphaForge-v1.1.html"

raw = SRC.read_text(encoding="utf-8")

# 1. 保护裸 HTML 区块（架构图 / 结尾块），避免被 Markdown 拆散
protected = {}


def stash(m):
    key = "\n\n@@BLOCK%d@@\n\n" % len(protected)
    protected[key.strip()] = m.group(0)
    return key


raw = re.sub(r'<div class="fig">.*?</svg>\s*</div>', stash, raw, flags=re.S)
raw = re.sub(r'<div class="doc-end">.*?</div>', stash, raw, flags=re.S)

# 2. Markdown -> HTML
md = markdown.Markdown(
    extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
    extension_configs={"toc": {"toc_depth": "2-3", "permalink": False}},
)
body = md.convert(raw)
toc_html = md.toc

# 3. 还原 HTML 区块
for key, html in protected.items():
    body = body.replace("<p>%s</p>" % key, html).replace(key, html)

# 4. 表格包一层滚动容器
body = body.replace("<table>", '<div class="tbl-wrap"><table>')
body = body.replace("</table>", "</table></div>")

# 5. 优先级徽章
for p, cls in (("P0", "p0"), ("P1", "p1"), ("P2", "p2"), ("P3", "p3")):
    body = body.replace("<td>%s</td>" % p, '<td><span class="badge %s">%s</span></td>' % (cls, p))

# 6. 首个表格（文档信息）加卡片样式
body = body.replace('<div class="tbl-wrap">', '<div class="tbl-wrap meta-wrap">', 1)

# 6.5 纯日期单元格禁止折行（窄列里 "2026-11-06" 会被断成两行）
body = re.sub(r'<td>(\d{4}-\d{2}-\d{2})</td>', r'<td class="nowrap">\1</td>', body)

# 7. 目录：把 div.toc 整体换成 nav.toc（开闭标签必须同步替换，否则 main 会被嵌套进 nav 而裁切）
toc_html = re.sub(r'^<div class="toc">', '<nav class="toc" id="tocNav">', toc_html.strip())
toc_html = re.sub(r'</div>\s*$', '</nav>', toc_html)
assert toc_html.startswith("<nav") and toc_html.endswith("</nav>"), toc_html[:80]

CSS = """
:root{
  --accent:#486ff2; --accent-soft:#eef2ff; --ink:#111827; --ink-2:#374151;
  --ink-3:#6b7280; --line:#e5e8ef; --line-2:#eef1f6; --bg:#f6f7f9; --card:#ffffff;
  --code-bg:#f4f6fa;
}
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{display:flex; align-items:flex-start; max-width:1500px; margin:0 auto;}

/* ---------- 侧边栏 ---------- */
aside{
  position:sticky; top:0; height:100vh; width:290px; flex:0 0 290px;
  background:var(--card); border-right:1px solid var(--line);
  display:flex; flex-direction:column;
}
.brand{padding:20px 20px 14px; border-bottom:1px solid var(--line-2);}
.brand .kicker{font-size:11px; letter-spacing:.14em; color:var(--accent); font-weight:700;}
.brand h1{font-size:15px; margin:6px 0 4px; line-height:1.4; font-weight:700;}
.brand .ver{font-size:11px; color:var(--ink-3);}
nav.toc{overflow-y:auto; padding:12px 10px 40px; flex:1; font-size:13px;}
nav.toc ul{list-style:none; margin:0; padding:0;}
nav.toc > ul > li{margin:1px 0;}
nav.toc a{
  display:block; padding:5px 10px; border-radius:6px; color:var(--ink-2);
  text-decoration:none; line-height:1.45; border-left:2px solid transparent;
}
nav.toc a:hover{background:var(--accent-soft); color:var(--accent);}
nav.toc a.active{background:var(--accent-soft); color:var(--accent); font-weight:700; border-left-color:var(--accent);}
nav.toc ul ul{margin:0 0 4px 0;}
nav.toc ul ul a{padding-left:22px; font-size:12.5px; color:var(--ink-3);}

/* ---------- 主体 ---------- */
main{flex:1 1 auto; min-width:0; padding:34px 48px 100px; max-width:1180px;}
main > h1:first-child{
  font-size:27px; line-height:1.35; margin:0 0 6px; letter-spacing:-.01em;
}
main h2{
  font-size:20px; margin:44px 0 14px; padding-bottom:9px;
  border-bottom:2px solid var(--line); scroll-margin-top:20px;
}
main h2:first-of-type{margin-top:26px;}
main h3{font-size:16px; margin:28px 0 10px; color:#0f172a; scroll-margin-top:20px;}
main h4{font-size:14.5px; margin:20px 0 8px;}
p{margin:10px 0; color:var(--ink-2);}
strong{color:var(--ink); font-weight:600;}
hr{border:0; border-top:1px solid var(--line); margin:38px 0;}
a{color:var(--accent);}
ul,ol{padding-left:22px; color:var(--ink-2);}
li{margin:4px 0;}

/* ---------- 表格 ---------- */
.tbl-wrap{overflow-x:auto; margin:16px 0 22px; border:1px solid var(--line); border-radius:10px; background:var(--card);}
table{border-collapse:collapse; width:100%; font-size:13px; line-height:1.6;}
thead th{
  background:#f1f4fb; text-align:left; font-weight:600; color:#0f172a;
  padding:9px 12px; border-bottom:1px solid var(--line); white-space:nowrap;
}
tbody td{padding:8px 12px; border-bottom:1px solid var(--line-2); vertical-align:top; color:var(--ink-2);}
tbody tr:last-child td{border-bottom:0;}
tbody tr:nth-child(even){background:#fafbfd;}
tbody tr:hover{background:var(--accent-soft);}
td:first-child{color:var(--ink); font-weight:500;}
td.nowrap{white-space:nowrap;}

.meta-wrap table{font-size:13.5px;}
.meta-wrap thead{display:none;}
.meta-wrap td:first-child{width:150px; color:var(--ink-3); font-weight:500; background:#fafbfd;}

/* ---------- 徽章 ---------- */
.badge{display:inline-block; padding:1px 7px; border-radius:20px; font-size:11.5px; font-weight:700; letter-spacing:.02em;}
.badge.p0{background:#fee2e2; color:#b91c1c;}
.badge.p1{background:#fef3c7; color:#b45309;}
.badge.p2{background:#dbeafe; color:#1d4ed8;}
.badge.p3{background:#e5e7eb; color:#4b5563;}

/* ---------- 代码 ---------- */
code{
  background:var(--code-bg); padding:1.5px 6px; border-radius:5px;
  font:12.5px/1.6 ui-monospace,SFMono-Regular,Consolas,"Courier New",monospace; color:#b3306b;
}
pre{
  background:#0f172a; color:#e2e8f0; padding:16px 18px; border-radius:10px;
  overflow-x:auto; font:12.5px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace; margin:14px 0 20px;
}
pre code{background:none; padding:0; color:inherit; font-size:12.5px;}

/* ---------- 引用块（场景/说明） ---------- */
blockquote{
  margin:16px 0; padding:12px 18px; background:#f8faff;
  border-left:3px solid var(--accent); border-radius:0 8px 8px 0; color:var(--ink-2);
}
blockquote p{margin:4px 0;}

/* ---------- 架构图 ---------- */
.fig{margin:22px 0 8px; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 8px;}
.fig svg{display:block; width:100%; height:auto;}

.doc-end{margin-top:50px; padding-top:16px; border-top:1px solid var(--line); color:var(--ink-3); font-size:13px;}

@media (max-width:1080px){
  aside{display:none;}
  main{padding:24px 18px 80px;}
  .tbl-wrap{border-radius:8px;}
}
@media print{
  aside{display:none;} body{background:#fff;} main{max-width:none; padding:0;}
  .tbl-wrap{border:1px solid #ddd;} tbody tr:hover{background:none;}
  pre{background:#f4f4f4; color:#111;}
  h2{page-break-after:avoid;} table{page-break-inside:avoid;}
}
"""

JS = """
const links=[...document.querySelectorAll('#tocNav a')];
const heads=links.map(a=>document.querySelector(a.getAttribute('href'))).filter(Boolean);
function spy(){
  const y=window.scrollY+120;
  let idx=0;
  heads.forEach((h,i)=>{ if(h.offsetTop<=y) idx=i; });
  links.forEach(l=>l.classList.remove('active'));
  if(links[idx]) links[idx].classList.add('active');
}
window.addEventListener('scroll',spy,{passive:true}); window.addEventListener('load',spy);
document.querySelectorAll('#tocNav a').forEach(a=>{
  a.addEventListener('click',e=>{
    const t=document.querySelector(a.getAttribute('href'));
    if(t){ e.preventDefault(); window.scrollTo({top:t.offsetTop-12,behavior:'smooth'}); history.replaceState(null,'',a.getAttribute('href')); }
  });
});
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AlphaForge · 阿尔法熔炉 · 软件需求开发文档 v1.1</title>
<style>%s</style>
</head>
<body>
<div class="wrap">
  <aside>
    <div class="brand">
      <div class="kicker">SRS · AF-001</div>
      <h1>AlphaForge · 阿尔法熔炉</h1>
      <div class="ver">软件需求开发文档 v1.1 · 2026-11-06</div>
    </div>
    %s
  </aside>
  <main>%s</main>
</div>
<script>%s</script>
</body>
</html>
""" % (CSS, toc_html, body, JS)

OUT.write_text(HTML, encoding="utf-8")
print("OK ->", OUT)
print("toc_items:", toc_html.count("<a href="))
print("tables:", body.count("<table>"))
print("svg:", body.count("<svg"))
print("size_kb:", round(len(HTML) / 1024, 1))
