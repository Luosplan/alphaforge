<script setup lang="ts">
/**
 * 数据状态条：这一行是为了回答「这些数字是哪来的、什么时候的」。
 *
 * 判断标准很简单——如果界面上出现了一个涨跌幅，用户必须能在两秒内查到
 * 它的来源与截止时间；否则「真实数据」和「模拟数据」在界面上没有区别。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { api } from '@/api'

const store = useAppStore()
const open = ref(false)
const busy = ref(false)

const status = computed(() => store.dataStatus)
const ready = computed(() => status.value?.ready ?? false)
const sync = computed(() => status.value?.sync)
const running = computed(() => sync.value?.running ?? false)

const coverage = computed(() => {
  const symbols = status.value?.symbols ?? 0
  return symbols ? `全 A ${symbols} 只` : '尚未同步'
})

const asOf = computed(() => {
  const date = status.value?.trade_date
  return date ? `截至 ${date}` : '无行情数据'
})

const dbSize = computed(() => {
  const mb = status.value?.db_size_mb ?? 0
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(0)} MB`
})

const sourceNames = computed(
  () => (store.dataSources?.sources ?? []).map((s) => s.name).join(' · ') || '新浪财经 · 腾讯行情 · 东方财富',
)

const stepLabel: Record<string, string> = {
  universe: '标的名单',
  fundamentals: 'ROE / 行业',
  bonus: '分红送配',
  daily: '日线',
  flow: '资金流',
  index: '指数',
  factor: '因子',
  starting: '启动中',
}

function fmtTime(value: string | null | undefined) {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 16)
}

/** 秒 → 「2 分 14 秒」，ETA 这种数字比 134 秒好读 */
function fmtDur(seconds: number) {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  const rest = s % 60
  return rest ? `${m} 分 ${rest} 秒` : `${m} 分`
}

const stepName = computed(
  () => sync.value?.label || stepLabel[sync.value?.step ?? ''] || sync.value?.step || '准备',
)

/** 状态条上那行：日线 3,210 / 5,578 · 58% · 剩 2 分 14 秒 */
const progressText = computed(() => {
  const s = sync.value
  if (!s?.running) return ''
  const parts = [stepName.value]
  // total<=1 表示这一步只有一次请求（标的名单 / 分红 / 指数）：
  // 报「0 / 1」和「0%」都像是卡住了，改成只说这一步跑了多久
  if (s.total > 1) {
    // 「5,331 / 5,580」是这一步的分母，和总进度不是一个口径，
    // 所以总进度必须带「总」字，否则两个百分比挨在一起会互相打架
    parts.push(`${s.done.toLocaleString()} / ${s.total.toLocaleString()}`)
    parts.push(`总 ${Math.round(s.overall)}%`)
  } else if (s.elapsed) {
    parts.push(`已用 ${fmtDur(s.elapsed)}`)
  }
  if (s.eta && s.eta > 3) parts.push(`剩 ${fmtDur(s.eta)}`)
  return parts.join(' · ')
})

/** 单请求步骤没有百分比可言，进度条改成「来回跑」的不确定态 */
const indeterminate = computed(() => (sync.value?.total ?? 0) <= 1)

const elapsedText = computed(() => (sync.value?.elapsed ? fmtDur(sync.value.elapsed) : ''))

/* 同步在后台跑，前端不轮询的话进度条永远停在发起那一刻 */
let timer: number | undefined

function stopPolling() {
  if (timer) window.clearInterval(timer)
  timer = undefined
}

function startPolling() {
  if (timer) return
  timer = window.setInterval(async () => {
    await store.loadDataStatus()
    if (!store.dataStatus?.sync?.running) stopPolling()
  }, 2000)
}

async function refresh() {
  await store.loadDataStatus()
}

async function startSync(only: 'all' | 'daily', force = false) {
  busy.value = true
  try {
    await api.dataSync(only, 24, force)
    ElMessage.success(only === 'daily' ? '开始补日线，进度显示在这里' : '同步已开始，进度显示在这里')
    await refresh()
    startPolling()
  } catch (err) {
    ElMessage.warning(err instanceof Error ? err.message : '发起同步失败，可能已有任务在跑')
  } finally {
    busy.value = false
  }
}

function openDetail() {
  store.loadDataSources()
  refresh()
  open.value = true
}

