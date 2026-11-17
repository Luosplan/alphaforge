<script setup lang="ts">
/**
 * 指数详情抽屉：点指数卡片后展开。
 *
 * 回答「这个指数现在什么状态」三个问题：
 * 区间走势如何（图）、多周期涨跌多少（5/20/60 日）、是哪些成分在带节奏（领涨/领跌）。
 * 成分可点，直接把主图切到那只票——指数到个股的下钻不该来回找代码。
 */
import { computed, ref, watch } from 'vue'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import type { IndexDetail } from '@/types'
import { fmtNum, fmtPct, toneOf } from '@/utils/format'
import IndexTrendChart from '@/components/IndexTrendChart.vue'

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'pick', code: string): void
}>()

const store = useAppStore()
const detail = ref<IndexDetail | null>(null)
const loading = ref(false)
const days = ref(60)
const failed = ref('')

const visible = computed({
  get: () => !!props.modelValue,
  set: (open: boolean) => {
    if (!open) emit('update:modelValue', '')
  },
})

const leaders = computed(() => (detail.value?.constituents ?? []).slice(0, 6))
const laggards = computed(() => {
  // 优先用接口单独取的跌幅榜；没有时才退回列表尾部（老接口兼容）
  const top = detail.value?.laggards
  if (top?.length) return top.slice(0, 6)
  return (detail.value?.constituents ?? []).slice(-6).reverse()
})

async function load() {
  if (!props.modelValue) return
  loading.value = true
  failed.value = ''
  try {
    detail.value = await api.indexDetail(props.modelValue, days.value)
  } catch {
    detail.value = null
    failed.value = '指数数据读取失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch(() => props.modelValue, (code) => {
  if (code) {
    detail.value = null
    load()
  }
})

watch(days, load)

function pick(code: string) {
  emit('pick', code)
  visible.value = false
}
</script>

<template>
  <el-drawer
    v-model="visible"
    :size="store.isMobile ? '100%' : 588"
    class="index-drawer"
  >
    <template #header>
      <div class="drawer-head">
        <div class="dh-left">
          <span class="drawer-title">{{ detail?.name ?? '指数详情' }}</span>
          <span class="drawer-sub num">{{ modelValue }}</span>
        </div>
        <span v-if="detail && detail.has_constituents" class="dh-pill">
          成分 {{ detail.members }} 只
        </span>
      </div>
    </template>

    <div v-if="loading && !detail" class="tip">正在读取指数行情…</div>
    <div v-else-if="failed" class="tip">{{ failed }}</div>

    <div v-else-if="detail" class="body">
      <!-- 点位 -->
      <section class="hero">
        <div class="hero-main">
          <span class="hero-value num" :class="toneOf(detail.change_pct)">{{ fmtNum(detail.value, 2) }}</span>
          <span class="hero-chg num" :class="toneOf(detail.change_pct)">
            {{ detail.change > 0 ? '+' : '' }}{{ fmtNum(detail.change, 2) }} {{ fmtPct(detail.change_pct) }}
          </span>
        </div>
        <div class="hero-span">
          <span v-for="item in [
            { k: '5 日', v: detail.chg5 },
            { k: '20 日', v: detail.chg20 },
            { k: '60 日', v: detail.chg60 },
          ]" :key="item.k" class="hs">
            <span class="k">{{ item.k }}</span>
            <span class="v num" :class="toneOf(item.v)">{{ fmtPct(item.v) }}</span>
          </span>
        </div>
      </section>

      <!-- 走势 -->
      <section class="block">
        <header class="block-head">
          <span class="block-title">走势</span>
          <el-radio-group v-model="days" size="small" class="days">
            <el-radio-button :value="60">60 日</el-radio-button>
            <el-radio-button :value="120">120 日</el-radio-button>
            <el-radio-button :value="250">250 日</el-radio-button>
          </el-radio-group>
        </header>
        <div v-loading="loading" class="chart-wrap">
          <IndexTrendChart :series="detail.series" :height="store.isMobile ? 176 : 208" />
        </div>
      </section>

      <!-- 结构：只有拿到官方成分表才有「涨跌家数」这一说 -->
      <section v-if="detail.has_constituents" class="block">
        <header class="block-head">
          <span class="block-title">成分结构</span>
          <span class="block-sub">基于 {{ detail.members }} 只官方成分股</span>
        </header>
        <div class="grid4">
          <div class="cell"><span class="k">上涨</span><span class="v num up">{{ detail.up }}</span></div>
          <div class="cell"><span class="k">下跌</span><span class="v num down">{{ detail.down }}</span></div>
          <div class="cell"><span class="k">平盘</span><span class="v num">{{ detail.flat }}</span></div>
          <div class="cell"><span class="k">成交额</span><span class="v num">{{ detail.amount.toFixed(0) }} 亿</span></div>
        </div>
        <div class="bar">
          <span class="seg up" :style="{ width: `${(detail.up / Math.max(detail.members, 1)) * 100}%` }" />
          <span class="seg down" :style="{ width: `${(detail.down / Math.max(detail.members, 1)) * 100}%` }" />
        </div>
      </section>

      <!-- 领涨 / 领跌 -->
      <section v-if="detail.has_constituents" class="block">
        <header class="block-head">
          <span class="block-title">成分表现</span>
          <span class="block-sub">点击切换到主图</span>
        </header>
        <div class="cols">
          <div class="col">
            <div class="col-head"><span>领涨</span><span class="muted">涨跌幅</span></div>
            <button
              v-for="row in leaders"
              :key="row.code"
              class="row"
              type="button"
              @click="pick(row.code)"
            >
              <span class="rn">{{ row.name }}</span>
              <span class="rc num">{{ row.code }}</span>
              <span class="rp num" :class="toneOf(row.change_pct)">{{ fmtPct(row.change_pct) }}</span>
            </button>
          </div>
          <div class="col">
            <div class="col-head"><span>领跌</span><span class="muted">涨跌幅</span></div>
            <button
              v-for="row in laggards"
              :key="row.code"
              class="row"
              type="button"
              @click="pick(row.code)"
            >
              <span class="rn">{{ row.name }}</span>
              <span class="rc num">{{ row.code }}</span>
              <span class="rp num" :class="toneOf(row.change_pct)">{{ fmtPct(row.change_pct) }}</span>
            </button>
          </div>
        </div>
      </section>
    </div>
  </el-drawer>
