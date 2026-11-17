const { chromium } = require('playwright-core')

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  await page.goto('http://127.0.0.1:8000/signals?plan=PLAN-20261106-300750-7165', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1800)

  const out = await page.evaluate(() => {
    const chart = window.__afChart
    if (!chart) return { err: 'no chart' }
    const canvas = document.querySelector('canvas')
    const ctx = canvas.getContext('2d')
    const rect = canvas.getBoundingClientRect()
    const sample = (x, y) => {
      const p = ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data
      return [p[0], p[1], p[2], p[3]]
    }
    const pt = (xIdx, price) => chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [xIdx, price])
    const res = {}
    for (const [name, price] of [
      ['entry', 207.2],
      ['add', 201.3],
      ['stop', 194.2],
      ['target', 240],
      ['empty', 290],
    ]) {
      const p = pt(140, price)
      res[name] = { pixel: p, rgba: sample(p[0], p[1]) }
    }
    return { size: [rect.width, rect.height], canvas: [canvas.width, canvas.height], res }
  })
  console.log(JSON.stringify(out, null, 2))
  await browser.close()
})()
