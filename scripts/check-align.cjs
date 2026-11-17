// 比对表头与首行列的横向位置，验证列对齐
const { chromium } = require('playwright-core')

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge' })
  for (const w of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: 900 } })
    const page = await ctx.newPage()
    await page.goto('http://127.0.0.1:8000/screener', { waitUntil: 'networkidle' })
    await page.waitForTimeout(1200)
    const out = await page.evaluate(() => {
      const head = document.querySelector('.row-head')
      const row = document.querySelector('.rows .row')
      if (!head || !row) return { err: 'missing' }
      const cells = (el) =>
        [...el.children]
          .filter((c) => getComputedStyle(c).display !== 'none')
          .map((c) => {
            const b = c.getBoundingClientRect()
            return { t: (c.textContent || '').trim().slice(0, 6), l: Math.round(b.left), r: Math.round(b.right) }
          })
      return { head: cells(head), row: cells(row) }
    })
    console.log('width', w)
    console.log(' head', JSON.stringify(out.head))
    console.log(' row ', JSON.stringify(out.row))
    await ctx.close()
  }
  await browser.close()
})()
