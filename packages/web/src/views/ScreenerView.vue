<script setup lang="ts">
/**
 * 选股工作台：三种入口共用一套结果视图。
 *
 * - 多因子：从策略库里选一套存好的权重跑（老路径）
 * - AI 问股：一句话 → 解析成条件 chips → 可删可改 → 跑
 * - 条件筛选：表单式构造条件，不写权重
 *
 * 三条路径最终都收敛到同一份 spec（weights / filters / sort / top_n），
 * 所以结果列表、详情、计划卡只写一份，不因入口不同而各写一套。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CaretRight, Close, CopyDocument, Delete, EditPen, RefreshRight } from '@element-plus/icons-vue'
import { api } from '@/api'
import type {
  AiParse,
  Candidate,
  Condition,
  FieldMeta,
  PlanCard,
  SelectResult,
  SortSpec,
  Strategy,
  StrategyPayload,
} from '@/types'
import { fmtPct, toneOf } from '@/utils/format'
import FactorBars from '@/components/FactorBars.vue'
import ZoneLadder from '@/components/ZoneLadder.vue'
import ConditionBuilder from '@/components/ConditionBuilder.vue'
import StrategyEditor from '@/components/StrategyEditor.vue'

type Mode = 'factor' | 'ai' | 'filter'

const MODES: Array<{ key: Mode; label: string; hint: string }> = [
  { key: 'factor', label: '多因子', hint: '用策略库里的权重打分排序' },
  { key: 'ai', label: 'AI 问股', hint: '用一句话描述选股条件' },
  { key: 'filter', label: '条件筛选', hint: '手工组合筛选条件' },
]

/* ------------------------------------------------------------ 状态 */
const mode = ref<Mode>('factor')
const strategies = ref<Strategy[]>([])
const fields = ref<FieldMeta[]>([])
const examples = ref<string[]>([])
const activeId = ref('')
const topN = ref(10)

const result = ref<SelectResult | null>(null)
const selected = ref<Candidate | null>(null)
const loading = ref(false)
const detailOpen = ref(false)

/* AI 模式 */
const aiText = ref('')
const parsed = ref<AiParse | null>(null)
/** 解析所依据的原句：句子改了就必须重新解析，不能拿旧条件去套新问题 */
const parsedText = ref('')
const aiFilters = ref<Condition[]>([])
const aiSort = ref<SortSpec | null>(null)
const aiDirty = ref(false)

/* 条件模式 */
const filterConditions = ref<Condition[]>([])
const filterSortBy = ref('')
const filterSortDesc = ref(true)

/* 策略编辑器 */
const editorOpen = ref(false)
const editing = ref<Strategy | null>(null)
const preset = ref<Partial<StrategyPayload> | null>(null)

/* 计划卡 */
const plan = ref<PlanCard | null>(null)
const planOpen = ref(false)
const planLoading = ref(false)

const current = computed(() => strategies.value.find((s) => s.id === activeId.value) ?? null)
const numericFields = computed(() => fields.value.filter((f) => f.kind === 'num'))

const sortLabel = computed(() => {
  const sort = result.value?.sort
  if (!sort) return '未指定'
  const label = sort.label || fields.value.find((f) => f.key === sort.by)?.label || sort.by
  return `${label} ${sort.desc ? '降序' : '升序'}`
})

const modeHint = computed(() => MODES.find((m) => m.key === mode.value)?.hint ?? '')

/* 纯条件模式下没有权重，Z 值加权和恒为 0——这时候显示「综合分 0.0」是误导读者的，
   换成真正决定顺序的排序值（后端已在 rank_value 里给到） */
const isFilterMode = computed(() => result.value?.mode === 'filter')
const scoreTitle = computed(() => (isFilterMode.value ? '排序值' : '综合分'))
const detailScoreTitle = computed(() => (isFilterMode.value ? '排序值' : '综合得分'))
function scoreText(c: Candidate): string {
  return isFilterMode.value ? c.rank_value.toFixed(2) : c.score.toFixed(1)
}

function targetText(target: unknown): string {
  if (Array.isArray(target)) return target.join('–')
  return String(target)
}

