// 实验室页截图：用 data/auth.json 里的活会话塞 cookie 绕过登录
const fs = require('fs')
const path = require('path')
const { chromium } = require('playwright-core')

const sid = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'auth.json'), 'utf8'))
  .sessions[0].sid

const BASE = 'http://localhost:5173'
const OUT = path.join(__dirname, '..', 'data', 'shots')

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge' })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  await ctx.addCookies([{ name: 'af_session', value: sid, domain: 'localhost', path: '/' }])
  const p = await ctx.newPage()
  const errors = []
  p.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  p.on('pageerror', (e) => errors.push(String(e)))

  await p.goto(`${BASE}/lab`, { waitUntil: 'networkidle' })
  await p.waitForTimeout(1500)
  await p.screenshot({ path: path.join(OUT, 'lab-board.png') })

  // 点第二条（第一条是「全部策略」）：默认应落在「收益概览」
  const first = p.locator('.sitem').nth(1)
  if (await first.count()) {
    await first.click()
    await p.waitForTimeout(1200)
    await p.screenshot({ path: path.join(OUT, 'lab-overview.png') })
    console.log(
      'tabs:',
      await p.locator('.lab-tabs .el-tabs__item').allInnerTexts(),
      'active:',
      await p.locator('.lab-tabs .el-tabs__item.is-active').innerText(),
    )
    // 滚到底，确认没有 footer、页签吸顶正常
    await p.evaluate(() => {
      const el = document.querySelector('.main')
      if (el) el.scrollTop = el.scrollHeight
    })
    await p.waitForTimeout(600)
    await p.screenshot({ path: path.join(OUT, 'lab-overview-bottom.png') })
    console.log('footer:', await p.locator('footer').count())
  }
  console.log('errors:', errors)
  await browser.close()

  // 手机端：页签条横向滚动 + 七格退成 2 列
  const m = await chromium.launch({ channel: 'msedge' })
  const mctx = await m.newContext({ viewport: { width: 390, height: 844 }, isMobile: true })
  await mctx.addCookies([{ name: 'af_session', value: sid, domain: 'localhost', path: '/' }])
  const mp = await mctx.newPage()
  await mp.goto(`${BASE}/lab`, { waitUntil: 'networkidle' })
  await mp.waitForTimeout(1200)
  await mp.locator('.sitem').nth(1).click()
  await mp.waitForTimeout(1000)
  await mp.screenshot({ path: path.join(OUT, 'lab-mobile.png') })
  await m.close()
})()
