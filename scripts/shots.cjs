// 分区截图：按视口截取，避免整页长图过大被内容过滤拦截
const { chromium } = require('playwright-core')

const ROUTES = (process.argv[2] || '/overview,/market,/screener,/signals,/portfolio').split(',')
const VP = (process.argv[3] || '1440x1000').split('x').map(Number)
const TAG = process.argv[4] || 'v3'

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge' })
  const ctx = await browser.newContext({ viewport: { width: VP[0], height: VP[1] }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  for (const route of ROUTES) {
    await page.goto('http://127.0.0.1:8000' + route, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)
    const name = route.replace(/\//g, '') || 'root'
    const file = `.shots/${TAG}-${name}-${VP[0]}.png`
    await page.screenshot({ path: file })
    console.log('saved', file)
  }
  await browser.close()
})()
