// 侧栏三态截图：展开 / 折叠 / 平板默认
const { chromium } = require('playwright-core')

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge' })

  const desk = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const p = await desk.newPage()
  await p.goto('http://127.0.0.1:8000/overview', { waitUntil: 'networkidle' })
  await p.waitForTimeout(1400)
  await p.screenshot({ path: '.shots/nav-expanded.png' })

  await p.click('.nav-toggle')
  await p.waitForTimeout(600)
  await p.screenshot({ path: '.shots/nav-collapsed.png' })

  // 折叠后刷新，验证 localStorage 记忆生效
  await p.reload({ waitUntil: 'networkidle' })
  await p.waitForTimeout(1200)
  const persisted = await p.evaluate(() => document.querySelector('.side')?.classList.contains('collapsed'))
  console.log('collapsed persisted after reload:', persisted)
  await desk.close()

  const tab = await browser.newContext({ viewport: { width: 1024, height: 800 } })
  const p2 = await tab.newPage()
  await p2.goto('http://127.0.0.1:8000/portfolio', { waitUntil: 'networkidle' })
  await p2.waitForTimeout(1400)
  await p2.screenshot({ path: '.shots/nav-tablet.png' })
  await tab.close()

  await browser.close()
})()
