// 侧栏与 navbar 左侧局部放大截图，用于核对图标细节与账号区对齐
const { chromium } = require('playwright-core')

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge' })
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
  })
  // 清掉上一轮用例残留的折叠状态，保证从"展开"开始
  await ctx.addInitScript(() => window.localStorage.removeItem('af.nav.collapsed'))

  const p = await ctx.newPage()
  await p.goto('http://127.0.0.1:8000/', { waitUntil: 'networkidle' })
  await p.waitForTimeout(1200)

  const side = await p.$('.side')
  await side.screenshot({ path: '.shots/zoom-side-open.png' })

  await p.screenshot({
    path: '.shots/zoom-navbar.png',
    clip: { x: 0, y: 0, width: 430, height: 52 },
  })

  const foot = await p.$('.side-foot')
  await foot.screenshot({ path: '.shots/zoom-account.png' })

  const geoOpen = await p.evaluate(() => {
    const g = (sel) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const b = el.getBoundingClientRect()
      return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }
    }
    const navs = [...document.querySelectorAll('.side-nav a, .side-nav .nav, .side a')].map((a) => ({
      text: (a.textContent || '').trim(),
      ...g0(a),
    }))
    function g0(el) {
      const b = el.getBoundingClientRect()
      return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }
    }
    return {
      navToggle: g('.nav-toggle'),
      pageTitle: g('.page-title'),
      pageSub: g('.page-sub'),
      appbar: g('.appbar'),
      side: g('.side'),
      account: g('.side-foot'),
      avatar: g('.avatar'),
      accName: g('.acc-name'),
      accMeta: g('.acc-meta'),
      more: g('.acc-more'),
      navs,
    }
  })
  console.log('--- 展开态 ---')
  console.log(JSON.stringify(geoOpen, null, 2))

  await p.click('.nav-toggle')
  await p.waitForTimeout(700)

  const side2 = await p.$('.side')
  await side2.screenshot({ path: '.shots/zoom-side-collapsed.png' })
  const foot2 = await p.$('.side-foot')
  await foot2.screenshot({ path: '.shots/zoom-account-collapsed.png' })

  const geoCollapsed = await p.evaluate(() => {
    const g0 = (el) => {
      const b = el.getBoundingClientRect()
      return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }
    }
    const q = (sel) => {
      const el = document.querySelector(sel)
      return el ? g0(el) : null
    }
    return {
      navToggle: q('.nav-toggle'),
      side: q('.side'),
      avatar: q('.avatar'),
      accName: q('.acc-name'),
      more: q('.acc-more'),
      sideWidth: document.querySelector('.side')?.getBoundingClientRect().width,
    }
  })
  console.log('--- 折叠态 ---')
  console.log(JSON.stringify(geoCollapsed, null, 2))

  await browser.close()
})()