onMounted(async () => {
  await refresh()
  if (running.value) startPolling()
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="dbar" :class="{ warn: !ready }">
    <span class="dot" />
    <span class="txt">{{ coverage }}</span>
    <span class="sep" />
    <span class="txt num">{{ asOf }}</span>
    <span class="sep hide-sm" />
    <span class="txt muted hide-sm">源 {{ sourceNames }}</span>
    <span v-if="running" class="tag running">
      <span class="prog-track" :class="{ indet: indeterminate }">
        <span class="prog-fill" :style="{ width: `${sync?.overall ?? 0}%` }" />
      </span>
      {{ progressText }}
    </span>
    <span v-else-if="!ready" class="tag">数据未就绪，点右侧「详情」发起同步</span>
    <span class="spacer" />
    <button class="link" type="button" @click="openDetail">详情</button>
  </div>

  <el-dialog v-model="open" title="数据来源与同步状态" width="640px" align-center>
    <div class="dlg">
      <!-- 正在同步：先给进度，再给明细数字 -->
      <section v-if="running" class="block progress-block">
        <div class="block-head">
          正在同步 · {{ stepName }}
          <span class="muted num" v-if="elapsedText">已用 {{ elapsedText }}{{ sync?.eta ? ` · 约剩 ${fmtDur(sync.eta)}` : '' }}</span>
        </div>
        <div class="p-bar" :class="{ indet: indeterminate }">
          <span class="p-fill" :style="{ width: `${sync?.overall ?? 0}%` }" />
        </div>
        <div class="p-meta">
          <span class="num">
            <template v-if="indeterminate">
              {{ stepName }} · 第 {{ (sync?.steps_done ?? 0) + 1 }}/{{ sync?.steps_total || 1 }} 步
            </template>
            <template v-else>
              总进度 {{ Math.round(sync?.overall ?? 0) }}%
              <template v-if="(sync?.steps_total ?? 0) > 1">
                · 第 {{ (sync?.steps_done ?? 0) + 1 }}/{{ sync?.steps_total }} 步
              </template>
            </template>
          </span>
          <span class="num" v-if="(sync?.total ?? 0) > 1">{{ sync?.done.toLocaleString() }} / {{ sync?.total?.toLocaleString() }} 只</span>
        </div>
        <div class="p-msg muted" v-if="sync?.message">{{ sync?.message }}</div>
      </section>

      <section class="row">
        <div class="kv">
          <span class="k">覆盖标的</span><span class="v num">{{ status?.symbols ?? 0 }} 只</span>
        </div>
        <div class="kv">
          <span class="k">行情截止</span><span class="v num">{{ status?.trade_date ?? '—' }}</span>
        </div>
        <div class="kv">
          <span class="k">本地库</span><span class="v num">{{ dbSize }}</span>
        </div>
      </section>

      <section class="block">
        <div class="block-head">数据来源</div>
        <div v-for="src in store.dataSources?.sources ?? []" :key="src.name" class="src">
          <span class="src-name">{{ src.name }}</span>
          <span class="src-role">{{ src.role }}</span>
        </div>
      </section>

      <section class="block">
        <div class="block-head">口径说明</div>
        <ul class="notes">
          <li v-for="note in store.dataSources?.notes ?? []" :key="note">{{ note }}</li>
        </ul>
      </section>

      <section class="block">
        <div class="block-head">最近同步</div>
        <table class="runs">
          <thead>
            <tr><th>步骤</th><th>状态</th><th class="r">行数</th><th class="r">完成时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="(run, i) in status?.runs ?? []" :key="i">
              <td>{{ stepLabel[run.step] ?? run.step }}</td>
              <td :class="run.status === 'fail' ? 'fail' : ''">
                {{ run.status === 'ok' ? '成功' : run.status === 'fail' ? '失败' : run.status }}
                <span v-if="run.note" class="muted" :title="run.note">· {{ run.note }}</span>
              </td>
              <td class="r num">{{ run.rows == null ? '—' : run.rows.toLocaleString() }}</td>
              <td class="r num muted">{{ fmtTime(run.finished) }}</td>
            </tr>
            <tr v-if="!(status?.runs ?? []).length">
              <td colspan="4" class="muted">还没有跑过同步</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <template #footer>
      <el-button @click="open = false">关闭</el-button>
      <el-button :loading="busy" :disabled="running" @click="startSync('daily')">只补日线</el-button>
      <el-button :loading="busy" :disabled="running" @click="startSync('all', true)">强制重跑</el-button>
      <el-button type="primary" :loading="busy" :disabled="running" @click="startSync('all')">
        {{ running ? '同步进行中' : '立即同步全部' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.dbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 22px;
  height: 30px;
  border-bottom: 1px solid var(--af-border);
  background: var(--af-surface-2);
  font-size: 11px;
  color: var(--af-ink-3);
  flex-shrink: 0;
}
.dbar.warn {
  background: color-mix(in srgb, var(--af-warn, #b54708) 6%, transparent);
}
.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  /* 用主色不用红：红绿在这个系统里只表达涨跌（ADR 0003） */
  background: var(--af-brand);
  flex-shrink: 0;
}
.warn .dot {
  background: var(--af-warn, #b54708);
}
.txt {
  white-space: nowrap;
}
.num {
  font-variant-numeric: tabular-nums;
}
.muted {
  color: var(--af-ink-4);
}
.sep {
  width: 1px;
  height: 10px;
  background: var(--af-border);
}
.spacer {
  flex: 1;
}
.tag {
  padding: 1px 6px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--af-warn, #b54708) 12%, transparent);
  color: var(--af-warn, #b54708);
  white-space: nowrap;
}
.tag.running {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: color-mix(in srgb, var(--af-brand) 12%, transparent);
  color: var(--af-brand);
}

/* 状态条上的细进度：宽度 64px，只表意不抢注意力 */
.prog-track {
  width: 64px;
  height: 4px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--af-brand) 18%, transparent);
  overflow: hidden;
  flex: 0 0 auto;
}

.prog-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--af-brand);
  transition: width 0.4s ease;
}

/* 对话框里的进度块 */
.progress-block {
  border: 1px solid var(--af-border);
  border-radius: 9px;
  padding: 11px 12px;
  background: var(--af-surface-2);
}

.progress-block .block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.p-bar {
  height: 6px;
  border-radius: 999px;
  background: var(--af-bg-deep, var(--af-surface-3));
  overflow: hidden;
}

.p-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--af-brand);
  transition: width 0.4s ease;
}

