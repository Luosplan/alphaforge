<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api'
import type { PlaybookPick, PlaybookStrategy } from '@/types'

const loading = ref(true)
const strategies = ref<PlaybookStrategy[]>([])
const activeId = ref('')

const active = computed(
  () => strategies.value.find((s) => s.id === activeId.value) ?? strategies.value[0] ?? null,
)

onMounted(async () => {
  try {
    const res = await api.playbook()
    strategies.value = res.strategies
    activeId.value = res.strategies[0]?.id ?? ''
  } finally {
    loading.value = false
  }
})

const px = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))
const signed = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`

/** 漏斗条按平方根压缩：5383→62 这种量级差用线性宽，后面几步会细成一条线。 */
function funnelWidth(count: number, max: number): string {
  if (!max) return '0%'
  return `${Math.max(Math.sqrt(count / max) * 100, 9)}%`
}

/** 数值带单位：百分比贴紧，其余（亿、倍、元）留一个空格，否则「1.3倍」读起来挤。 */
function withUnit(value: unknown, unit: string): string {
  if (!unit) return String(value)
  return unit === '%' ? `${value}%` : `${value} ${unit}`
}

/** 命中值：布尔条件没有可比的数，标签里只留字段名，不写「涨停 是」。 */
function hitText(h: PlaybookPick['hits'][number]): string {
  if (h.value == null || typeof h.value === 'boolean') return ''
  return withUnit(Number(h.value.toFixed(2)), h.unit || '')
}

/** 门槛：布尔条件不重复写「是」，它不是一道数值门槛。 */
function targetText(h: PlaybookPick['hits'][number]): string {
  if (typeof h.target === 'boolean') return ''
  if (Array.isArray(h.target)) return `${h.op}${h.target.join('~')}${h.unit || ''}`
  return `${h.op}${withUnit(h.target, h.unit || '')}`
}

/** 硬条件同理：`涨停` 本身就是条件，后面再跟一个「是」是废话。 */
function condText(c: PlaybookStrategy['selection']['conditions'][number]): string {
  if (typeof c.target === 'boolean') return ''
  if (Array.isArray(c.target)) return `${c.op} ${c.target.join('~')}${c.unit || ''}`
  return `${c.op} ${withUnit(c.target, c.unit || '')}`
}

type Tag = { t: string; v?: string; tone?: 'up' | 'down' }
type Rule = { k: string; tags: Tag[]; why: string }

const sortTags = computed<Tag[]>(() => {
  const s = active.value?.selection
  if (!s) return []
  if (!s.sort) return [{ t: '综合得分', v: '从高到低' }]
  return [{ t: s.sort.label, v: s.sort.desc ? '从高到低' : '从低到高' }]
})

const quotaTags = computed<Tag[]>(() => {
  const s = active.value?.selection
  if (!s) return []
  const tags: Tag[] = [{ t: '同行业', v: `≤ ${s.max_per_industry} 只` }]
  const lo = s.pool_limit_min
  const hi = s.pool_limit_max
  if (lo || hi) {
    tags.push({
      t: '连板',
      v: lo && hi ? `${lo}~${hi} 板` : lo ? `≥ ${lo} 板` : `≤ ${hi} 板`,
    })
  }
  tags.push({ t: '取前', v: `${s.top_n} 只` })
  return tags
})

const entryRules = computed<Rule[]>(() => {
  const a = active.value
  const e = a?.entry
  if (!e || !a) return []
  const s = e.sizing
  const rules: Rule[] = [
    {
      k: '出手时间',
      tags: [{ t: '收盘出信号' }, { t: '次日开盘买' }],
      why: '选股用的是收盘价，当天买不进（T+1）',
    },
    {
      k: '买点',
      tags: e.gap_band
        ? [{ t: '高开', v: `${e.gap_band[0]}%~${e.gap_band[1]}%` }]
        : [{ t: '开盘市价' }],
      why: e.gap_band
        ? `低于 ${e.gap_band[0]}% 没人接，高于 ${e.gap_band[1]}% 接近一字`
        : e.gap_gate
          ? `高开超过 ${e.gap_max}% 视为情绪透支，不追`
          : '赚的是趋势延续，不看跳空',
    },
    {
      k: '买不到',
      tags: [{ t: '一字涨停', v: '跳过' }, { t: '超仓位上限', v: '缩量' }],
      why: '封死了买不进；钱不够就按可用额度少买',
    },
    {
      k: '大盘',
      tags: e.market_ma
        ? [{ t: '沪深300', v: `> ${e.market_ma} 日线` }]
        : [{ t: '不看大盘' }],
      why: e.market_ma
        ? '跌破只做已有持仓的离场，不开新仓'
        : '涨停梯队在熊市里照样存在',
    },
  ]
  const sel = a.selection
  if (sel.sent_min_top) {
    const top = sel.sentiment.top
    rules.push({
      k: '情绪',
      tags: [
        { t: '最高连板', v: `≥ ${sel.sent_min_top} 板` },
        top ? { t: '今日最高', v: `${top} 板 · 涨停 ${sel.sentiment.up} 只` } : { t: '今日', v: '—' },
      ],
      why: '最高只有几板说明没人愿意接高位，1~2 板的次日溢价也拿不到',
    })
  }
  if (e.market_ma && e.weak_participate) {
    rules.push({
      k: '弱势',
      tags: [
        { t: '仓位', v: `×${e.regime_scale}` },
        { t: e.weak_huddle ? '要求行业抱团' : '不要求抱团' },
      ],
      why: '只放一半钱，靠题材强度筛掉没支撑的票',
    })
  }
  rules.push(
    {
      k: '仓位',
      tags: [
        { t: '最多', v: `${s.top_n} 只` },
        { t: '同行业', v: `≤ ${s.max_per_industry} 只` },
        { t: '单票', v: `≤ ${Math.round(s.single_weight * 100)}%` },
        { t: '单笔亏损', v: `≤ ${(s.risk_per_trade * 100).toFixed(1)}%` },
      ],
      why: '止损越宽，买入股数越少',
    },
    {
      k: '加仓',
      tags: [{ t: e.allow_add ? '允许加仓' : '不加仓' }],
      why: e.allow_add
        ? '信号还在就补到计划仓位'
        : '一次下注就是一次，中途补仓等于两次没确认的下注',
    },
  )
  return rules
})

const exitRules = computed<Rule[]>(() => {
  const x = active.value?.exit
  if (!x) return []
  return [
    {
      k: '止损',
      tags: [{ t: `-${x.stop_atr}×ATR`, tone: 'down' }],
      why: '跌破就走，不找理由',
    },
    {
      k: '第一目标',
      tags: [
        { t: `+${x.tp1_atr}×ATR`, tone: 'up' },
        { t: '卖一半' },
        ...(x.breakeven_after_tp1 ? [{ t: '止损移到成本' }] : []),
      ],
      why: '先锁一半利润，剩下的用成本价去博',
    },
    {
      k: '第二目标',
      tags: [{ t: `+${x.tp2_atr}×ATR`, tone: 'up' }, { t: '余仓清空' }],
      why: '走到这一档说明是主升段，不再留尾仓',
    },
    {
      k: '时间止损',
      tags: [{ t: `${x.time_stop} 个交易日` }],
      why: '到期没到任何一档也走，钱占着不涨就是成本',
    },
  ]
})
</script>

<template>
  <div v-loading="loading" class="view kb">
    <section class="panel head">
      <div class="panel-head wrap">
        <div class="panel-title">
          策略知识库
          <span v-if="active" class="panel-sub">
            第 {{ active.generation }} 代参数 · 基准日 {{ active.selection.as_of ?? '—' }}
          </span>
        </div>
        <div class="head-tools">
          <el-radio-group v-model="activeId" size="small">
            <el-radio-button v-for="s in strategies" :key="s.id" :value="s.id">
              {{ s.name }}
            </el-radio-button>
          </el-radio-group>
        </div>
      </div>
      <div v-if="active" class="panel-body intro">
        <p class="intro-desc">{{ active.desc }}</p>
        <div class="intro-chips">
          <span class="chip">选 {{ active.selection.top_n }} 只</span>
          <span class="chip">止损 {{ active.exit.stop_atr }}×ATR</span>
          <span class="chip">止盈 {{ active.exit.tp1_atr }}× / {{ active.exit.tp2_atr }}× ATR</span>
          <span class="chip">持有 {{ active.exit.time_stop }} 个交易日</span>
          <span v-if="active.fitness != null" class="chip brand">进化得分 {{ active.fitness }}</span>
        </div>
      </div>
    </section>

    <template v-if="active">
      <!-- 一、选股 -->
      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            <span class="kb-no">一</span>
            怎么选出这几只
            <span class="panel-sub">{{ active.selection.universe }}</span>
          </div>
        </div>
        <div class="panel-body kb-body">
          <div class="funnel">
            <div v-for="step in active.selection.funnel" :key="step.key" class="fn-row">
              <span class="fn-label">{{ step.label }}</span>
              <div class="fn-track">
                <div
                  class="fn-fill"
                  :class="{ last: step.key === 'picks' }"
                  :style="{
                    width: funnelWidth(
                      step.count,
                      active.selection.funnel[0]?.count ?? 1,
                    ),
                  }"
                />
              </div>
              <span class="fn-count num">{{ step.count }}</span>
            </div>
          </div>

          <div class="rows">
            <div class="row">
              <span class="row-k">硬条件</span>
              <div class="tags">
                <span v-for="(c, i) in active.selection.conditions" :key="i" class="tg">
                  <b>{{ c.label }}</b><i v-if="condText(c)">{{ condText(c) }}</i>
                </span>
                <span v-if="!active.selection.conditions.length" class="tg">
                  <b>无</b><i>全池打分</i>
                </span>
              </div>
            </div>
            <div class="row">
              <span class="row-k">排序</span>
              <div class="tags">
                <span v-for="(t, i) in sortTags" :key="i" class="tg">
                  <b>{{ t.t }}</b><i>{{ t.v }}</i>
                </span>
              </div>
            </div>
            <div class="row">
              <span class="row-k">名额</span>
              <div class="tags">
                <span v-for="(t, i) in quotaTags" :key="i" class="tg">
                  <b>{{ t.t }}</b><i>{{ t.v }}</i>
                </span>
              </div>
            </div>
            <div class="row">
              <span class="row-k">加分</span>
              <div class="tags">
                <span
                  v-for="f in active.selection.factors"
                  :key="f.key"
                  class="tg"
                  :class="{ brand: f.weight >= 0.2 }"
                >
                  <b>{{ f.label }}</b><i>×{{ (f.weight * 100).toFixed(0) }}%</i>
                </span>
              </div>
            </div>
          </div>

          <div class="kb-block">
            <div class="kb-title">今天选中的 {{ active.selection.picks.length }} 只 · 为什么是它们</div>
            <div v-if="active.selection.picks.length" class="picks">
              <article v-for="p in active.selection.picks" :key="p.code" class="pick">
                <header class="pk-head">
                  <span class="pk-rank num">{{ p.rank }}</span>
                  <b class="pk-name">{{ p.name }}</b>
                  <span class="pk-code num">{{ p.code }}</span>
                  <span v-if="p.limit_cnt && active.selection.pool_limit_min" class="tg brand">
                    <b>{{ p.limit_cnt }} 板</b>
                  </span>
                  <span class="chip">{{ p.industry }}</span>
                  <span class="pk-chg num" :class="(p.change_pct ?? 0) >= 0 ? 'up' : 'down'">
                    {{ signed(p.change_pct) }}%
                  </span>
                </header>

                <div class="pk-why">
                  <span class="pk-k">命中</span>
                  <span v-for="(h, i) in p.hits" :key="i" class="tg">
                    <b>{{ h.label }}</b>
                    <i v-if="hitText(h)">{{ hitText(h) }}</i>
                    <em v-if="targetText(h)">{{ targetText(h) }}</em>
                  </span>
                </div>

                <div v-if="p.factors.length" class="pk-why">
                  <span class="pk-k">加分</span>
                  <span v-for="f in p.factors.filter((x) => x.contribution > 0)" :key="f.key" class="tg">
                    <b>{{ f.label }}</b>
                    <i>{{ signed(f.raw) }}{{ f.unit }}</i>
                    <em>×{{ (f.weight * 100).toFixed(0) }}%</em>
                  </span>
                </div>

                <div v-if="p.reason.negative?.length" class="pk-why">
                  <span class="pk-k warn">留意</span>
                  <span v-for="(n, i) in p.reason.negative" :key="i" class="tg warn">{{ n }}</span>
                </div>

                <footer class="pk-levels">
                  <span class="lv"><i>现价</i><b class="num">{{ px(p.price) }}</b></span>
                  <span class="lv down"><i>止损</i><b class="num">{{ px(p.levels.stop) }}</b></span>
                  <span class="lv up"><i>TP1</i><b class="num">{{ px(p.levels.tp1) }}</b></span>
                  <span class="lv up"><i>TP2</i><b class="num">{{ px(p.levels.tp2) }}</b></span>
                  <span class="lv"><i>ATR</i><b class="num">{{ p.atr }}</b></span>
                </footer>
              </article>
            </div>
            <p v-else-if="active.selection.sentiment_note" class="muted">
              {{ active.selection.sentiment_note }}
            </p>
            <p v-else class="muted">今天没有符合条件的标的：宁缺勿滥，不凑数</p>
          </div>
        </div>
      </section>

      <!-- 二、出手 -->
      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            <span class="kb-no">二</span>
            什么时候出手
            <span class="panel-sub">{{ active.entry.timing }}</span>
          </div>
        </div>
        <div class="panel-body kb-body">
          <div class="rows">
            <div v-for="(r, i) in entryRules" :key="i" class="row">
              <span class="row-k">{{ r.k }}</span>
              <div class="tags">
                <span v-for="(t, j) in r.tags" :key="j" class="tg">
                  <b>{{ t.t }}</b><i v-if="t.v">{{ t.v }}</i>
                </span>
                <span class="row-why">{{ r.why }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 三、卖出 -->
      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            <span class="kb-no">三</span>
            什么时候卖出
            <span class="panel-sub">价格全部按成交价 ± ATR(14) 计算</span>
          </div>
        </div>
        <div class="panel-body kb-body">
          <div class="rows">
            <div v-for="(r, i) in exitRules" :key="i" class="row">
              <span class="row-k">{{ r.k }}</span>
              <div class="tags">
                <span
                  v-for="(t, j) in r.tags"
                  :key="j"
                  class="tg"
                  :class="t.tone"
                >
                  <b>{{ t.t }}</b><i v-if="t.v">{{ t.v }}</i>
                </span>
                <span class="row-why">{{ r.why }}</span>
              </div>
            </div>
            <div v-if="active.exit.need_limit_close" class="row">
              <span class="row-k">烂板</span>
              <div class="tags">
                <span class="tg"><b>当日没封住</b><i>次日走</i></span>
                <span class="row-why">收盘没涨停说明板没做住，不等第二天开盘的低开</span>
              </div>
            </div>
          </div>

          <div v-if="active.selection.picks.length" class="kb-block">
            <div class="kb-title">按今天的价位算，三档在哪里</div>
            <table class="lv-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>现价</th>
                  <th>止损</th>
                  <th>TP1（减半）</th>
                  <th>TP2（清仓）</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in active.selection.picks" :key="p.code">
                  <td class="lv-name">{{ p.name }}<span class="num muted"> {{ p.code }}</span></td>
                  <td class="num">{{ px(p.price) }}</td>
                  <td class="num down">{{ px(p.levels.stop) }}</td>
                  <td class="num up">{{ px(p.levels.tp1) }}</td>
                  <td class="num up">{{ px(p.levels.tp2) }}</td>
                </tr>
              </tbody>
            </table>
            <p class="muted kb-line">
              以现价代替成交价估算，实际以明日开盘成交价为准；ATR 越大的票，止损止盈的绝对价差越宽。
            </p>
          </div>
        </div>
      </section>
    </template>

    <div v-else-if="!loading" class="empty">暂无策略</div>
  </div>
</template>

<style scoped>
.view.kb {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 内容区是固定高度的滚动容器，panel 作为 flex 子项默认会被压缩，
   内容多的那块底部就被 overflow:hidden 切掉了。 */
.view.kb > .panel {
  flex: none;
}

.intro {
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-top: 1px solid var(--af-border);
}

.intro-desc {
  margin: 0;
  color: var(--af-ink-2);
  font-size: 12.5px;
  line-height: 1.7;
}

.intro-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.kb-no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 5px;
  background: var(--af-brand-soft);
  color: var(--af-brand);
  font-size: 11px;
  font-weight: 600;
}

.kb-body {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.funnel {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.fn-row {
  display: grid;
  grid-template-columns: 92px 1fr 64px;
  align-items: center;
  gap: 10px;
}

.fn-label {
  font-size: 12px;
  color: var(--af-ink-2);
}

.fn-track {
  height: 16px;
  border-radius: 5px;
  background: var(--af-surface-2);
  overflow: hidden;
}

.fn-fill {
  height: 100%;
  border-radius: 5px;
  background: linear-gradient(90deg, rgba(26, 86, 219, 0.5), rgba(26, 86, 219, 0.22));
}

.fn-fill.last {
  background: linear-gradient(90deg, var(--af-brand), #4b7ce8);
}

.fn-count {
  text-align: right;
  font-size: 12px;
  font-weight: 600;
}

/* 一个条件一个标签，读法和同花顺问财一样：先看字段，再看门槛。 */
.rows {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.row {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.row-k {
  padding-top: 3px;
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.tg {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--af-surface-2);
  font-size: 12px;
  line-height: 18px;
  color: var(--af-ink-2);
  white-space: nowrap;
}

.tg b {
  font-weight: 600;
  color: var(--af-ink);
}

.tg i {
  font-style: normal;
  font-weight: 600;
  color: var(--af-brand-ink);
  font-variant-numeric: tabular-nums;
}

.tg.brand {
  background: var(--af-brand-soft);
}

.tg.brand i {
  color: var(--af-brand);
}

.tg.up,
.tg.up b {
  color: var(--af-up);
}

.tg.down,
.tg.down b {
  color: var(--af-down);
}

.tg.warn {
  background: var(--af-warn-soft);
  color: var(--af-warn);
}

.tg.warn b {
  color: inherit;
}

.tg em {
  font-style: normal;
  font-size: 11px;
  font-weight: 500;
  color: var(--af-ink-3);
}

.tg.warn em {
  color: inherit;
  opacity: 0.85;
}

.row-why {
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.kb-block {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.kb-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--af-ink-2);
}

.kb-line {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--af-ink-2);
}

.picks {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
  gap: 12px;
}

.pick {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface);
}

.pk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pk-rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 5px;
  background: var(--af-brand-soft);
  color: var(--af-brand);
  font-size: 11px;
  font-weight: 600;
}

.pk-name {
  font-size: 13.5px;
}

.pk-code {
  font-size: 11px;
  color: var(--af-ink-3);
}

.pk-chg {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
}

.pk-why {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
}

.pk-k {
  width: 30px;
  flex: none;
  font-size: 11px;
  color: var(--af-ink-3);
}

.pk-k.warn {
  color: var(--af-warn);
}

.pk-levels {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 6px;
  margin-top: auto;
  padding-top: 8px;
  border-top: 1px dashed var(--af-border);
}

.lv {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.lv i {
  font-style: normal;
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.lv b {
  font-size: 12.5px;
  font-weight: 600;
}

.lv.down b {
  color: var(--af-down);
}

.lv.up b {
  color: var(--af-up);
}

.lv-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.lv-table th {
  padding: 7px 10px;
  text-align: center;
  font-size: 11px;
  font-weight: 550;
  color: var(--af-ink-3);
  background: var(--af-surface-2);
  border-bottom: 1px solid var(--af-border);
  white-space: nowrap;
}

.lv-table td {
  padding: 7px 10px;
  text-align: center;
  border-bottom: 1px solid var(--af-border);
}

.lv-table tr:last-child td {
  border-bottom: none;
}

.lv-name {
  text-align: left;
}

.lv-name .num {
  margin-left: 5px;
}

@media (max-width: 760px) {
  .fn-row {
    grid-template-columns: 72px 1fr 48px;
  }

  /* 窄屏上标签行改成上下排：62px 的标签栏会把条件挤成一条条竖字。 */
  .row {
    grid-template-columns: 1fr;
    gap: 5px;
  }

  .row-k {
    padding-top: 0;
  }

  .picks {
    grid-template-columns: 1fr;
  }

  .pk-levels {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
</style>
