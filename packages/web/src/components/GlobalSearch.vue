<script setup lang="ts">
/**
 * 全局标的检索（顶栏入口 + ⌘/Ctrl + K）。
 *
 * 三个曾经踩过的点：
 * 1. 只防抖不防乱序 —— 快速输入时先发的请求后到，会把旧结果盖在新结果上。
 *    这里给每次请求编号，只有最新一次的结果才允许落到界面。
 * 2. el-dialog 里的 autofocus 只对首次挂载生效，第二次打开光标就不在了，
 *    改为在 opened 回调里显式 focus。
 * 3. 点击结果只 push 路由不保证页面刷新（同一路由仅 query 变化时组件被复用），
 *    所以这里连同 MarketView 的 query 监听一起改，两处都兜住。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import type { SearchHit } from '@/types'
import AppIcon from '@/components/AppIcon.vue'
import { fmtNum, fmtPct, toneOf } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const open = ref(false)
const keyword = ref('')
const hits = ref<SearchHit[]>([])
const loading = ref(false)
const active = ref(0)
const inputRef = ref<{ focus: () => void } | null>(null)

let debounceTimer: number | undefined
let requestSeq = 0

const hint = computed(() => (loading.value ? '检索中…' : ''))

watch(keyword, (value) => {
  window.clearTimeout(debounceTimer)
  const q = value.trim()
  if (!q) {
    requestSeq += 1
    hits.value = []
    loading.value = false
    active.value = 0
    return
  }
  loading.value = true
  debounceTimer = window.setTimeout(() => void run(q), 180)
})

async function run(q: string) {
  const seq = ++requestSeq
  try {
    const rows = await api.search(q, 10)
    if (seq !== requestSeq) return
    hits.value = rows
    active.value = 0
  } catch {
    if (seq === requestSeq) hits.value = []
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function openPalette() {
  open.value = true
  keyword.value = ''
  hits.value = []
  active.value = 0
}

async function onOpened() {
  await nextTick()
  inputRef.value?.focus()
}

function choose(hit?: SearchHit) {
  const target = hit ?? hits.value[active.value]
  if (!target) return
  open.value = false
  const next = { ...route.query, code: target.code }
  // 同一标的重复选中时 push 会报冗余导航，catch 掉即可（此时页面已经在该标的上）
  router.push({ name: 'market', query: next }).catch(() => undefined)
}

function move(step: number) {
  if (!hits.value.length) return
  const total = hits.value.length
  active.value = (active.value + step + total) % total
  const el = document.getElementById(`gs-hit-${active.value}`)
  el?.scrollIntoView({ block: 'nearest' })
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    move(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    move(-1)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    choose()
  }
}

/** 命中片段高亮：名称里能直接看到关键词匹配到哪一段，比单纯排前面更好确认 */
function segments(text: string | null | undefined): Array<{ text: string; hit: boolean }> {
  const raw = text ?? ''
  const q = keyword.value.trim()
  if (!q || !raw) return [{ text: raw, hit: false }]
  const lower = raw.toLowerCase()
  const idx = lower.indexOf(q.toLowerCase())
  if (idx < 0) return [{ text: raw, hit: false }]
  return [
    { text: raw.slice(0, idx), hit: false },
    { text: raw.slice(idx, idx + q.length), hit: true },
    { text: raw.slice(idx + q.length), hit: false },
  ].filter((p) => p.text)
}

function onGlobalKey(event: KeyboardEvent) {
  const isMac = /mac/i.test(navigator.platform || navigator.userAgent)
  const combo = isMac ? event.metaKey : event.ctrlKey
  if (combo && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    openPalette()
  }
}

onMounted(() => window.addEventListener('keydown', onGlobalKey))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKey)
  window.clearTimeout(debounceTimer)
})
</script>