.p-meta {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--af-ink-2);
}

.p-msg {
  margin-top: 4px;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 单请求步骤（标的名单 / 分红 / 指数）：没有百分比，用来回跑的条表示「在动」 */
.indet .prog-fill,
.indet .p-fill {
  width: 38% !important;
  transition: none;
  animation: af-indet 1.15s ease-in-out infinite;
}

@keyframes af-indet {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(300%);
  }
}
.link {
  border: 0;
  background: none;
  color: var(--af-brand);
  font-size: 11px;
  cursor: pointer;
  padding: 0 2px;
}
.link:hover {
  text-decoration: underline;
}

.dlg {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.row {
  display: flex;
  gap: 22px;
  flex-wrap: wrap;
}
.kv {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kv .k {
  font-size: 11px;
  color: var(--af-ink-4);
}
.kv .v {
  font-size: 15px;
  font-weight: 600;
}
.block-head {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--af-ink-2);
}
.src {
  display: flex;
  gap: 10px;
  font-size: 12px;
  padding: 3px 0;
  border-bottom: 1px dashed var(--af-border);
}
.src-name {
  width: 84px;
  flex-shrink: 0;
  font-weight: 600;
}
.src-role {
  color: var(--af-ink-3);
}
.notes {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  line-height: 1.75;
  color: var(--af-ink-3);
}
.runs {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.runs th,
.runs td {
  text-align: left;
  padding: 5px 8px;
  border-bottom: 1px solid var(--af-border);
}
.runs th {
  font-weight: 500;
  color: var(--af-ink-4);
  font-size: 11px;
}
.runs .r {
  text-align: right;
}
.up {
  color: var(--af-up);
}
.down {
  color: var(--af-down);
}
/* 同步结果是运行状态，不是涨跌：成功不染红绿，失败走琥珀 */
.fail {
  color: var(--af-warn);
  font-weight: 600;
}

@media (max-width: 899px) {
  .hide-sm {
    display: none;
  }
  .dbar {
    padding: 0 10px;
  }
}
</style>
