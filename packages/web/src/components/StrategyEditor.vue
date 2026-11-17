<script setup lang="ts">
/**
 * 策略编辑器：把「完整可调」落到一个抽屉里。
 *
 * 三块分别对应执行层的三种模式：
 * - 只设权重        → factor（多因子打分排序）
 * - 只设条件        → filter（命中即入选，按涨跌幅兜底排序）
 * - 权重 + 条件     → hybrid（先卡条件，再在命中的池子里打分）
 * 界面上不暴露 mode 这个词，而是用「权重为空时按条件筛选」这样的提示说明后果，
 * 因为 mode 是实现的形状，用户关心的是「我这么设会得到什么」。
 */
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import type { Condition, FieldMeta, Strategy, StrategyPayload } from '@/types'
import ConditionBuilder from '@/components/ConditionBuilder.vue'

const props = defineProps<{
  modelValue: boolean
  fields: FieldMeta[]
  /** 传入表示编辑既有策略，null 表示新建 */
  editing: Strategy | null
  /** 新建时的预填内容（来自 AI 解析或条件筛选的当前状态） */
  preset?: Partial<StrategyPayload> | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'saved', strategy: Strategy): void
}>()

const store = useAppStore()
const saving = ref(false)

const form = reactive({
  name: '',
  desc: '',
  weights: {} as Record<string, number>,
  filters: [] as Condition[],
  sortBy: '',
  sortDesc: true,
  top_n: 10,
  max_per_industry: 2,
  exclude_st: true,
})

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const isBuiltin = computed(() => !!props.editing?.builtin)

const weightable = computed(() => props.fields.filter((f) => f.weightable))
const numeric = computed(() => props.fields.filter((f) => f.kind === 'num'))
const labelOf = (key: string) => props.fields.find((f) => f.key === key)?.label ?? key

/** 已选因子：保持权重表里的顺序，删掉的就自然消失 */
const weightRows = computed(() =>
  Object.keys(form.weights).map((key) => ({
    key,
    label: labelOf(key),
    value: form.weights[key],
  })),
)

const addableWeights = computed(() => weightable.value.filter((f) => !(f.key in form.weights)))

const modeHint = computed(() => {
  const hasW = Object.keys(form.weights).length > 0
  const hasF = form.filters.length > 0
  if (hasW && hasF) return { text: '混合模式：先按条件过滤，再在结果内多因子打分排序', tone: 'brand' }
  if (hasW) return { text: '多因子模式：全池按权重打分，条件为空', tone: 'brand' }
  if (hasF) return { text: '条件模式：命中条件即入选，按涨跌幅兜底排序', tone: 'brand' }
  return { text: '还没设置权重和条件，运行后会得到一份按涨跌幅排序的清单', tone: 'warn' }
})

function reset() {
  const base = props.editing
  const preset = props.preset
  form.name = base?.name ?? preset?.name ?? ''
  form.desc = base?.desc ?? preset?.desc ?? ''
  form.weights = { ...(base?.weights ?? preset?.weights ?? {}) }
  form.filters = ((base?.filters ?? preset?.filters ?? []) as Condition[]).map((f) => ({
    ...f,
    label: f.label ?? '',
  }))
  form.sortBy = base?.sort?.by ?? preset?.sort?.by ?? ''
  form.sortDesc = base?.sort?.desc ?? preset?.sort?.desc ?? true
  form.top_n = base?.top_n ?? preset?.top_n ?? 10
  form.max_per_industry = base?.max_per_industry ?? preset?.max_per_industry ?? 2
  form.exclude_st = base?.exclude_st ?? preset?.exclude_st ?? true
}

watch(visible, (open) => {
  if (open) reset()
})

function addWeight(key: string) {
  if (!key) return
  form.weights = { ...form.weights, [key]: 1 }
}

function dropWeight(key: string) {
  const next = { ...form.weights }
  delete next[key]
  form.weights = next
}

function setWeight(key: string, value: number | number[]) {
  const num = Array.isArray(value) ? value[0] : value
  form.weights = { ...form.weights, [key]: num }
}

