<script setup lang="ts">
/**
 * 条件构造器：AI 问股、条件筛选、策略编辑器三处共用同一份「条件」模型。
 *
 * 一条条件就是 `{field, op, value}`。这里只负责把它拼出来，
 * 中文标签交给后端 `_label()` 生成——前端自己拼一份必然会和后端说法不一致，
 * 而「界面写 A、结果写 B」是这类工具最容易失分的地方。
 * 因此：本地新增的条件先显示临时描述（describe），运行后由后端标签覆盖。
 */
import { computed, ref, watch } from 'vue'
import { Close } from '@element-plus/icons-vue'
import type { Condition, FieldMeta } from '@/types'

const props = withDefaults(
  defineProps<{
    modelValue: Condition[]
    fields: FieldMeta[]
    /** 紧凑模式：不显示添加表单，只做展示与删除（AI 解析结果用） */
    compact?: boolean
  }>(),
  { compact: false },
)

const emit = defineEmits<{ (e: 'update:modelValue', value: Condition[]): void }>()

const OP_TEXT: Record<string, string> = { '>': '>', '>=': '≥', '<': '<', '<=': '≤', '=': '=', '!=': '≠' }

/* 组节点：一条条件里套一组条件，value 是子条件数组。只有后端会产出，
   构造器不提供新增入口——但复制、另存会剥掉 label，本地得自己摊开。 */
const GROUP_JOIN: Record<string, string> = { any: ' 或 ', all: ' 且 ' }

/* 添加表单 */
const fieldKey = ref('')
const op = ref('>=')
const num = ref<number>(10)
const range = ref<[number, number]>([10, 20])
const text = ref('')
const flag = ref(true)

const field = computed(() => props.fields.find((f) => f.key === fieldKey.value) ?? null)
const ops = computed(() => field.value?.ops ?? [])

/* 分组下拉：字段有 34 个，平铺一列找起来太慢 */
const grouped = computed(() => {
  const map = new Map<string, FieldMeta[]>()
  for (const f of props.fields) {
    const list = map.get(f.group) ?? []
    list.push(f)
    map.set(f.group, list)
  }
  return [...map.entries()].map(([group, items]) => ({ group, items }))
})

watch(
  field,
  (f) => {
    if (!f) return
    op.value = f.ops[0] ?? '='
    if (f.kind === 'cat') text.value = f.options[0] ?? ''
    if (f.kind === 'bool') flag.value = true
  },
  { immediate: true },
)

const canAdd = computed(() => {
  if (!field.value) return false
  if (field.value.kind === 'num') {
    return op.value === 'between'
      ? Number.isFinite(range.value[0]) && Number.isFinite(range.value[1])
      : Number.isFinite(num.value)
  }
  if (field.value.kind === 'cat') return !!text.value
  return true
})

function describe(c: Condition): string {
  const join = GROUP_JOIN[c.field]
  if (join) return (c.value as Condition[]).map(describe).join(join)
  const meta = props.fields.find((f) => f.key === c.field)
  const label = meta?.label ?? c.field
  const unit = meta?.unit ?? ''
  if (meta?.kind === 'bool') return `${c.value ? '' : '非'}${label}`
  if (Array.isArray(c.value)) return `${label} ${c.value[0]}–${c.value[1]}${unit}`
  return `${label} ${OP_TEXT[c.op] ?? c.op} ${c.value}${unit}`
}

function add() {
  if (!canAdd.value || !field.value) return
  const f = field.value
  const value =
    f.kind === 'num'
      ? op.value === 'between'
        ? [...range.value]
        : Number(num.value)
      : f.kind === 'cat'
        ? text.value
        : flag.value
  const next: Condition[] = [
    ...props.modelValue,
    { field: f.key, op: f.kind === 'num' ? op.value : f.kind === 'cat' ? op.value : '=', value, label: '' },
  ]
  emit('update:modelValue', next)
}

function remove(index: number) {
  const next = props.modelValue.filter((_, i) => i !== index)
  emit('update:modelValue', next)
}
</script>

<template>
  <div class="builder">
    <div v-if="!compact" class="add-row">
      <el-select v-model="fieldKey" class="f-field" placeholder="选择指标" filterable size="small">
        <el-option-group v-for="g in grouped" :key="g.group" :label="g.group">
          <el-option v-for="f in g.items" :key="f.key" :label="f.label" :value="f.key" />
        </el-option-group>
      </el-select>

      <el-select v-if="field && field.kind !== 'bool'" v-model="op" class="f-op" size="small">
        <el-option v-for="o in ops" :key="o" :label="OP_TEXT[o] ?? o" :value="o" />
      </el-select>

      <template v-if="field">
        <template v-if="field.kind === 'num'">
          <div v-if="op === 'between'" class="f-range">
            <el-input-number v-model="range[0]" :controls="false" size="small" class="f-num" />
            <span class="dash">–</span>
            <el-input-number v-model="range[1]" :controls="false" size="small" class="f-num" />
          </div>
          <el-input-number v-else v-model="num" :controls="false" size="small" class="f-num" />
          <span class="unit">{{ field.unit }}</span>
        </template>

        <el-select v-else-if="field.kind === 'cat'" v-model="text" size="small" class="f-text">
          <el-option v-for="o in field.options" :key="o" :label="o" :value="o" />
        </el-select>

        <el-switch v-else v-model="flag" size="small" active-text="是" inactive-text="否" />
      </template>

      <el-button size="small" :disabled="!canAdd" @click="add">添加</el-button>
    </div>

    <div class="chips">
      <span v-for="(c, i) in modelValue" :key="`${c.field}-${i}`" class="chip-cond">
        <span class="ct">{{ c.label || describe(c) }}</span>
        <button class="cx" type="button" title="移除该条件" @click="remove(i)">
          <el-icon><Close /></el-icon>
        </button>
      </span>
      <span v-if="!modelValue.length" class="empty">暂无条件{{ compact ? '' : '，全部标的都参与打分' }}</span>
    </div>
  </div>
</template>

<style scoped>
.builder {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.add-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.f-field {
  width: 150px;
}

.f-op {
  width: 74px;
}

.f-num {
  width: 92px;
}

.f-range {
  display: flex;
  align-items: center;
  gap: 4px;
}

.f-range .f-num {
  width: 74px;
}

.dash {
  color: var(--af-ink-3);
}

.f-text {
  width: 132px;
}

.unit {
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-height: 26px;
}

.chip-cond {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px 3px 3px 9px;
  border-radius: 6px;
  background: var(--af-brand-soft);
  color: var(--af-brand);
  font-size: 11.5px;
  font-weight: 550;
  line-height: 1.4;
}

.ct {
  white-space: nowrap;
}

.cx {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  opacity: 0.55;
  font-size: 11px;
}

.cx:hover {
  opacity: 1;
  background: var(--af-brand-soft);
}

.empty {
  font-size: 11.5px;
  color: var(--af-ink-4);
}

@media (max-width: 767px) {
  .f-field,
  .f-text {
    flex: 1;
    min-width: 120px;
    width: auto;
  }
}
</style>
