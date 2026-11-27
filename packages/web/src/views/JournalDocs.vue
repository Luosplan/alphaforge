<script setup lang="ts">
/**
 * 经验库：md 原件 + 《经验》总结。
 *
 * 为什么原文直接显示，而不是转成结构化卡片
 * --------------------------------------
 * 这份文档的读者有两个：人，和下一步选股时喂给模型的那段 prompt。转成卡片
 * 之后两边看到的东西就不一样了——页面上读到的「仓位已满 92 只」和模型读到的
 * 可能是另一版口径。显示 md 原件，人看到的就是模型看到的，出了问题能对上账。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import type { JournalIndex } from '@/types'
import MarkdownDoc from '@/components/MarkdownDoc.vue'
import { fmtPct } from '@/utils/format'

const loading = ref(true)
const busy = ref('')
const index = ref<JournalIndex | null>(null)
const days = ref<JournalIndex['items']>([])
const active = ref<string>('lessons')
const doc = ref('')

const stats = computed(() => index.value?.stats)

const rate = computed(() => {
  const v = stats.value?.win_rate
  return v == null ? '—' : `${v.toFixed(0)}%`
})

const expectancy = computed(() => stats.value?.expectancy)

async function loadIndex() {
  loading.value = true
  try {
    const got = await api.journal()
    index.value = got
    days.value = got.items ?? []
    if (active.value !== 'lessons' && !days.value.some((d) => d.date === active.value)) {
      active.value = 'lessons'
    }
    await loadDoc()
  } finally {
    loading.value = false
  }
}

async function loadDoc() {
  if (active.value === 'lessons') {
    doc.value = index.value?.lessons || ''
    return
  }
  const got = await api.journalDay(active.value)
  doc.value = got.markdown
}

async function pick(date: string) {
  active.value = date
  await loadDoc()
}

async function rebuild() {
  busy.value = 'rebuild'
  try {
    const got = await api.journalRebuild()
    ElMessage.success(`${got.date} 的复盘已重写`)
    await loadIndex()
  } finally {
    busy.value = ''
  }
}

async function rebuildLessons() {
  busy.value = 'lessons'
  try {
    const got = await api.journalLessons()
    ElMessage.success('经验已重写')
    if (index.value) index.value.lessons = got.markdown
    if (active.value === 'lessons') doc.value = got.markdown
  } finally {
    busy.value = ''
  }
}

onMounted(loadIndex)
</script>

<template>
  <div class="docs" v-loading="loading">
    <section class="d-head">
      <div class="d-title">
        经验库
        <span class="muted">每日 md 原件，选股前会连同《经验》一起交给模型复核</span>
      </div>
      <div class="d-acts">
        <span class="chip flat">平仓 {{ stats?.count ?? 0 }} 笔</span>
        <span class="chip flat">胜率 {{ rate }}</span>
        <span v-if="expectancy != null" class="chip flat num">
          期望 {{ fmtPct(expectancy) }}
        </span>
        <button type="button" class="btn" :disabled="!!busy" @click="rebuild">
          {{ busy === 'rebuild' ? '重写中…' : '重写今日复盘' }}
        </button>
        <button type="button" class="btn ghost" :disabled="!!busy" @click="rebuildLessons">
          {{ busy === 'lessons' ? '重写中…' : '重写经验' }}
        </button>
      </div>
    </section>

    <div class="d-grid">
      <aside class="d-list">
        <button
          type="button"
          class="d-item"
          :class="{ active: active === 'lessons' }"
          @click="pick('lessons')"
        >
          <span class="d-date">经验</span>
          <span class="d-fact">滚动总结，模型复核时的主线</span>
        </button>
        <button
          v-for="d in days"
          :key="d.date"
          type="button"
          class="d-item"
          :class="{ active: active === d.date }"
          @click="pick(d.date)"
        >
          <span class="d-date num">{{ d.date }}</span>
          <span v-for="(f, i) in d.facts.slice(0, 2)" :key="i" class="d-fact">{{ f }}</span>
        </button>
        <div v-if="!days.length" class="d-empty">
          还没有复盘。<br>
          收盘任务（15:05）跑完会自动写一篇，也可以点右上角「重写今日复盘」。
        </div>
      </aside>

      <article class="d-body">
        <MarkdownDoc v-if="doc" :text="doc" />
        <div v-else class="d-empty-body">
          这一页还空着。经验库第一次生成是在收盘任务跑完之后；模型没接入时，
          每日复盘照样会写，只是《经验》那一节会退化成统计表。
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.docs {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  flex: 1;
}

.d-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius);
  background: var(--af-surface);
}

.d-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-weight: 650;
  color: var(--af-ink);
  font-size: 13px;
}

.d-title .muted {
  font-weight: 400;
  font-size: 11.5px;
  color: var(--af-ink-2);
}

.d-acts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.btn {
  padding: 5px 11px;
  border: 1px solid var(--af-brand);
  border-radius: var(--af-radius-sm);
  background: var(--af-brand);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}

.btn.ghost {
  background: transparent;
  color: var(--af-brand);
}

.btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.d-grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 268px minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
}

.d-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  min-height: 0;
  padding: 8px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius);
  background: var(--af-surface);
}

.d-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 10px;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  background: transparent;
  text-align: left;
  cursor: pointer;
  font-size: 12px;
  color: var(--af-ink-2);
}

.d-item:hover {
  background: var(--af-surface-2);
}

.d-item.active {
  border-color: var(--af-brand);
  background: var(--af-brand-soft);
}

.d-date {
  font-weight: 600;
  color: var(--af-ink);
}

.d-fact {
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-ink-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.d-fact :deep(strong) {
  color: var(--af-ink);
}

.d-empty,
.d-empty-body {
  padding: 14px;
  font-size: 12px;
  line-height: 1.8;
  color: var(--af-ink-2);
}

.d-body {
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius);
  background: var(--af-surface);
}

@media (max-width: 900px) {
  .d-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .d-list {
    max-height: 180px;
  }
}
</style>