</template>

<style scoped>
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dh-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.drawer-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--af-ink);
}

.drawer-sub {
  font-size: 11px;
  color: var(--af-ink-3);
}

.dh-pill {
  padding: 1px 8px;
  border-radius: 4px;
  background: var(--af-surface-3);
  color: var(--af-ink-2);
  font-size: 10.5px;
}

.tip {
  padding: 40px 0;
  text-align: center;
  color: var(--af-ink-3);
  font-size: 12px;
}

.body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-bottom: 8px;
}

/* ------------------------------------------------------------ 点位 */
.hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 14px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
}

.hero-main {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.hero-value {
  font-size: 27px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.hero-chg {
  font-size: 12.5px;
  font-weight: 600;
}

.hero-span {
  display: flex;
  gap: 16px;
}

.hs {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.hs .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.hs .v {
  font-size: 12.5px;
  font-weight: 600;
}

/* ------------------------------------------------------------ 区块 */
.block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.block-title {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--af-ink);
}

.block-sub {
  font-size: 10.5px;
  color: var(--af-ink-4);
}

.days :deep(.el-radio-button__inner) {
  padding: 4px 10px;
  font-size: 11.5px;
}

.chart-wrap {
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  padding: 4px 6px 2px;
}

.grid4 {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.cell .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.cell .v {
  font-size: 14px;
  font-weight: 650;
}

.bar {
  display: flex;
  height: 5px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--af-surface-3);
}

.seg.up {
  background: var(--af-up);
}

.seg.down {
  background: var(--af-down);
}

/* ------------------------------------------------------------ 成分 */
.cols {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.col {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.col-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 6px 4px;
  font-size: 10.5px;
  color: var(--af-ink-3);
  border-bottom: 1px solid var(--af-border);
}

.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 34px;
  grid-template-areas: 'name pct' 'code pct';
  align-items: center;
  gap: 0 6px;
  padding: 6px 6px;
  border: 0;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  transition: background 0.14s ease;
}

.row:hover {
  background: var(--af-surface-2);
}

.rn {
  grid-area: name;
  font-size: 12px;
  font-weight: 550;
  color: var(--af-ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rc {
  grid-area: code;
  font-size: 10px;
  color: var(--af-ink-4);
}

.rp {
  grid-area: pct;
  font-size: 11.5px;
  font-weight: 600;
  text-align: right;
}

.muted {
  color: var(--af-ink-4);
}

@media (max-width: 767px) {
  .cols {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
  .hero {
    padding: 10px 12px;
  }
  .hero-value {
    font-size: 23px;
  }
  .grid4 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
}
</style>
