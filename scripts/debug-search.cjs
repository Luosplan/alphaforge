// 搜索弹窗回归验证：键入后弹窗必须存活、结果必须渲染、控制台不得报错。
// 覆盖两条路径：
//   1. 常见标的（600519）—— 有价格
//   2. 次新股（森峰 920085.BJ）—— factor 表无行，价格为空，必须显示「—」
//      （这条就是原 bug：fmtNum(undefined) 抛错把整个 dialog 渲染打崩）
const { chromium } = require('playwright-core')

const PROBE = () => {
  const dialog = document.querySelector('.search-dialog')
  const input = document.querySelector('.search-dialog input')
  const rows = [...document.querySelectorAll('.search-dialog .result')]
  return {
    dialogPresent: !!dialog,
    inputValue: input ? input.value : null,
    rowCount: rows.length,
    firstRow: rows[0]
      ? {
          code: rows[0].querySelector('.code')?.textContent?.trim(),
          price: rows[0].querySelector('.price')?.textContent?.trim(),
          chg: rows[0].querySelector('.chg')?.textContent?.trim(),
        }
      : null,
    emptyText: document.querySelector('.search-dialog .empty')?.textContent?.trim().slice(0, 30) || null,
  }
}

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge' })
  const page = await browser.newContext({ viewport: { width: 1440, height: 1000 } }).then((c) => c.newPage())

  const errors = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().split('\n')[0])
  })
  page.on('pageerror', (e) => errors.push(`[pageerror] ${e.message}`))

  await page.goto('http://127.0.0.1:8000/overview', { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)

  let failed = 0
  const check = (label, ok, extra) => {
    console.log(`${ok ? '✓' : '✗'} ${label}${extra ? '  ' + extra : ''}`)
    if (!ok) failed += 1
  }

  // ---- 场景 1：普通标的
  await page.click('.trigger')
  await page.waitForTimeout(400)
  await page.click('.search-dialog input')
  await page.keyboard.type('600519', { delay: 50 })
  await page.waitForTimeout(900)
  let s = await page.evaluate(PROBE)
  check('键入 600519 后弹窗仍存在', s.dialogPresent)
  check('输入框内容完整', s.inputValue === '600519', `value=${s.inputValue}`)
  check('有搜索结果', s.rowCount > 0, `rows=${s.rowCount}`)
  check('首行是贵州茅台且有价格', s.firstRow?.code === '600519.SH' && s.firstRow?.price === '1,453.00',
    JSON.stringify(s.firstRow))

  // ---- 场景 2：清空后搜次新股（价格为空）
  await page.fill('.search-dialog input', '')
  await page.waitForTimeout(300)
  await page.keyboard.type('森峰', { delay: 60 })
  await page.waitForTimeout(900)
  s = await page.evaluate(PROBE)
  check('键入「森峰」后弹窗仍存在', s.dialogPresent)
  check('无价格标的仍出现在结果里', s.rowCount > 0, `rows=${s.rowCount}`)
  check('价格位退化为「—」而不是崩掉', s.firstRow?.price === '—', JSON.stringify(s.firstRow))

  // ---- 场景 3：拼音与行业
  await page.fill('.search-dialog input', '')
  await page.waitForTimeout(200)
  await page.keyboard.type('gzmt', { delay: 50 })
  await page.waitForTimeout(800)
  s = await page.evaluate(PROBE)
  check('拼音 gzmt 命中贵州茅台', s.firstRow?.code === '600519.SH', JSON.stringify(s.firstRow))

  check('控制台零错误', errors.length === 0, errors.slice(0, 3).join(' | '))

  console.log(failed ? `\n失败 ${failed} 项` : '\n全部通过')
  await browser.close()
  process.exit(failed ? 1 : 0)
})()