async function save() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('请先给策略起个名字')
    return
  }
  if (!Object.keys(form.weights).length && !form.filters.length) {
    ElMessage.warning('至少设置一个因子权重或一条筛选条件')
    return
  }
  const payload: StrategyPayload = {
    name,
    desc: form.desc.trim(),
    weights: { ...form.weights },
    filters: form.filters.map((f) => ({ field: f.field, op: f.op, value: f.value })),
    sort: form.sortBy ? { by: form.sortBy, desc: form.sortDesc } : null,
    top_n: form.top_n,
    max_per_industry: form.max_per_industry,
    exclude_st: form.exclude_st,
  }
  saving.value = true
  try {
    const saved = props.editing && !isBuiltin.value
      ? await api.updateStrategy(props.editing.id, payload)
      : await api.createStrategy(payload)
    ElMessage.success(isBuiltin.value ? '已另存为新策略' : props.editing ? '策略已更新' : '策略已保存')
    emit('saved', saved)
    visible.value = false
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-drawer v-model="visible" :size="store.isMobile ? '100%' : 560" class="strategy-editor">
    <template #header>
      <div class="ed-head">
        <span class="ed-title">{{ editing ? (isBuiltin ? '另存为副本' : '编辑策略') : '新建策略' }}</span>
        <span class="ed-sub">权重 · 条件 · 排序 · 分散规则</span>
      </div>
    </template>

    <div class="body">
      <!-- 基本信息 -->
      <section class="block">
        <label class="field">
          <span class="k">策略名称</span>
          <el-input v-model="form.name" maxlength="20" show-word-limit placeholder="例如：低估值高股息" />
        </label>
        <label class="field">
          <span class="k">一句话说明</span>
          <el-input v-model="form.desc" maxlength="40" placeholder="这套策略想抓什么，写给自己看" />
        </label>
        <p class="mode-hint" :class="modeHint.tone">{{ modeHint.text }}</p>
        <p v-if="isBuiltin" class="builtin-tip">内置策略不可直接修改，保存会生成一份可编辑的副本。</p>
      </section>

      <!-- 因子权重 -->
      <section class="block">
        <header class="block-head">
          <span class="block-title">因子权重</span>
          <el-select
            class="add-factor"
            size="small"
            placeholder="添加因子"
            :model-value="''"
            @change="addWeight"
          >
            <el-option v-for="f in addableWeights" :key="f.key" :label="f.label" :value="f.key">
              <span class="opt-row">
                <span>{{ f.label }}</span>
                <span class="opt-hint">{{ f.direction_hint }}</span>
              </span>
            </el-option>
          </el-select>
        </header>

        <div v-if="!weightRows.length" class="empty">
          未选因子。只设条件时，命中的标的按涨跌幅排序。
        </div>

        <div v-for="row in weightRows" :key="row.key" class="wrow">
          <span class="wname">
            {{ row.label }}
            <span class="wdir">{{ weightable.find((f) => f.key === row.key)?.direction_hint }}</span>
          </span>
          <el-slider
            :model-value="row.value"
            :min="0"
            :max="3"
            :step="0.5"
            :show-tooltip="false"
            size="small"
            class="wslider"
            @update:model-value="(v: number | number[]) => setWeight(row.key, v)"
          />
          <span class="wval num">{{ row.value.toFixed(1) }}</span>
          <button class="wdel" type="button" title="移除因子" @click="dropWeight(row.key)">
            <el-icon><Close /></el-icon>
          </button>
        </div>
      </section>

      <!-- 筛选条件 -->
      <section class="block">
        <header class="block-head">
          <span class="block-title">筛选条件</span>
          <span class="block-sub">命中全部条件才入选</span>
        </header>
        <ConditionBuilder v-model="form.filters" :fields="fields" />
      </section>

      <!-- 排序与约束 -->
      <section class="block">
        <header class="block-head">
          <span class="block-title">排序与约束</span>
        </header>
        <div class="grid2">
          <label class="field">
            <span class="k">排序字段</span>
            <el-select v-model="form.sortBy" size="small" clearable placeholder="不指定">
              <el-option v-for="f in numeric" :key="f.key" :label="f.label" :value="f.key" />
            </el-select>
          </label>
          <label class="field">
            <span class="k">方向</span>
            <el-radio-group v-model="form.sortDesc" size="small">
              <el-radio-button :value="true">降序</el-radio-button>
              <el-radio-button :value="false">升序</el-radio-button>
            </el-radio-group>
          </label>
          <label class="field">
            <span class="k">入选数量</span>
            <el-input-number v-model="form.top_n" :min="1" :max="40" size="small" :controls="false" />
          </label>
          <label class="field">
            <span class="k">同行业上限</span>
            <el-input-number v-model="form.max_per_industry" :min="1" :max="10" size="small" :controls="false" />
          </label>
        </div>
        <label class="switch-row">
          <el-switch v-model="form.exclude_st" size="small" />
          <span>排除 ST（条件里显式提到 ST 时不受此限制）</span>
        </label>
      </section>
    </div>

    <template #footer>
      <div class="foot">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">
          {{ isBuiltin ? '另存为副本' : '保存策略' }}
        </el-button>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.ed-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ed-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--af-ink);
}

.ed-sub {
  font-size: 11px;
  color: var(--af-ink-3);
}

.body {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding-bottom: 8px;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 10px;
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

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field .k {
  font-size: 11px;
  color: var(--af-ink-3);
}

.mode-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  padding: 7px 10px;
  border-radius: 6px;
  background: var(--af-surface-2);
}

.mode-hint.brand {
  color: var(--af-brand);
}

.mode-hint.warn {
  color: var(--af-warn);
}

.builtin-tip {
  margin: 0;
  font-size: 11px;
  color: var(--af-warn);
}

.add-factor {
  width: 132px;
}

.opt-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.opt-hint {
  font-size: 10.5px;
  color: var(--af-ink-4);
}

.empty {
  font-size: 11.5px;
  color: var(--af-ink-4);
  line-height: 1.6;
}

.wrow {
  display: grid;
  grid-template-columns: 104px minmax(0, 1fr) 32px 20px;
  align-items: center;
  gap: 10px;
}

.wname {
  display: flex;
  flex-direction: column;
  font-size: 11.5px;
  color: var(--af-ink-2);
  line-height: 1.3;
  min-width: 0;
}

.wdir {
  font-size: 9.5px;
  color: var(--af-ink-4);
}

.wslider {
  min-width: 0;
}

.wval {
  font-size: 11.5px;
  color: var(--af-ink-2);
  text-align: right;
}

.wdel {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--af-ink-4);
  cursor: pointer;
  font-size: 11px;
}

.wdel:hover {
  background: var(--af-surface-3);
  color: var(--af-ink-2);
}

.grid2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
}

.switch-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  color: var(--af-ink-2);
}

.foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 767px) {
  .wrow {
    grid-template-columns: 84px minmax(0, 1fr) 28px 18px;
    gap: 8px;
  }
  .grid2 {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