/* ------------------------------------------------------------ 运行 */
function payloadSpec(spec: Record<string, unknown>) {
  return api.runSelect({ spec, top_n: topN.value })
}

function filterSpec(): Record<string, unknown> {
  return {
    id: 'filter',
    name: filterConditions.value.length ? `条件筛选 · ${filterConditions.value.length} 条` : '条件筛选',
    weights: {},
    filters: filterConditions.value.map((c) => ({ field: c.field, op: c.op, value: c.value })),
    sort: filterSortBy.value ? { by: filterSortBy.value, desc: filterSortDesc.value } : null,
    top_n: topN.value,
    max_per_industry: 99,
    exclude_st: true,
  }
}

function aiSpec(): Record<string, unknown> {
  const p = parsed.value
  return {
    id: 'ai',
    name: p?.suggested_name || 'AI 问股',
    weights: p?.weights ?? {},
    filters: aiFilters.value.map((c) => ({ field: c.field, op: c.op, value: c.value })),
    sort: aiSort.value ? { by: aiSort.value.by, desc: aiSort.value.desc } : null,
    top_n: topN.value,
    max_per_industry: p?.max_per_industry ?? 2,
    exclude_st: p?.exclude_st ?? true,
  }
}

async function run() {
  loading.value = true
  try {
    if (mode.value === 'ai') {
      const text = aiText.value.trim()
      if (!text) {
        ElMessage.warning('先用一句话描述你想找什么')
        return
      }
      // 句子没变、只是删了条件 → 按剩下的条件跑；句子变了 → 重新解析
      if (parsed.value && aiDirty.value && parsedText.value === text) {
        apply(await payloadSpec(aiSpec()))
        return
      }
      const res = await api.runQuery(text, topN.value)
      parsed.value = res.parsed ?? null
      parsedText.value = text
      aiFilters.value = parsed.value?.filters ?? []
      aiSort.value = parsed.value?.sort ?? null
      aiDirty.value = false
      apply(res)
      return
    }
    const res =
      mode.value === 'factor'
        ? await api.runSelect({ strategy: activeId.value, top_n: topN.value })
        : await payloadSpec(filterSpec())
    apply(res)
  } finally {
    loading.value = false
  }
}

function apply(res: SelectResult) {
  result.value = res
  selected.value = res.items[0] ?? null
  detailOpen.value = false
  if (!res.items.length) {
    ElMessage.warning('没有标的命中这些条件，试着放宽一条')
  } else {
    ElMessage.success(`${res.matched_count} 只命中，取前 ${res.items.length} 只`)
  }
}

/** AI 结果里删一条条件：直接按剩下的条件重跑，不让人再点一次按钮 */
async function onAiFilters(value: Condition[]) {
  aiFilters.value = value
  aiDirty.value = true
  if (parsed.value) await run()
}

/** 从别的模式切到条件筛选时，把当前结果的条件带过来，避免面对一张空表单 */
watch(mode, (next) => {
  if (next !== 'filter' || filterConditions.value.length || !result.value) return
  filterConditions.value = result.value.filters.map((c) => ({ ...c }))
  filterSortBy.value = result.value.sort?.by ?? ''
  filterSortDesc.value = result.value.sort?.desc ?? true
})

function useExample(text: string) {
  aiText.value = text
  parsed.value = null
  parsedText.value = ''
  run()
}

/* ------------------------------------------------------------ 策略库 */
async function loadStrategies(keep?: string) {
  strategies.value = await api.strategies()
  if (keep && strategies.value.some((s) => s.id === keep)) {
    activeId.value = keep
  } else if (!strategies.value.some((s) => s.id === activeId.value)) {
    activeId.value = strategies.value[0]?.id ?? ''
  }
}

function openEditor(target: Strategy | null, from?: Partial<StrategyPayload>) {
  editing.value = target
  preset.value = from ?? null
  editorOpen.value = true
}

function editCurrent() {
  if (!current.value) return
  openEditor(current.value)
}

