<script setup lang="ts">
/**
 * 个人设置：当前账户的身份、交易成本口径、可买板块。
 *
 * 表单里按「人话单位」录入（万分之 / 千分之 / 元），提交时换算成小数比例，
 * 与后端 constants.PROFILE_RANGES 的区间一一对应——两边单位统一在入口处收口，
 * 避免出现「界面写 2.5、库里存 0.00025」这种对不上的情况。
 */
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import type { UserProfile } from '@/types'
import { fmtNum } from '@/utils/format'
import { errorText } from '@/utils/error'

const store = useAppStore()
const auth = useAuthStore()
const router = useRouter()

const visible = computed({
  get: () => store.settingsOpen,
  set: (value: boolean) => store.setSettings(value),
})

const form = reactive({
  account_name: '',
  commission_rate: 2.5,
  commission_min: 5,
  stamp_tax_rate: 0.5,
  transfer_fee_rate: 0.1,
  slippage_bps: 5,
  boards: {} as Record<string, boolean>,
})

const saving = ref(false)
/** 试算金额：固定 10 万，够看清一买一卖的量级差 */
const SAMPLE = 100_000

const draft = computed<UserProfile>(() => ({
  account_name: form.account_name.trim() || 'A 股主账户',
  commission_rate: clamp(form.commission_rate, 0, 300) / 10000,
  commission_min: Number(form.commission_min) || 0,
  stamp_tax_rate: clamp(form.stamp_tax_rate, 0, 20) / 1000,
  transfer_fee_rate: clamp(form.transfer_fee_rate, 0, 10) / 10000,
  slippage_bps: clamp(form.slippage_bps, 0, 200),
  boards: { ...form.boards },
}))

const dirty = computed(() => {
  const base = store.profile
  if (!base) return false
  const now = draft.value
  return (
    base.account_name !== now.account_name ||
    base.commission_rate !== round6(now.commission_rate) ||
    base.commission_min !== now.commission_min ||
    base.stamp_tax_rate !== round6(now.stamp_tax_rate) ||
    base.transfer_fee_rate !== round6(now.transfer_fee_rate) ||
    base.slippage_bps !== now.slippage_bps ||
    store.boardMeta.some((b) => base.boards[b.key] !== now.boards[b.key])
  )
})

const feeBuy = computed(() => calcFee(SAMPLE, 'BUY'))
const feeSell = computed(() => calcFee(SAMPLE, 'SELL'))
const boardCount = computed(() => Object.values(form.boards).filter(Boolean).length)

function clamp(value: number, low: number, high: number): number {
  const num = Number(value)
  if (!Number.isFinite(num)) return low
  return Math.min(Math.max(num, low), high)
}

function round6(value: number): number {
  return Math.round(value * 1e6) / 1e6
}

function calcFee(amount: number, side: 'BUY' | 'SELL') {
  const cfg = draft.value
  const commission = amount > 0 ? Math.max(amount * cfg.commission_rate, cfg.commission_min) : 0
  const stamp = side === 'SELL' ? amount * cfg.stamp_tax_rate : 0
  const transfer = amount * cfg.transfer_fee_rate
  const total = commission + stamp + transfer
  return {
    commission,
    stamp,
    transfer,
    total,
    rate: amount ? (total / amount) * 10000 : 0,
  }
}

function fill(profile: UserProfile) {
  form.account_name = profile.account_name
  form.commission_rate = round6(profile.commission_rate * 10000)
  form.commission_min = profile.commission_min
  form.stamp_tax_rate = round6(profile.stamp_tax_rate * 1000)
  form.transfer_fee_rate = round6(profile.transfer_fee_rate * 10000)
  form.slippage_bps = profile.slippage_bps
  form.boards = { ...(store.profileDefaults?.boards ?? {}), ...profile.boards }
}

watch(visible, async (open) => {
  if (!open) return
  if (!store.profile) await store.loadProfile()
  if (store.profile) fill(store.profile)
})

