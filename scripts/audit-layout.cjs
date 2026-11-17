// 布局审计：输出各路由关键区块的几何信息，替代整页截图复核
const { chromium } = require('playwright-core')

const ROUTES = ['/overview', '/market', '/screener', '/signals', '/portfolio']
const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 1000 },
  { name: 'tablet', width: 1024, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]

const COLLECT = () => {
  const r = (el) => {
    const b = el.getBoundingClientRect()
    return {
      x: Math.round(b.x),
      y: Math.round(b.y),
      w: Math.round(b.width),
      h: Math.round(b.height),
    }
  }
  const sel = ['.topbar', '.strip', '.content', '.split', '.panel', '.panel-head', '.toolbar', '.bottom-nav', 'footer']
  const out = {}
  for (const s of sel) {
    const list = [...document.querySelectorAll(s)]
    if (!list.length) continue
    out[s] = list.slice(0, 6).map(r)
  }
  // 溢出检测：水平溢出
  const docW = document.documentElement.clientWidth
  const overflow = []
  for (const el of document.querySelectorAll('.content *')) {
    const b = el.getBoundingClientRect()
    if (b.width === 0) continue
    if (b.right > docW + 1 || b.left < -1) {
      overflow.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 60),
        left: Math.round(b.left),
        right: Math.round(b.right),
        w: Math.round(b.width),
      })
    }
  }
  // 文本截断检测
  const clipped = []
  for (const el of document.querySelectorAll('.content *')) {
    if (el.children.length > 2) continue
    if (el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflow !== 'visible') {
      const t = (el.textContent || '').trim().slice(0, 24)
      if (t) clipped.push({ cls: (el.className || '').toString().slice(0, 40), text: t, sw: el.scrollWidth, cw: el.clientWidth })
    }
  }
  return { docW, scrollH: document.documentElement.scrollHeight, blocks: out, overflow: overflow.slice(0, 8), clipped: clipped.slice(0, 8) }
}

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge' })
  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } })
    const page = await ctx.newPage()
    for (const route of ROUTES) {
      await page.goto('http://127.0.0.1:8000' + route, { waitUntil: 'networkidle' })
      await page.waitForTimeout(900)
      const data = await page.evaluate(COLLECT)
      console.log(`\n===== ${vp.name} ${vp.width}x${vp.height} ${route} =====`)
      console.log(`docW=${data.docW} scrollH=${data.scrollH}`)
      for (const [k, v] of Object.entries(data.blocks)) {
        console.log(`  ${k.padEnd(12)} ${JSON.stringify(v)}`)
      }
      if (data.overflow.length) console.log('  OVERFLOW:', JSON.stringify(data.overflow))
      if (data.clipped.length) console.log('  CLIPPED :', JSON.stringify(data.clipped))
    }
    await ctx.close()
  }
  await browser.close()
})()