<template>
  <button class="trigger" type="button" title="搜索标的（⌘/Ctrl + K）" @click="openPalette">
    <AppIcon name="search" />
    <span class="trigger-text">搜索标的</span>
    <span class="kbd">⌘K</span>
  </button>

  <el-dialog
    v-model="open"
    width="min(560px, 94vw)"
    :show-close="false"
    align-center
    append-to-body
    class="search-dialog"
    @opened="onOpened"
  >
    <div class="search-head">
      <AppIcon name="search" class="head-icon" />
      <el-input
        ref="inputRef"
        v-model="keyword"
        size="large"
        placeholder="代码 / 名称 / 拼音"
        clearable
        @keydown="onKeydown"
      />
    </div>

    <div class="results">
      <div v-if="loading && !hits.length" class="empty">{{ hint }}</div>

      <button
        v-for="(r, i) in hits"
        :id="`gs-hit-${i}`"
        :key="r.code"
        class="result"
        :class="{ active: i === active }"
        type="button"
        @mouseenter="active = i"
        @click="choose(r)"
      >
        <span class="code num">
          <template v-for="(seg, si) in segments(r.code)" :key="si">
            <mark v-if="seg.hit">{{ seg.text }}</mark>
            <span v-else>{{ seg.text }}</span>
          </template>
        </span>
        <span class="name">
          <span class="nm">
            <template v-for="(seg, si) in segments(r.name)" :key="si">
              <mark v-if="seg.hit">{{ seg.text }}</mark>
              <span v-else>{{ seg.text }}</span>
            </template>
          </span>
          <span class="ind">{{ r.industry }}</span>
        </span>
        <span class="px num">
          <span class="price">{{ fmtNum(r.price, 2) }}</span>
          <span class="chg" :class="toneOf(r.change_pct)">{{ fmtPct(r.change_pct) }}</span>
        </span>
      </button>

      <div v-if="!loading && keyword && !hits.length" class="empty">
        没有匹配「{{ keyword.trim() }}」的标的
        <span class="empty-sub">全 A 股 5578 只，代码、名称、拼音首字母、行业均可检索</span>
      </div>
      <div v-if="!keyword" class="empty">
        输入关键词开始检索
        <span class="empty-sub">支持代码 600519 / 名称 茅台 / 拼音 gzmt / 行业 银行</span>
      </div>
    </div>

    <div class="foot">
      <span class="kb-hint"><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
      <span class="kb-hint"><kbd>Enter</kbd> 打开行情</span>
      <span class="kb-hint"><kbd>Esc</kbd> 关闭</span>
      <span class="foot-right">本地行情库</span>
      <button class="mob-close" type="button" @click="open = false">关闭</button>
    </div>
  </el-dialog>
</template>

<style scoped>
.trigger {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 8px 0 10px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
  color: var(--af-ink-3);
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.14s ease, color 0.14s ease, background 0.14s ease;
}

.trigger:hover {
  border-color: var(--af-border-strong);
  color: var(--af-ink);
  background: var(--af-surface);
}

.trigger :deep(.ai) {
  font-size: 15px;
}

.kbd {
  padding: 1px 4px;
  border: 1px solid var(--af-border);
  border-radius: 4px;
  background: var(--af-surface);
  color: var(--af-ink-4);
  font-size: 10px;
  font-family: inherit;
  line-height: 1.4;
}

.search-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.head-icon {
  font-size: 17px;
  color: var(--af-ink-3);
}

.results {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 1px;
  max-height: 46vh;
  overflow-y: auto;
}

.result {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-size: 13px;
  color: var(--af-ink);
}

.result.active {
  background: var(--af-brand-soft);
}

.result .code {
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.result .name {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

/* 名称必须单行省略：中文名 + 行业在内联排布下很容易被挤成两行 */
.result .nm {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 550;
}

.result .ind {
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 400;
  color: var(--af-ink-3);
  white-space: nowrap;
}

.result mark {
  background: transparent;
  color: var(--af-brand);
  font-weight: 700;
}

.px {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}

.price {
  font-weight: 550;
}

.chg {
  font-size: 11.5px;
  min-width: 52px;
  text-align: right;
}

.empty {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 22px 8px;
  color: var(--af-ink-3);
  font-size: 12.5px;
  text-align: center;
}

.empty-sub {
  font-size: 11px;
  color: var(--af-ink-4);
}

.foot {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--af-border);
  color: var(--af-ink-4);
  font-size: 11px;
}

.foot-right {
  margin-left: auto;
}

kbd {
  display: inline-block;
  min-width: 16px;
  margin-right: 3px;
  padding: 0 3px;
  border: 1px solid var(--af-border);
  border-radius: 3px;
  background: var(--af-surface-2);
  font-family: inherit;
  font-size: 10px;
  text-align: center;
  line-height: 15px;
}

/* 触屏没有物理键盘，快捷键提示反而挤占空间，换成显式关闭按钮 */
.mob-close {
  display: none;
  align-items: center;
  height: 26px;
  padding: 0 12px;
  border: 1px solid var(--af-border-strong);
  border-radius: 5px;
  background: var(--af-surface);
  color: var(--af-ink-2);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}

@media (max-width: 767px) {
  /* 小屏先让位给「代码 + 名称 + 涨跌幅」，行业信息在这里价值最低 */
  .result {
    grid-template-columns: 82px minmax(0, 1fr) auto;
    gap: 8px;
  }
  .result .ind {
    display: none;
  }
  .kb-hint {
    display: none;
  }
  .mob-close {
    display: inline-flex;
    order: 3;
    margin-left: 10px;
  }
  .foot-right {
    margin-left: 0;
    margin-right: auto;
  }
}

@media (max-width: 991px) {
  .trigger-text,
  .trigger .kbd {
    display: none;
  }
  .trigger {
    width: 30px;
    padding: 0;
    justify-content: center;
  }
}
</style>
