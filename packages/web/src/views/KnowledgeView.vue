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

function factorWidth(weight: number, list: PlaybookStrategy['selection']['factors']): string {
  const max = Math.max(...list.map((f) => f.weight), 0.0001)
  return `${Math.max((weight / max) * 100, 2)}%`
}

/** 数值带单位：百分比贴紧，其余（亿、倍、元）留一个空格，否则「1.3倍」读起来挤。 */
function withUnit(value: unknown, unit: string): string {
  if (!unit) return String(value)
  return unit === '%' ? `${value}%` : `${value} ${unit}`
}

/** 命中值：布尔条件写成「是/否」，数值保留两位有效小数——成交额 13.0543 亿太吵。 */
function hitText(h: PlaybookPick['hits'][number]): string {
  if (h.value == null) return '—'
  if (typeof h.value === 'boolean') return h.value ? '是' : '否'
  return withUnit(Number(h.value.toFixed(2)), h.unit || '')
}

/** 门槛：布尔条件不重复写「是」，它不是一道数值门槛。 */
function targetText(h: PlaybookPick['hits'][number]): string {
  if (typeof h.target === 'boolean') return ''
  if (Array.isArray(h.target)) return `${h.op}${h.target.join('~')}${h.unit || ''}`
  return `${h.op}${withUnit(h.target, h.unit || '')}`
}

/** 硬条件：布尔条件写「是/否」，数值条件把比较符与单位带上。 */
function condText(c: PlaybookStrategy['selection']['conditions'][number]): string {
  if (typeof c.target === 'boolean') return c.target ? '是' : '否'
  if (Array.isArray(c.target)) return `${c.op} ${c.target.join('~')}${c.unit || ''}`
  return `${c.op} ${withUnit(c.target, c.unit || '')}`
}

const picksNote = computed(() => {
  const s = active.value?.selection
  if (!s) return ''
  const sort = s.sort ? `按${s.sort.label}${s.sort.desc ? '从高到低' : '从低到高'}` : '按综合得分'
  return `${sort}排序，同一行业最多 ${s.max_per_industry} 只，取前 ${s.top_n} 只`
})

