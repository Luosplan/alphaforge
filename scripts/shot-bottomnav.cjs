// 移动端底部 tab 局部截图，核对 20px 图标的可辨度与对齐
const { chromium } = require('playwright-core')

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge' })
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3,
  })
  await ctx.addInitScript(() => window.localStorage.removeItem('af.nav.collapsed'))
  const p = await ctx.newPage()
  await p.goto('http://127.0.0.1:8000/signals', { waitUntil: 'networkidle' })
  await p.waitForTimeout(1200)

  await p.screenshot({
    path: '.shots/zoom-bottomnav.png',
    clip: { x: 0, y: 844 - 62, width: 390, height: 62 },
  })

  const geo = await p.evaluate(() => {
    const bar = document.querySelector('.tabbar') || document.querySelector('.bottom-nav') || document.querySelector('nav.fixed')
    const tabs = [...document.querySelectorAll('[class*="tab"]')]
      .filter((el) => el.offsetHeight > 30 && el.offsetHeight < 80)
      .slice(0, 8)
      .map((el) => {
        const b = el.getBoundingClientRect()
        return { cls: el.className, y: Math.round(b.y), h: Math.round(b.height) }
      })
    return {
      bar: bar ? (() => { const b = bar.getBoundingClientRect(); return { cls: bar.className, y: Math.round(b.y), h: Math.round(b.height) } })() : null,
      tabs,
    }
  })
  console.log(JSON.stringify(geo, null, 2))
  await browser.close()
})()
