/**
 * 实时报价轮询。
 *
 * 三条规则，都是踩过才知道的：
 *
 * 1. **用递归 setTimeout 而不是 setInterval**。行情响应偶尔会慢于轮询周期，
 *    setInterval 会把请求堆起来，越堆越慢，最后整整晚一轮。递归法保证
 *    「上一次结束才排下一次」。
 * 2. **页面不可见就不轮询**。切到别的标签页还在每 3 秒打接口，除了烧
 *    数据源配额没有任何意义，而且回来时会看到一堆乱序响应。
 * 3. **收盘后降频而不是停掉**。停掉的话，用户把页面放一晚上，第二天开盘
 *    还得手动刷新；改成 60 秒探一次，开盘自动恢复。
 *
 * 顺序问题同样要处理：慢响应不能覆盖快响应，所以每次请求带序号。
 */
import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { api } from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { IndexQuote, MarketSession, LiveQuote } from '@/types'

const LIVE_MS = 3000
const IDLE_MS = 60_000

export function useLiveQuotes(codes: Ref<string[]>, interval = LIVE_MS) {
  const auth = useAuthStore()
  const items = ref<Record<string, LiveQuote>>({})
  const indices = ref<IndexQuote[]>([])
  const asOf = ref('')
  const session = ref<MarketSession>('closed')
  const sessionLabel = ref('')
  const isLive = ref(false)
  const streaming = ref(false)
  const error = ref('')

  let timer: number | undefined
  let seq = 0
  let disposed = false

  function clear() {
    if (timer) window.clearTimeout(timer)
    timer = undefined
  }

  function schedule(delay: number) {
    clear()
    if (disposed) return
    timer = window.setTimeout(() => void tick(), delay)
  }

  async function tick() {
    if (disposed) return
    // 登出后不再续轮询：否则退出瞬间还会打出几个必然 401 的请求
    if (!auth.authenticated) {
      schedule(IDLE_MS)
      return
    }
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
      schedule(interval)
      return
    }
    const list = codes.value.filter(Boolean)
    const mine = ++seq

    try {
      const data = await api.marketLive(list)
      if (mine !== seq || disposed) return
      const map: Record<string, LiveQuote> = {}
      for (const row of data.items ?? []) map[row.code] = row
      items.value = map
      if (data.indices?.length) indices.value = data.indices
      asOf.value = data.as_of || ''
      session.value = data.session
      sessionLabel.value = data.session_label
      isLive.value = data.is_live
      streaming.value = data.streaming
      error.value = ''
    } catch {
      // 失败不弹错：轮询是持续行为，下一轮自然补上。
      // 只在界面上把「实时」标记降级成「数据延迟」。
      if (mine === seq) error.value = '实时报价暂不可用'
    }

    schedule(isLive.value ? interval : IDLE_MS)
  }

  function onVisible() {
    if (document.visibilityState === 'visible') {
      if (timer) window.clearTimeout(timer)
      void tick()
    }
  }

  document.addEventListener('visibilitychange', onVisible)

  // 代码集合变化（切票、加自选）立刻重新拉一次，不等下一个周期
  watch(
    () => codes.value.join(','),
    () => void tick(),
  )

  void tick()

  onBeforeUnmount(() => {
    disposed = true
    clear()
    document.removeEventListener('visibilitychange', onVisible)
  })

  return { items, indices, asOf, session, sessionLabel, isLive, streaming, error }
}
