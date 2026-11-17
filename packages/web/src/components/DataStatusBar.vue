<script setup lang="ts">
/**
 * 数据状态条：这一行是为了回答「这些数字是哪来的、什么时候的」。
 *
 * 判断标准很简单——如果界面上出现了一个涨跌幅，用户必须能在两秒内查到
 * 它的来源与截止时间；否则「真实数据」和「模拟数据」在界面上没有区别。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { api } from '@/api'

const store = useAppStore()
const open = ref(false)
const busy = ref(false)

const status = computed(() => store.dataStatus)
const ready = computed(() => status.value?.ready ?? false)
const running = computed(() => status.value?.sync?.running ?? false)

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

async function refresh() {
  await store.loadDataStatus()
}

async function sync() {
  busy.value = true
  try {
    await api.dataSync('all', 10)
    ElMessage.success('同步已开始，进度可在这里查看')
    await refresh()
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

onMounted(refresh)
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
      同步中 · {{ stepLabel[status?.sync?.step ?? ''] ?? status?.sync?.step ?? '准备' }}
    </span>
    <span v-else-if="!ready" class="tag">数据未就绪，点右侧「详情」发起同步</span>
    <span class="spacer" />
    <button class="link" type="button" @click="openDetail">详情</button>
  </div>

  <el-dialog v-model="open" title="数据来源与同步状态" width="640px" align-center>
    <div class="dlg">
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
              <td :class="run.status === 'ok' ? 'up' : run.status === 'fail' ? 'down' : ''">
                {{ run.status === 'ok' ? '成功' : run.status === 'fail' ? '失败' : run.status }}
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
      <el-button type="primary" :loading="busy" :disabled="running" @click="sync">
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
  padding: 0 20px;
  height: 30px;
  border-bottom: 1px solid var(--af-border);
  background: var(--af-surface, transparent);
  font-size: 11px;
  color: var(--af-ink-3);
  flex-shrink: 0;
}
.dbar.warn {
  background: color-mix(in srgb, var(--af-warn, #b54708) 6%, transparent);
}
.dot {
  width: 6px;
  height: 6px;
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
  background: color-mix(in srgb, var(--af-brand) 12%, transparent);
  color: var(--af-brand);
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

@media (max-width: 899px) {
  .hide-sm {
    display: none;
  }
  .dbar {
    padding: 0 10px;
  }
}
</style>