/** 保存为策略：把「此刻跑的东西」原样变成一套可复用的策略 */
function saveCurrent() {
  const base = result.value
  const fallbackName =
    mode.value === 'ai'
      ? parsed.value?.suggested_name || 'AI 问股策略'
      : base
        ? `${base.strategy_name} 副本`
        : '条件策略'
  const draft: Partial<StrategyPayload> = {
    name: fallbackName,
    desc: mode.value === 'ai' ? aiText.value.trim().slice(0, 40) : '',
    weights: base?.weights ?? {},
    filters: (base?.filters ?? []).map((c) => ({ field: c.field, op: c.op, value: c.value })),
    sort: base?.sort ? { by: base.sort.by, desc: base.sort.desc } : null,
    top_n: topN.value,
    max_per_industry: base?.max_per_industry ?? 2,
    exclude_st: base?.exclude_st ?? true,
  }
  openEditor(null, draft)
}

async function duplicate() {
  if (!current.value) return
  const s = current.value
  openEditor(null, {
    name: `${s.name} 副本`,
    desc: s.desc,
    weights: { ...s.weights },
    filters: s.filters.map((c) => ({ field: c.field, op: c.op, value: c.value })),
    sort: s.sort ? { by: s.sort.by, desc: s.sort.desc } : null,
    top_n: s.top_n,
    max_per_industry: s.max_per_industry,
    exclude_st: s.exclude_st,
  })
}