function resetToDefaults() {
  if (store.profileDefaults) {
    fill(store.profileDefaults)
    ElMessage.info('已恢复默认口径，保存后生效')
  }
}

async function save() {
  saving.value = true
  try {
    await store.saveProfile(draft.value)
    ElMessage.success('设置已保存，风控与费用口径即时生效')
    visible.value = false
  } finally {
    saving.value = false
  }
}

// ---------------------------------------------------------------- 安全
const pwdOpen = ref(false)
const pwdSaving = ref(false)
const pwdError = ref('')
const pwd = reactive({ old: '', next: '', confirm: '' })

function openPassword() {
  pwd.old = ''
  pwd.next = ''
  pwd.confirm = ''
  pwdError.value = ''
  pwdOpen.value = true
}

async function submitPassword() {
  if (pwdSaving.value) return
  pwdError.value = ''
  if (!pwd.old) {
    pwdError.value = '请输入当前密码'
    return
  }
  if (pwd.next.length < 6) {
    pwdError.value = '新密码至少 6 位'
    return
  }
  if (pwd.next !== pwd.confirm) {
    pwdError.value = '两次输入的新密码不一致'
    return
  }
  pwdSaving.value = true
  try {
    await auth.changePassword(pwd.old, pwd.next)
    ElMessage.success('密码已更新，其它设备上的登录已失效')
    pwdOpen.value = false
  } catch (e) {
    pwdError.value = errorText(e)
  } finally {
    pwdSaving.value = false
  }
}