const marketNote = computed(() => {
  const e = active.value?.entry
  if (!e) return ''
  if (!e.market_ma) return '这条策略不看大盘位置，任何指数环境下都照常选股，仓位也不按指数打折'
  return `沪深300 站在 ${e.market_ma} 日均线上方才开新仓，跌破只做已有持仓的离场`
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

          <div class="kb-grid">
            <div class="kb-block">
              <div class="kb-title">硬条件：不满足就不在池子里</div>
              <ul v-if="active.selection.conditions.length" class="kb-list">
                <li v-for="(c, i) in active.selection.conditions" :key="i">
                  <b>{{ c.label }}</b>
                  <span class="kb-op">{{ condText(c) }}</span>
                </li>
              </ul>
              <p v-else class="muted">这条策略没有硬条件，全池按因子打分排序</p>
            </div>

            <div class="kb-block">
              <div class="kb-title">排序与名额</div>
              <p class="kb-line">{{ picksNote }}</p>
              <p class="kb-line muted">
                行业分散是为了不把 {{ active.selection.top_n }} 个名额押在同一个题材上
              </p>
            </div>
          </div>

          <div class="kb-block">
            <div class="kb-title">软条件：因子加权（进化后的权重）</div>
            <div class="factors">
              <div v-for="f in active.selection.factors" :key="f.key" class="ft-row">
                <span class="ft-label">{{ f.label }}</span>
                <div class="ft-track">
                  <div
                    class="ft-fill"
                    :style="{ width: factorWidth(f.weight, active.selection.factors) }"
                  />
                </div>
                <span class="ft-weight num">{{ (f.weight * 100).toFixed(1) }}%</span>
                <span class="ft-desc muted">{{ f.desc }}</span>
              </div>
            </div>
          </div>

          <div class="kb-block">
            <div class="kb-title">今天选中的 {{ active.selection.picks.length }} 只 · 为什么是它们</div>
            <p v-if="active.selection.notice" class="muted kb-line">{{ active.selection.notice }}</p>
            <div v-if="active.selection.picks.length" class="picks">
              <article v-for="p in active.selection.picks" :key="p.code" class="pick">
                <header class="pk-head">
                  <span class="pk-rank num">{{ p.rank }}</span>
                  <b class="pk-name">{{ p.name }}</b>
                  <span class="pk-code num">{{ p.code }}</span>
                  <span class="chip">{{ p.industry }}</span>
                  <span class="pk-chg num" :class="(p.change_pct ?? 0) >= 0 ? 'up' : 'down'">
                    {{ signed(p.change_pct) }}%
                  </span>
                </header>

                <div class="pk-why">
                  <span class="pk-k">命中</span>
                  <span v-for="(h, i) in p.hits" :key="i" class="pk-tag">
                    {{ h.label }} {{ hitText(h) }}
                    <em v-if="targetText(h)" class="muted">{{ targetText(h) }}</em>
                  </span>
                </div>

                <div v-if="p.factors.length" class="pk-why">
                  <span class="pk-k">加分</span>
                  <span v-for="f in p.factors.filter((x) => x.contribution > 0)" :key="f.key" class="pk-tag">
                    {{ f.label }} {{ signed(f.raw) }}{{ f.unit }}
                    <em class="muted">×{{ (f.weight * 100).toFixed(0) }}%</em>
                  </span>
                </div>

                <div v-if="p.reason.negative?.length" class="pk-why">
                  <span class="pk-k warn">留意</span>
                  <span v-for="(n, i) in p.reason.negative" :key="i" class="pk-tag warn">{{ n }}</span>
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
          <ol class="rules">
            <li>
              <div class="rule-k">信号与成交隔一夜</div>
              <p>
                选股用的都是<b>收盘后</b>才定下来的数，所以今天算出的信号最早
                <b>明天开盘</b>成交，当天买不进（T+1）。
              </p>
            </li>
            <li>
              <div class="rule-k">买点</div>
              <p v-if="active.entry.gap_band">
                明天<b>高开 {{ active.entry.gap_band[0] }}%~{{ active.entry.gap_band[1] }}%</b>
                才买。低于 {{ active.entry.gap_band[0] }}% 说明昨天的强势没被市场确认；
                高于 {{ active.entry.gap_band[1] }}% 已经接近一字板，追进去没有溢价、还可能买不到。
              </p>              <p v-else-if="active.entry.gap_gate">
                明天<b>开盘按市价</b>买。高开超过 {{ active.entry.gap_max }}%
                视为情绪透支不追；低开超过 {{ Math.abs(active.entry.gap_min) }}%
                视为跳空破位，昨天的信号已作废。
              </p>
              <p v-else>
                明天<b>开盘按市价</b>买，不看跳空幅度——这条策略赚的是趋势延续，高开就是确认。
              </p>
            </li>
            <li>
              <div class="rule-k">买不到的两种情况</div>
              <p>
                开盘一字涨停（买盘封死）直接跳过；现金不够或单票超过
                <b>{{ (active.entry.sizing.single_weight * 100).toFixed(0) }}%</b> 仓位上限时，
                按可用额度缩量或跳过。
              </p>
            </li>
            <li>
              <div class="rule-k">大盘闸门</div>
              <p>{{ marketNote }}</p>
            </li>
            <!-- 不看大盘的策略没有「弱市」这个状态，回测里 regime 恒为空、
                 折算系数从不生效，写了就是骗人。 -->
            <li v-if="active.entry.market_ma">
              <div class="rule-k">弱势怎么处理</div>
              <p v-if="active.entry.weak_participate">
                弱市不停手：仓位按 ×{{ active.entry.regime_scale }} 折算，
                <template v-if="active.entry.weak_huddle">
                  并且要求所属行业当天有抱团迹象（行业涨停家数够多或涨幅排进前列），
                  不抱团的票只做观察
                </template>
                <template v-else>不额外要求行业抱团</template>。
              </p>
              <p v-else>弱市直接停手，一张新单都不挂，等指数回到均线上方再恢复。</p>
            </li>
            <li>
              <div class="rule-k">一次买多少</div>
              <p>
                最多同时持有 <b>{{ active.entry.sizing.top_n }} 只</b>，同一行业不超过
                <b>{{ active.entry.sizing.max_per_industry }} 只</b>；单票不超过总资产的
                <b>{{ (active.entry.sizing.single_weight * 100).toFixed(0) }}%</b>，
                单笔最大亏损控制在总资产的
                <b>{{ (active.entry.sizing.risk_per_trade * 100).toFixed(1) }}%</b>，
                所以止损越宽，买入股数越少。
              </p>
            </li>
            <li>
              <div class="rule-k">能不能加仓</div>
              <p>
                {{ active.entry.allow_add ? '允许加仓：回踩不破关键均线时补到目标仓位。' : '不加仓：一次下注就是一次，中途补仓等于把已确认的判断改成两次没确认的下注。' }}
              </p>
            </li>
          </ol>
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
          <div class="exits">
            <div class="ex">
              <div class="ex-k down">止损</div>
              <p>成交价 − <b>{{ active.exit.stop_atr }}×ATR</b>，跌破即卖，不讲理由。</p>
            </div>
            <div class="ex">
              <div class="ex-k up">第一目标 TP1</div>
              <p>
                成交价 + <b>{{ active.exit.tp1_atr }}×ATR</b>，卖出<b>一半</b>。
                <template v-if="active.exit.breakeven_after_tp1">
                  剩余仓位的止损同时上移到成本价，这笔交易从此不可能再亏损。
                </template>
              </p>
            </div>
            <div class="ex">
              <div class="ex-k up">第二目标 TP2</div>
              <p>成交价 + <b>{{ active.exit.tp2_atr }}×ATR</b>，剩余仓位全部清掉。</p>
            </div>
            <div class="ex">
              <div class="ex-k">时间止损</div>
              <p>
                持有满 <b>{{ active.exit.time_stop }} 个交易日</b>还没到目标，无论盈亏都退出——
                资金被占着不涨，本身就是成本。
              </p>
            </div>
          </div>

          <p v-if="active.exit.need_limit_close" class="kb-line">
            另外：买入当天收盘没封住涨停，次日开盘无条件走。
          </p>

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

.kb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 14px;
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

.kb-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.kb-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 10px;
  border-radius: var(--af-radius-xs);
  background: var(--af-surface-2);
  font-size: 12px;
}

.kb-op {
  color: var(--af-brand-ink);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.kb-line {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--af-ink-2);
}

.factors {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ft-row {
  display: grid;
  grid-template-columns: 92px 1fr 52px minmax(0, 1.4fr);
  align-items: center;
  gap: 10px;
}

.ft-label {
  font-size: 12px;
  color: var(--af-ink-2);
}

.ft-track {
  height: 8px;
  border-radius: 4px;
  background: var(--af-surface-3);
  overflow: hidden;
}

.ft-fill {
  height: 100%;
  border-radius: 4px;
  background: var(--af-brand);
  opacity: 0.75;
}

.ft-weight {
  text-align: right;
  font-size: 11.5px;
  color: var(--af-ink-2);
}

.ft-desc {
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.pk-tag {
  padding: 2px 7px;
  border-radius: 5px;
  background: var(--af-surface-2);
  font-size: 11.5px;
  color: var(--af-ink-2);
}

.pk-tag.warn {
  background: var(--af-warn-soft);
  color: var(--af-warn);
}

.pk-tag em {
  font-style: normal;
  font-size: 10.5px;
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

.rules {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
  counter-reset: rule;
}

.rules li {
  position: relative;
  padding-left: 22px;
  counter-increment: rule;
}

.rules li::before {
  content: counter(rule);
  position: absolute;
  left: 0;
  top: 1px;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  background: var(--af-surface-3);
  color: var(--af-ink-2);
  font-size: 10px;
  font-weight: 600;
  line-height: 15px;
  text-align: center;
}

.rule-k {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--af-ink);
  margin-bottom: 3px;
}

.rules p {
  margin: 0;
  font-size: 12px;
  line-height: 1.75;
  color: var(--af-ink-2);
}

.rules b {
  color: var(--af-ink);
  font-weight: 600;
}

.exits {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
}

.ex {
  padding: 10px 12px;
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
}

.ex-k {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 4px;
}

.ex-k.down {
  color: var(--af-down);
}

.ex-k.up {
  color: var(--af-up);
}

.ex p {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--af-ink-2);
}

.ex b {
  color: var(--af-ink);
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

  .ft-row {
    grid-template-columns: 76px 1fr 46px;
  }

  .ft-desc {
    display: none;
  }

  .picks {
    grid-template-columns: 1fr;
  }

  .pk-levels {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
</style>