async function removeStrategy() {
  const s = current.value
  if (!s || s.builtin) return
  try {
    await ElMessageBox.confirm(`删除策略「${s.name}」？删除后不可恢复。`, '删除策略', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  await api.deleteStrategy(s.id)
  ElMessage.success('已删除')
  await loadStrategies()
  if (mode.value === 'factor') await run()
}

async function onSaved(saved: Strategy) {
  await loadStrategies(saved.id)
  mode.value = 'factor'
  await run()
}

/* ------------------------------------------------------------ 结果交互 */
function pick(c: Candidate) {
  selected.value = c
  if (window.innerWidth < 1200) detailOpen.value = true
}

async function makePlan(c: Candidate) {
  planLoading.value = true
  planOpen.value = true
  // 计划卡只认策略库里的 id；AI/条件模式产生的临时策略统一挂到内置多因子上
  const known = strategies.value.some((s) => s.id === result.value?.strategy_id)
  try {
    plan.value = await api.previewPlan({
      code: c.code,
      strategy: known ? (result.value?.strategy_id as string) : 'multifactor',
    })
  } finally {
    planLoading.value = false
  }
}

onMounted(async () => {
  const [list, meta, sample] = await Promise.all([api.strategies(), api.fields(), api.examples()])
  strategies.value = list
  fields.value = meta
  examples.value = sample
  activeId.value = list[0]?.id ?? ''
  if (activeId.value) await run()
})
</script>

<template>
  <div class="screener">
    <!-- 查询区 -->
    <section class="panel query">
      <div class="q-head">
        <el-radio-group v-model="mode" class="modes">
          <el-radio-button v-for="m in MODES" :key="m.key" :value="m.key">{{ m.label }}</el-radio-button>
        </el-radio-group>
        <span class="q-hint hide-mobile">{{ modeHint }}</span>
        <div class="q-actions">
          <div class="topn">
            <span class="muted">Top N</span>
            <el-input-number v-model="topN" :min="1" :max="40" :step="5" size="small" controls-position="right" />
          </div>
          <el-button type="primary" :icon="RefreshRight" :loading="loading" @click="run">运行</el-button>
        </div>
      </div>

      <!-- 多因子 -->
      <div v-if="mode === 'factor'" class="q-body">
        <el-select v-model="activeId" class="strategy-select" placeholder="选择策略" @change="run">
          <el-option v-for="s in strategies" :key="s.id" :label="s.name" :value="s.id">
            <div class="opt">
              <span class="opt-name">
                {{ s.name }}
                <span v-if="s.builtin" class="opt-tag">内置</span>
              </span>
              <span class="opt-desc">{{ s.desc || '无说明' }}</span>
            </div>
          </el-option>
        </el-select>
        <div v-if="current" class="strat-meta">
          <span>{{ Object.keys(current.weights).length }} 个因子</span>
          <span>{{ current.filters.length }} 条条件</span>
          <span>{{ current.sort ? '指定排序' : '默认排序' }}</span>
          <span>同行业 ≤ {{ current.max_per_industry }}</span>
        </div>
        <div class="q-tools">
          <el-button size="small" :icon="EditPen" :disabled="!current" @click="editCurrent">编辑</el-button>
          <el-button size="small" :icon="CopyDocument" :disabled="!current" @click="duplicate">复制</el-button>
          <el-button size="small" :icon="Delete" :disabled="!current || current.builtin" @click="removeStrategy">
            删除
          </el-button>
          <el-button size="small" type="primary" plain @click="openEditor(null)">新建策略</el-button>
        </div>
      </div>

      <!-- AI 问股 -->
      <div v-else-if="mode === 'ai'" class="q-body col">
        <div class="ai-line">
          <el-input
            v-model="aiText"
            class="ai-input"
            placeholder="用一句话描述，例如：主力净流入前10的创业板股票"
            clearable
            @keyup.enter="run"
          />
          <el-button type="primary" @click="run">解析并选股</el-button>
        </div>

        <div class="examples">
          <span class="ex-label">试试</span>
          <button v-for="e in examples" :key="e" class="ex" type="button" @click="useExample(e)">{{ e }}</button>
        </div>

        <template v-if="parsed">
          <div class="parsed">
            <div class="parsed-head">
              <span class="ph-title">识别到的条件</span>
              <span class="ph-meta num">{{ parsed.engine === 'llm' ? '模型解析' : '规则解析' }}</span>
            </div>
            <ConditionBuilder :model-value="aiFilters" :fields="fields" compact @update:model-value="onAiFilters" />
            <div class="parsed-foot">
              <span>排序 · {{ sortLabel }}</span>
              <span v-if="parsed.unresolved.length" class="warn">
                未识别：{{ parsed.unresolved.join(' / ') }}
              </span>
            </div>
          </div>
        </template>

        <div class="q-tools">
          <el-button size="small" :disabled="!result" @click="saveCurrent">存为策略</el-button>
        </div>
      </div>

      <!-- 条件筛选 -->
      <div v-else class="q-body col">
        <ConditionBuilder v-model="filterConditions" :fields="fields" />

        <div class="filter-foot">
          <span class="ff-k">排序</span>
          <el-select v-model="filterSortBy" size="small" clearable placeholder="不指定（按涨跌幅）" class="ff-select">
            <el-option v-for="f in numericFields" :key="f.key" :label="f.label" :value="f.key" />
          </el-select>
          <el-radio-group v-if="filterSortBy" v-model="filterSortDesc" size="small">
            <el-radio-button :value="true">降序</el-radio-button>
            <el-radio-button :value="false">升序</el-radio-button>
          </el-radio-group>
          <el-button size="small" :disabled="!result" @click="saveCurrent">存为策略</el-button>
        </div>
      </div>
    </section>

    <!-- 结果 -->
    <div class="body">
      <section class="panel list" v-loading="loading">
        <div class="panel-head">
          <div class="panel-title">
            候选列表
            <span class="panel-sub">{{ result?.strategy_name ?? '—' }} · {{ mode === 'factor' ? '已做行业分散' : `命中 ${result?.matched_count ?? 0} 只` }}</span>
          </div>
          <span class="chip brand">{{ result?.items.length ?? 0 }} 只</span>
        </div>

        <div class="rows">
          <div class="row-head">
            <span>#</span>
            <span>标的</span>
            <span class="hide-mobile">行业</span>
            <span>现价</span>
            <span>涨跌</span>
            <span class="ta-r">{{ scoreTitle }}</span>
            <span class="hide-mobile" />
          </div>
          <button
            v-for="c in result?.items ?? []"
            :key="c.code"
            class="row"
            :class="{ active: selected?.code === c.code }"
            @click="pick(c)"
          >
            <span class="rank num" :class="{ top: c.rank <= 3 }">{{ c.rank }}</span>
            <span class="ident">
              <span class="name">{{ c.name }}</span>
              <span class="code num">{{ c.code }}</span>
              <span v-if="c.hits?.length" class="hits">
                <span v-for="h in c.hits" :key="h.field" class="hit">
                  {{ h.label }} {{ h.value }}{{ h.unit }}
                </span>
              </span>
            </span>
            <span class="ind chip hide-mobile">{{ c.industry }}</span>
            <span class="price num">{{ c.price.toFixed(2) }}</span>
            <span class="chg num" :class="toneOf(c.change_pct)">{{ fmtPct(c.change_pct) }}</span>
            <span class="score num">{{ scoreText(c) }}</span>
            <el-icon class="arrow"><CaretRight /></el-icon>
          </button>
          <div v-if="!loading && !(result?.items.length)" class="empty-list">
            当前条件下没有标的。放宽一条条件，或换个问法。
          </div>
        </div>

        <div class="list-note">{{ result?.rank_hint || '综合分为各因子 Z 值加权和 ×100。' }}</div>
      </section>

      <section class="panel detail" :class="{ open: detailOpen }" v-if="selected">
        <div class="panel-head wrap">
          <div class="panel-title">
            {{ selected.name }}
            <span class="panel-sub num">{{ selected.code }} · {{ selected.industry }}</span>
          </div>
          <div class="head-actions">
            <el-button type="primary" size="small" @click="makePlan(selected)">生成计划卡</el-button>
            <el-button
              class="detail-close"
              size="small"
              text
              :icon="Close"
              circle
              title="关闭详情"
              @click="detailOpen = false"
            />
          </div>
        </div>

        <div class="panel-body">
          <div class="d-stats">
            <div class="ds">
              <span class="k">{{ detailScoreTitle }}</span>
              <span class="v num brand">{{ scoreText(selected) }}</span>
            </div>
            <div class="ds">
              <span class="k">现价</span>
              <span class="v num">{{ selected.price.toFixed(2) }}</span>
            </div>
            <div class="ds">
              <span class="k">ATR(14)</span>
              <span class="v num">{{ selected.atr.toFixed(2) }}</span>
            </div>
            <div class="ds">
              <span class="k">MA20</span>
              <span class="v num">{{ selected.ma20.toFixed(2) }}</span>
            </div>
          </div>

          <template v-if="selected.hits?.length">
            <div class="block-title">命中条件</div>
            <div class="hit-grid">
              <div v-for="h in selected.hits" :key="h.field" class="hit-row">
                <span class="hk">{{ h.label }}</span>
                <span class="hv num">{{ h.value }}{{ h.unit }}</span>
                <span class="ht num">
                  {{ h.op === '=' ? '要求为' : `要求 ${h.op}` }} {{ targetText(h.target) }}
                </span>
              </div>
            </div>
          </template>

          <template v-if="selected.factors.length">
            <div class="block-title">因子贡献拆解</div>
            <FactorBars :factors="selected.factors" />
          </template>

          <div class="block-title">入选理由</div>
          <div class="reasons">
            <div class="reason-col">
              <div v-for="(r, i) in selected.reason.positive" :key="i" class="reason pos">
                <span class="mark">+</span>{{ r }}
              </div>
            </div>
            <div class="reason-col">
              <div v-for="(r, i) in selected.reason.negative" :key="i" class="reason neg">
                <span class="mark">−</span>{{ r }}
              </div>
            </div>
          </div>

          <template v-if="selected.factors.length">
            <div class="block-title">因子权重</div>
            <div class="weights">
              <div v-for="f in selected.factors" :key="f.key" class="w">
                <span class="w-label">{{ f.label }}</span>
                <div class="w-track"><span class="w-fill" :style="{ width: `${f.weight * 200}%` }" /></div>
                <span class="w-val num">{{ (f.weight * 100).toFixed(0) }}%</span>
              </div>
            </div>
          </template>
        </div>
      </section>
    </div>

    <StrategyEditor
      v-model="editorOpen"
      :fields="fields"
      :editing="editing"
      :preset="preset"
      @saved="onSaved"
    />

    <el-drawer v-model="planOpen" title="交易计划卡预览" size="min(460px, 94vw)">
      <div v-loading="planLoading" class="drawer-body">
        <template v-if="plan">
          <div class="plan-head">
            <div>
              <div class="plan-name">{{ plan.name }} <span class="muted num">{{ plan.code }}</span></div>
              <div class="muted">{{ plan.strategy_name }} · {{ plan.signal_label }} · 置信度 {{ plan.confidence }}</div>
            </div>
            <div class="rr">
              <span class="rr-val num">{{ plan.rr.toFixed(2) }}</span>
              <span class="muted">赔率 R:R</span>
            </div>
          </div>

          <ZoneLadder :plan="plan" class="drawer-ladder" />

          <div class="zone-table">
            <div class="zt-row"><span>建仓区</span><b class="num">{{ plan.zones.entry.low }} – {{ plan.zones.entry.high }}</b></div>
            <div class="zt-row"><span>加仓区</span><b class="num">{{ plan.zones.add.low }} – {{ plan.zones.add.high }}</b></div>
            <div class="zt-row"><span>止损区</span><b class="num">{{ plan.zones.stop.low }} – {{ plan.zones.stop.high }}</b></div>
            <div class="zt-row"><span>目标 TP1</span><b class="num">{{ plan.zones.target[0].low }} – {{ plan.zones.target[0].high }}</b></div>
            <div class="zt-row" v-if="plan.zones.target[1]"><span>目标 TP2</span><b class="num">{{ plan.zones.target[1].low }} – {{ plan.zones.target[1].high }}</b></div>
          </div>

          <div class="block-title">分批计划</div>
          <div class="tranches">
            <div v-for="(t, i) in plan.sizing.tranches" :key="i" class="tranche">
              <span class="t-idx">第 {{ i + 1 }} 批</span>
              <span class="num">{{ t.price.toFixed(2) }}</span>
              <span class="num">{{ t.shares }} 股</span>
              <span class="muted num">{{ (t.pct * 100).toFixed(0) }}%</span>
            </div>
          </div>

          <div class="sizing">
            <span>建议股数 <b class="num">{{ plan.sizing.shares }}</b></span>
            <span>仓位 <b class="num">{{ plan.sizing.weight_pct.toFixed(1) }}%</b></span>
            <span>最大亏损 <b class="num">¥{{ plan.sizing.max_loss.toLocaleString('zh-CN') }}</b></span>
          </div>

          <div class="block-title">失效条件</div>
          <ul class="invalid">
            <li v-for="(v, i) in plan.invalidation" :key="i">{{ v }}</li>
          </ul>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.screener {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ------------------------------------------------------------ 查询区 */
.query {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 16px 14px;
}

.q-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.modes :deep(.el-radio-button__inner) {
  padding: 7px 16px;
  font-size: 12.5px;
}

.q-hint {
  font-size: 11.5px;
  color: var(--af-ink-4);
  flex: 1;
  min-width: 0;
}

.q-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
}

.topn {
  display: flex;
  align-items: center;
  gap: 8px;
}

.topn :deep(.el-input-number) {
  width: 84px;
}

.q-body {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding-top: 12px;
  border-top: 1px dashed var(--af-border);
}

.q-body.col {
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
}

.strategy-select {
  width: 224px;
}

.opt {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
  padding: 3px 0;
}

.opt-name {
  display: flex;
  align-items: center;
  gap: 6px;
}

.opt-tag {
  padding: 0 5px;
  border-radius: 3px;
  background: var(--af-surface-3);
  color: var(--af-ink-3);
  font-size: 9.5px;
}

.opt-desc {
  font-size: 11px;
  color: var(--af-ink-3);
}

.strat-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--af-ink-3);
  flex-wrap: wrap;
}

