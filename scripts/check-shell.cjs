// 校验侧栏/顶栏在三种断点下的实际几何与可见性
const { chromium } = require('playwright-core')

const CASES = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 1024, height: 800 },
  { name: 'mobile', width: 390, height: 844 },
]

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge' })
  for (const c of CASES) {
    const ctx = await browser.newContext({ viewport: { width: c.width, height: c.height } })
    const p = await ctx.newPage()
    await p.goto('http://127.0.0.1:8000/overview', { waitUntil: 'networkidle' })
    await p.waitForTimeout(1200)
    const r = await p.evaluate(() => {
      const box = (sel) => {
        const el = document.querySelector(sel)
        if (!el) return null
        const b = el.getBoundingClientRect()
        return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }
      }
      return {
        side: box('.side'),
        sideCollapsed: !!document.querySelector('.side.collapsed'),
        brand: box('.side .brand'),
        items: [...document.querySelectorAll('.side .item')].map((el) => ({
          label: el.textContent.trim(),
          active: el.classList.contains('active'),
          w: Math.round(el.getBoundingClientRect().width),
        })),
        collapseBtn: box('.side .collapse'),
        topbar: box('.appbar'),
        pageTitle: document.querySelector('.appbar .page-title')?.textContent?.trim() ?? null,
        mark: box('.appbar .mark'),
        content: box('.content'),
        bottomNav: box('.bottom-nav, .bottom'),
      }
    })
    console.log(`\n=== ${c.name} ${c.width}x${c.height} ===`)
    console.log(JSON.stringify(r, null, 1))
    await ctx.close()
  }
  await browser.close()
})()