async function logout() {
  try {
    await ElMessageBox.confirm('退出后需要重新输入密码才能进入工作台。', '退出登录', {
      confirmButtonText: '退出登录',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  visible.value = false
  await auth.logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <el-drawer
    v-model="visible"
    :size="store.isMobile ? '100%' : 452"
    class="settings-drawer"
  >
    <template #header>
      <div class="drawer-head">
        <span class="drawer-title">个人设置</span>
        <span class="drawer-sub">账户 · 成本口径 · 购买权限 · 安全</span>
      </div>
    </template>
    <div v-if="!store.profile" class="loading-tip">正在读取账户设置…</div>

    <div v-else class="body">
      <!-- 账户 -->
      <section class="block">
        <header class="block-head">
          <span class="block-title">当前账户</span>
          <span class="block-sub">{{ store.accountMode }}</span>
        </header>

        <label class="field acct">
          <span class="field-label">账户名称</span>
          <el-input v-model="form.account_name" maxlength="24" show-word-limit placeholder="A 股主账户" />
        </label>

        <div class="meta-row">
          <span class="meta-k">账户类型</span>
          <span class="meta-v">{{ store.accountMode }}</span>
          <span class="meta-k">可买板块</span>
          <span class="meta-v num">{{ boardCount }} / {{ store.boardMeta.length }}</span>
        </div>
      </section>

      <!-- 交易成本 -->
      <section class="block">
        <header class="block-head">
          <span class="block-title">交易成本</span>
          <span class="block-sub">影响费用预估与回测口径</span>
        </header>

        <div class="fields">
          <label class="field">
            <span class="field-label">佣金费率</span>
            <el-input-number
              v-model="form.commission_rate"
              :min="0"
              :max="300"
              :step="0.1"
              :precision="3"
              :controls="false"
              class="num-input"
            />
            <span class="unit">万分之</span>
          </label>

          <label class="field">
            <span class="field-label">最低佣金</span>
            <el-input-number
              v-model="form.commission_min"
              :min="0"
              :max="50"
              :step="1"
              :precision="2"
              :controls="false"
              class="num-input"
            />
            <span class="unit">元</span>
          </label>

          <label class="field">
            <span class="field-label">印花税 · 仅卖出</span>
            <el-input-number
              v-model="form.stamp_tax_rate"
              :min="0"
              :max="20"
              :step="0.1"
              :precision="3"
              :controls="false"
              class="num-input"
            />
            <span class="unit">千分之</span>
          </label>

          <label class="field">
            <span class="field-label">过户费</span>
            <el-input-number
              v-model="form.transfer_fee_rate"
              :min="0"
              :max="10"
              :step="0.1"
              :precision="3"
              :controls="false"
              class="num-input"
            />
            <span class="unit">万分之</span>
          </label>
        </div>

        <label class="field">
          <span class="field-label">滑点</span>
          <el-input-number
            v-model="form.slippage_bps"
            :min="0"
            :max="200"
            :step="1"
            :precision="0"
            :controls="false"
            class="num-input"
          />
          <span class="unit">bp</span>
        </label>

        <div class="fee-preview">
          <div class="fee-row">
            <span class="fee-k">买入 10 万</span>
            <span class="fee-v num">¥{{ fmtNum(feeBuy.total, 2) }}</span>
            <span class="fee-rate num">万 {{ feeBuy.rate.toFixed(2) }}</span>
          </div>
          <div class="fee-row">
            <span class="fee-k">卖出 10 万</span>
            <span class="fee-v num">¥{{ fmtNum(feeSell.total, 2) }}</span>
            <span class="fee-rate num">万 {{ feeSell.rate.toFixed(2) }}</span>
          </div>
          <p class="fee-note">含佣金、过户费，卖出含印花税；滑点只影响回测成交价，不计入费用。</p>
        </div>
      </section>

      <!-- 购买权限 -->
      <section class="block">
        <header class="block-head">
          <span class="block-title">购买权限</span>
          <span class="block-sub">未开通板块将被风控拦截</span>
        </header>

        <div class="board-list">
          <div v-for="b in store.boardMeta" :key="b.key" class="board-row">
            <span class="board-text">
              <span class="board-label">{{ b.label }}</span>
              <span class="board-hint">{{ b.hint }}</span>
            </span>
            <el-switch v-model="form.boards[b.key]" :disabled="!store.profile" />
          </div>
        </div>

        <p class="board-note">
          关闭后，该板块标的在计划卡「风控校验」中直接判为未通过，手工记账也会被拒绝。
        </p>
      </section>

      <!-- 安全 -->
      <section class="block">
        <header class="block-head">
          <span class="block-title">安全</span>
          <span class="block-sub">登录凭证</span>
        </header>

        <div class="sec-row">
          <span class="sec-text">
            <span class="sec-label">登录账号</span>
            <span class="sec-hint">会话有效期 {{ auth.sessionDays }} 天，改密后其它设备需重新登录</span>
          </span>
          <span class="sec-value num">{{ auth.username || 'admin' }}</span>
        </div>

        <div class="sec-actions">
          <el-button @click="openPassword">修改密码</el-button>
          <el-button class="danger-btn" @click="logout">退出登录</el-button>
        </div>
      </section>
    </div>

    <template #footer>
      <div class="footer">
        <el-button text @click="resetToDefaults">恢复默认</el-button>
        <div class="footer-right">
          <el-button @click="visible = false">取消</el-button>
          <el-button
            type="primary"
            :loading="saving"
            :disabled="!store.profile || !dirty"
            @click="save"
          >
            保存
          </el-button>
        </div>
      </div>
    </template>
  </el-drawer>

  <!-- 改密弹窗：与设置抽屉分开，避免长表单里再插一段凭证输入 -->
  <el-dialog v-model="pwdOpen" title="修改密码" width="368" :close-on-click-modal="false">
    <div class="pwd-form">
      <label class="field acct">
        <span class="field-label">当前密码</span>
        <el-input v-model="pwd.old" type="password" show-password autocomplete="current-password" />
      </label>
      <label class="field acct">
        <span class="field-label">新密码</span>
        <el-input v-model="pwd.next" type="password" show-password placeholder="至少 6 位" autocomplete="new-password" />
      </label>
      <label class="field acct">
        <span class="field-label">确认新密码</span>
        <el-input v-model="pwd.confirm" type="password" show-password autocomplete="new-password" />
      </label>
      <p v-if="pwdError" class="pwd-error">{{ pwdError }}</p>
      <p class="pwd-note">改密成功后，当前浏览器保持登录，其它设备上的会话全部失效。</p>
    </div>
    <template #footer>
      <el-button @click="pwdOpen = false">取消</el-button>
      <el-button type="primary" :loading="pwdSaving" @click="submitPassword">确认修改</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.drawer-head {
  display: flex;
  align-items: baseline;
  gap: 9px;
  min-width: 0;
}

.drawer-title {
  font-size: 13.5px;
  font-weight: 650;
  color: var(--af-ink);
  letter-spacing: 0;
}

.drawer-sub {
  font-size: 11px;
  color: var(--af-ink-4);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.body {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.loading-tip {
  padding: 40px 0;
  text-align: center;
  color: var(--af-ink-3);
  font-size: 12.5px;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.block + .block {
  padding-top: 20px;
  border-top: 1px solid var(--af-border);
}

.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.block-title {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--af-ink);
}

.block-sub {
  font-size: 11px;
  color: var(--af-ink-4);
}

.field {
  display: grid;
  grid-template-columns: 88px 104px 44px;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* 账户名要占满剩余宽度，不能跟费率输入框同一个窄列 */
.field.acct {
  grid-template-columns: 88px minmax(0, 1fr);
}

.fields {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.field-label {
  font-size: 12.5px;
  color: var(--af-ink-2);
  white-space: nowrap;
}

.field :deep(.el-input) {
  grid-column: 2 / 3;
}

.unit {
  font-size: 11px;
  color: var(--af-ink-3);
  white-space: nowrap;
}

.num-input {
  width: 100%;
}

.num-input :deep(.el-input__inner) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.meta-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto;
  align-items: baseline;
  gap: 6px 10px;
  padding: 9px 12px;
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
  font-size: 11.5px;
}

.meta-k {
  color: var(--af-ink-3);
}

.meta-v {
  color: var(--af-ink);
  font-weight: 600;
}

.fee-preview {
  padding: 10px 12px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
}

.fee-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto 64px;
  align-items: baseline;
  gap: 10px;
  font-size: 12px;
  padding: 2px 0;
}

.fee-k {
  color: var(--af-ink-3);
}

.fee-v {
  font-weight: 650;
}

.fee-rate {
  color: var(--af-ink-4);
  font-size: 11px;
  text-align: right;
}

.fee-note {
  margin: 6px 0 0;
  color: var(--af-ink-4);
  font-size: 11px;
  line-height: 1.5;
}

.board-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.board-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 2px;
}

.board-row + .board-row {
  border-top: 1px solid var(--af-border);
}

.board-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.board-label {
  font-size: 12.5px;
  color: var(--af-ink);
  font-weight: 550;
}

.board-hint {
  font-size: 10.5px;
  color: var(--af-ink-4);
}

.board-note {
  margin: 0;
  color: var(--af-ink-4);
  font-size: 11px;
  line-height: 1.55;
}

/* ---------------------------------------------------------------- 安全 */
.sec-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
}

.sec-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.sec-label {
  font-size: 12.5px;
  font-weight: 550;
  color: var(--af-ink);
}

.sec-hint {
  font-size: 10.5px;
  color: var(--af-ink-4);
}

.sec-value {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--af-ink);
}

.sec-actions {
  display: flex;
  gap: 8px;
}

/* 退出登录用暖色描边而不是实心红：它不是「危险操作」，只是需要确认 */
.danger-btn {
  --el-button-text-color: var(--af-up);
  --el-button-border-color: #f0cfcb;
  --el-button-hover-text-color: var(--af-up);
  --el-button-hover-bg-color: var(--af-up-soft);
  --el-button-hover-border-color: #e6b7b1;
}

.pwd-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pwd-error {
  margin: 0;
  padding: 7px 10px;
  border-radius: var(--af-radius-sm);
  background: var(--af-up-soft);
  border: 1px solid #f6d3cf;
  color: var(--af-up);
  font-size: 11.5px;
  line-height: 1.5;
}

.pwd-note {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--af-ink-4);
}

.footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.footer-right {
  display: flex;
  gap: 8px;
}
</style>