.q-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  flex-wrap: wrap;
}

.q-tools :deep(.el-button + .el-button) {
  margin-left: 0;
}

/* ------------------------------------------------------------ AI */
.ai-line {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ai-input {
  flex: 1;
  min-width: 0;
}

.examples {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.ex-label {
  font-size: 11px;
  color: var(--af-ink-4);
}

.ex {
  padding: 3px 9px;
  border: 1px solid var(--af-border);
  border-radius: 999px;
  background: var(--af-surface);
  color: var(--af-ink-2);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.14s ease, color 0.14s ease;
}

.ex:hover {
  border-color: var(--af-brand);
  color: var(--af-brand);
}

.parsed {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
}

.parsed-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.ph-title {
  font-size: 11.5px;
  font-weight: 650;
  color: var(--af-ink-2);
}

.ph-meta {
  font-size: 10.5px;
  color: var(--af-ink-4);
}

.parsed-foot {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--af-ink-3);
  flex-wrap: wrap;
}

.warn {
  color: var(--af-warn);
}

.filter-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.ff-k {
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.ff-select {
  width: 180px;
}

/* ------------------------------------------------------------ 结果 */
.body {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.rows {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 10px 8px 4px;
}

.row-head {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) 64px 62px 66px 52px 16px;
  align-items: center;
  gap: 8px;
  padding: 0 8px 6px;
  font-size: 10.5px;
  letter-spacing: 0.02em;
  color: var(--af-ink-4);
  border-bottom: 1px solid var(--af-border);
  margin-bottom: 4px;
}

.row-head > :nth-child(4),
.row-head > :nth-child(5),
.row-head > :nth-child(6) {
  text-align: right;
}

.ta-r {
  text-align: right;
}

.row {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) 64px 62px 66px 52px 16px;
  align-items: center;
  gap: 8px;
  padding: 9px 8px;
  border: 0;
  background: transparent;
  border-radius: 9px;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  transition: background 0.14s ease;
}

.row:hover {
  background: var(--af-surface-2);
}

.row.active {
  background: var(--af-brand-soft);
}

.rank {
  font-size: 12px;
  font-weight: 650;
  color: var(--af-ink-3);
  text-align: center;
}

.rank.top {
  color: var(--af-brand);
}

.ident {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.name {
  font-size: 13px;
  font-weight: 600;
}

.code {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.hits {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 2px;
}

.hit {
  padding: 0 5px;
  border-radius: 3px;
  background: var(--af-surface-3);
  color: var(--af-ink-2);
  font-size: 9.5px;
  line-height: 15px;
  white-space: nowrap;
}

.ind {
  justify-self: start;
  font-size: 10.5px;
  height: 20px;
}

.price {
  font-size: 12.5px;
  text-align: right;
}

.chg {
  font-size: 11.5px;
  text-align: right;
}

.score {
  font-size: 13px;
  font-weight: 700;
  text-align: right;
  color: var(--af-brand);
}

.arrow {
  color: var(--af-ink-3);
}

.empty-list {
  padding: 28px 12px;
  text-align: center;
  font-size: 11.5px;
  color: var(--af-ink-4);
}

.list-note {
  padding: 6px 16px 14px;
  font-size: 11px;
  color: var(--af-ink-3);
  line-height: 1.6;
}

/* 详情头按钮组（注意不要复用列表行的 .row，那是 7 列 grid） */
.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

.head-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.d-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  margin-bottom: 4px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--af-border);
}

.ds {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 0 12px;
  border-left: 1px solid var(--af-border);
  min-width: 0;
}

.ds:first-child {
  padding-left: 0;
  border-left: 0;
}

.ds .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.ds .v {
  font-size: 14.5px;
  font-weight: 650;
}

.brand {
  color: var(--af-brand);
}

.block-title {
  font-size: 12px;
  font-weight: 650;
  color: var(--af-ink-2);
  margin: 16px 0 10px;
}

.hit-grid {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.hit-row {
  display: grid;
  grid-template-columns: 96px 84px minmax(0, 1fr);
  align-items: baseline;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 7px;
  background: var(--af-surface-2);
  font-size: 11.5px;
}

.hit-row .hk {
  color: var(--af-ink-2);
}

.hit-row .hv {
  font-weight: 650;
  color: var(--af-brand);
}

.hit-row .ht {
  color: var(--af-ink-4);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.reasons {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.reason-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.reason {
  font-size: 11.5px;
  line-height: 1.5;
  display: flex;
  gap: 5px;
  padding: 7px 9px;
  border-radius: 7px;
  background: var(--af-surface-2);
}

.reason.pos {
  color: var(--af-up);
}

.reason.neg {
  color: var(--af-down);
}

.reason .mark {
  font-weight: 700;
}

.weights {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.w {
  display: grid;
  grid-template-columns: 92px 1fr 38px;
  align-items: center;
  gap: 10px;
  font-size: 11.5px;
}

.w-label {
  color: var(--af-ink-2);
}

.w-track {
  height: 5px;
  border-radius: 999px;
  background: var(--af-bg-deep);
  overflow: hidden;
}

.w-fill {
  display: block;
  height: 100%;
  max-width: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #9db4ef, var(--af-brand));
}

.w-val {
  text-align: right;
  color: var(--af-ink-3);
}

/* ------------------------------------------------------------ 计划卡 */
.drawer-body {
  display: flex;
  flex-direction: column;
}

.plan-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.plan-name {
  font-size: 16px;
  font-weight: 700;
  margin-bottom: 4px;
}

.rr {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.rr-val {
  font-size: 20px;
  font-weight: 700;
  color: var(--af-brand);
}

.drawer-ladder {
  margin: 22px 0 18px;
}

.zone-table {
  display: flex;
  flex-direction: column;
  gap: 1px;
  border: 1px solid var(--af-border);
  border-radius: 10px;
  overflow: hidden;
}

.zt-row {
  display: flex;
  justify-content: space-between;
  padding: 9px 12px;
  font-size: 12.5px;
  background: var(--af-surface);
}

.zt-row:nth-child(odd) {
  background: var(--af-surface-2);
}

.zt-row span {
  color: var(--af-ink-2);
}

.tranches {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tranche {
  display: grid;
  grid-template-columns: 60px 1fr 1fr auto;
  gap: 10px;
  font-size: 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--af-surface-2);
}

.t-idx {
  color: var(--af-ink-3);
}

.sizing {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px dashed var(--af-border);
  font-size: 11.5px;
  color: var(--af-ink-2);
}

.sizing b {
  display: block;
  font-size: 14px;
  color: var(--af-ink);
  margin-top: 3px;
}

.invalid {
  margin: 0;
  padding-left: 16px;
  font-size: 11.5px;
  color: var(--af-ink-2);
  line-height: 1.8;
}

@media (max-width: 1199px) {
  .body {
    grid-template-columns: minmax(0, 1fr);
  }
  .detail {
    position: fixed;
    inset: 0;
    z-index: 90;
    border-radius: 0;
    overflow-y: auto;
    transform: translateY(100%);
    transition: transform 0.24s cubic-bezier(0.32, 0.72, 0, 1);
    visibility: hidden;
    box-shadow: 0 -10px 40px rgba(16, 24, 40, 0.16);
  }
  .detail.open {
    transform: none;
    visibility: visible;
  }
}

@media (max-width: 767px) {
  .q-actions {
    width: 100%;
    justify-content: space-between;
    margin-left: 0;
  }
  .strategy-select {
    width: 100%;
  }
  .q-tools {
    width: 100%;
    margin-left: 0;
  }
  .ai-line {
    flex-direction: column;
    align-items: stretch;
  }
  .ff-select {
    flex: 1;
    min-width: 120px;
  }
  .row {
    grid-template-columns: 22px minmax(0, 1fr) 54px 58px 46px;
    gap: 6px;
  }
  .row-head {
    grid-template-columns: 22px minmax(0, 1fr) 54px 58px 46px;
    gap: 6px;
  }
  .row .arrow {
    display: none;
  }
  .d-stats {
    grid-template-columns: repeat(2, 1fr);
  }
  .reasons {
    grid-template-columns: 1fr;
  }
  .hit-row {
    grid-template-columns: 80px 70px minmax(0, 1fr);
  }
}
</style>
