<script setup lang="ts">
/**
 * 极简 markdown 渲染：只覆盖复盘 md 实际会用到的语法。
 *
 * 为什么不引 marked / markdown-it
 * ------------------------------
 * 这两个包加起来 40KB+，而经验库的 md 是**我们自己写**的，语法集合是封闭的
 * （标题、列表、表格、引用、加粗、行内代码）。为六种语法引一个通用解析器，
 * 换来的是一堆用不到的扩展和一条新的供应链依赖。
 *
 * 不做 v-html 直通：文本里先转义再套标签，日期、股票名里的 `<` `>` 不会
 * 被当成标签吃掉。这一层是渲染，不是消毒——md 来源可信，转义只为不出错。
 */
import { computed } from 'vue'

const props = defineProps<{ text: string }>()

type Block =
  | { kind: 'h'; level: number; text: string }
  | { kind: 'p'; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'quote'; text: string }
  | { kind: 'hr' }
  | { kind: 'table'; head: string[]; rows: string[][] }

function escape(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/** 行内语法：加粗与行内代码。顺序是先转义再套标签，否则标签会被转义掉。 */
function inline(text: string): string {
  return escape(text)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

function cells(line: string): string[] {
  return line.replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
}

function parse(text: string): Block[] {
  const out: Block[] = []
  const lines = text.split(/\r?\n/)
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i += 1
      continue
    }
    const head = /^(#{1,4})\s+(.*)$/.exec(line)
    if (head) {
      out.push({ kind: 'h', level: head[1].length, text: head[2] })
      i += 1
      continue
    }
    if (/^---+$/.test(line.trim())) {
      out.push({ kind: 'hr' })
      i += 1
      continue
    }
    if (line.startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].startsWith('|')) {
        rows.push(cells(lines[i]))
        i += 1
      }
      // 第二行是 |---|---| 分隔符，不渲染，但它决定了后面每行都是数据行
      const body = rows.slice(1).filter((r) => !r.every((c) => /^:?-{2,}:?$/.test(c)))
      out.push({ kind: 'table', head: rows[0] ?? [], rows: body })
      continue
    }
    if (/^\s*-\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*-\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*-\s+/, ''))
        i += 1
      }
      out.push({ kind: 'ul', items })
      continue
    }
    if (line.startsWith('>')) {
      const parts: string[] = []
      while (i < lines.length && lines[i].startsWith('>')) {
        parts.push(lines[i].replace(/^>\s?/, ''))
        i += 1
      }
      out.push({ kind: 'quote', text: parts.join(' ') })
      continue
    }
    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !/^[#>|-]/.test(lines[i].trim())) {
      para.push(lines[i])
      i += 1
    }
    if (para.length) out.push({ kind: 'p', text: para.join(' ') })
  }
  return out
}

const blocks = computed(() => parse(props.text))
const html = (text: string): string => inline(text)
</script>

<template>
  <div class="md">
    <template v-for="(b, i) in blocks" :key="i">
      <component
        :is="`h${Math.min(b.level + 1, 5)}`"
        v-if="b.kind === 'h'"
        class="md-h"
        :class="`lv${b.level}`"
        v-html="html(b.text)"
      />
      <hr v-else-if="b.kind === 'hr'" class="md-hr">
      <blockquote v-else-if="b.kind === 'quote'" class="md-quote" v-html="html(b.text)" />
      <ul v-else-if="b.kind === 'ul'" class="md-ul">
        <li v-for="(it, k) in b.items" :key="k" v-html="html(it)" />
      </ul>
      <div v-else-if="b.kind === 'table'" class="md-table-wrap">
        <table class="md-table">
          <thead>
            <tr>
              <th v-for="(h, k) in b.head" :key="k" v-html="html(h)" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, k) in b.rows" :key="k">
              <td v-for="(c, j) in row" :key="j" v-html="html(c)" />
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="md-p" v-html="html(b.text)" />
    </template>
  </div>
</template>

<style scoped>
.md {
  font-size: 13px;
  line-height: 1.75;
  color: var(--af-ink-2);
}

.md-h {
  margin: 18px 0 8px;
  color: var(--af-ink);
  font-weight: 650;
}

.md-h.lv1 {
  margin-top: 0;
  font-size: 17px;
}

.md-h.lv2 {
  font-size: 14.5px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--af-border);
}

.md-h.lv3,
.md-h.lv4 {
  font-size: 13px;
}

.md-p {
  margin: 6px 0;
}

.md-ul {
  margin: 6px 0;
  padding-left: 18px;
}

.md-ul li {
  margin: 3px 0;
}

.md-quote {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid var(--af-brand);
  background: var(--af-surface-2);
  border-radius: 0 var(--af-radius-sm) var(--af-radius-sm) 0;
  color: var(--af-ink-2);
}

.md-hr {
  margin: 16px 0;
  border: 0;
  border-top: 1px solid var(--af-border);
}

.md-table-wrap {
  margin: 8px 0;
  overflow-x: auto;
}

.md-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.md-table th {
  text-align: left;
  font-weight: 600;
  color: var(--af-ink);
  background: var(--af-surface-2);
  white-space: nowrap;
}

.md-table th,
.md-table td {
  padding: 6px 9px;
  border-bottom: 1px solid var(--af-border);
  vertical-align: top;
}

.md-table tbody tr:hover td {
  background: var(--af-surface-2);
}

.md :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--af-surface-2);
  font-size: 12px;
}

.md :deep(strong) {
  color: var(--af-ink);
  font-weight: 650;
}
</style>
