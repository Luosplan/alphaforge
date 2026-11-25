<script setup lang="ts">
/**
 * 个人设置：当前账户的身份、交易成本口径、可买板块。
 *
 * 表单里按「人话单位」录入（万分之 / 千分之 / 元），提交时换算成小数比例，
 * 与后端 constants.PROFILE_RANGES 的区间一一对应——两边单位统一在入口处收口，
 * 避免出现「界面写 2.5、库里存 0.00025」这种对不上的情况。
 *
 * 版面是「标签左、控件右」的行式，不是一个个白卡：设置项本身没有层级关系，
 * 套卡只会把七行内容切成七块，扫读反而更慢。
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
    ElMessage.info('已恢复默认，保存后生效')
  }
}

async function save() {
  saving.value = true
  try {
    await store.saveProfile(draft.value)
    ElMessage.success('已保存')
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
    ElMessage.success('密码已更新')
    pwdOpen.value = false
  } catch (e) {
    pwdError.value = errorText(e)
  } finally {
    pwdSaving.value = false
  }
}

async function logout() {
  try {
    await ElMessageBox.confirm('退出后需要重新登录。', '退出登录', {
      confirmButtonText: '退出',
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
  <el-drawer v-model="visible" :size="store.isMobile ? '100%' : 440" class="settings-drawer">
    <template #header>
      <div class="d-head">设置</div>
    </template>

    <div v-if="!store.profile" class="loading">读取中…</div>

    <div v-else class="body">
      <section class="grp">
        <div class="grp-t">账户</div>
        <div class="row">
          <span class="k">名称</span>
          <el-input v-model="form.account_name" class="in-line" maxlength="24" placeholder="A 股主账户" />
        </div>
        <div class="row">
          <span class="k">可买板块</span>
          <span class="v num">{{ boardCount }} / {{ store.boardMeta.length }}</span>
        </div>
      </section>

      <section class="grp">
        <div class="grp-t">交易成本</div>
        <div class="row">
          <span class="k">佣金</span>
          <div class="num-box">
            <el-input-number
              v-model="form.commission_rate"
              :min="0"
              :max="300"
              :step="0.1"
              :precision="3"
              :controls="false"
            />
            <span class="u">万分之</span>
          </div>
        </div>
        <div class="row">
          <span class="k">最低佣金</span>
          <div class="num-box">
            <el-input-number
              v-model="form.commission_min"
              :min="0"
              :max="50"
              :step="1"
              :precision="2"
              :controls="false"
            />
            <span class="u">元</span>
          </div>
        </div>
        <div class="row">
          <span class="k">印花税</span>
          <div class="num-box">
            <el-input-number
              v-model="form.stamp_tax_rate"
              :min="0"
              :max="20"
              :step="0.1"
              :precision="3"
              :controls="false"
            />
            <span class="u">千分之</span>
          </div>
        </div>
        <div class="row">
          <span class="k">过户费</span>
          <div class="num-box">
            <el-input-number
              v-model="form.transfer_fee_rate"
              :min="0"
              :max="10"
              :step="0.1"
              :precision="3"
              :controls="false"
            />
            <span class="u">万分之</span>
          </div>
        </div>
        <div class="row">
          <span class="k">滑点</span>
          <div class="num-box">
            <el-input-number
              v-model="form.slippage_bps"
              :min="0"
              :max="200"
              :step="1"
              :precision="0"
              :controls="false"
            />
            <span class="u">bp</span>
          </div>
        </div>
        <p class="note num">
          买入 10 万 ¥{{ fmtNum(feeBuy.total, 2) }} · 卖出 ¥{{ fmtNum(feeSell.total, 2) }}
        </p>
      </section>

      <section class="grp">
        <div class="grp-t">购买权限</div>
        <div v-for="b in store.boardMeta" :key="b.key" class="row">
          <span class="k">{{ b.label }}</span>
          <el-switch v-model="form.boards[b.key]" :disabled="!store.profile" />
        </div>
      </section>

      <section class="grp">
        <div class="grp-t">安全</div>
        <div class="row">
          <span class="k">账号</span>
          <span class="v num">{{ auth.username || 'admin' }}</span>
        </div>
        <div class="row">
          <span class="k">密码</span>
          <el-button text class="link" @click="openPassword">修改</el-button>
        </div>
        <div class="row">
          <span class="k">登录状态</span>
          <el-button text class="link" @click="logout">退出</el-button>
        </div>
      </section>
    </div>

    <template #footer>
      <div class="foot">
        <el-button text class="link" @click="resetToDefaults">恢复默认</el-button>
        <div class="right">
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

  <el-dialog v-model="pwdOpen" title="修改密码" width="360" :close-on-click-modal="false">
    <div class="pwd">
      <label class="field">
        <span class="k">当前密码</span>
        <el-input v-model="pwd.old" type="password" show-password autocomplete="current-password" />
      </label>
      <label class="field">
        <span class="k">新密码</span>
        <el-input v-model="pwd.next" type="password" show-password placeholder="至少 6 位" autocomplete="new-password" />
      </label>
      <label class="field">
        <span class="k">确认新密码</span>
        <el-input v-model="pwd.confirm" type="password" show-password autocomplete="new-password" />
      </label>
      <p v-if="pwdError" class="pwd-error">{{ pwdError }}</p>
    </div>
    <template #footer>
      <el-button @click="pwdOpen = false">取消</el-button>
      <el-button type="primary" :loading="pwdSaving" @click="submitPassword">确认修改</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.d-head {
  height: 58px;
  display: flex;
  align-items: center;
  padding: 0 26px;
  font-size: 15px;
  font-weight: 500;
}

.loading {
  padding: 26px;
  font-size: 13px;
  color: var(--af-ink-3);
}

.body {
  padding: 0 26px 8px;
}

.grp {
  padding: 18px 0;
  border-bottom: 1px solid var(--af-border);
}

.grp:last-child {
  border-bottom: 0;
}

.grp-t {
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--af-ink-3);
}

.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  min-height: 36px;
}

.k {
  font-size: 13px;
  color: var(--af-ink-2);
}

.v {
  font-size: 13px;
  color: var(--af-ink);
}

.in-line {
  width: 160px;
}

.num-box {
  display: flex;
  align-items: center;
  gap: 6px;
}

.num-box :deep(.el-input-number) {
  width: 104px;
}

.num-box :deep(.el-input__wrapper) {
  height: 32px;
  padding: 0 10px;
}

.num-box :deep(.el-input__inner) {
  text-align: right;
  font-size: 13px;
}

.u {
  font-size: 11px;
  color: var(--af-ink-3);
}

.note {
  margin: 10px 0 0;
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.link {
  font-size: 13px;
  padding: 0;
  height: auto;
}

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 26px;
  height: 64px;
  border-top: 1px solid var(--af-border);
  width: 100%;
}

.right {
  display: flex;
  gap: 10px;
}

.pwd {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.pwd-error {
  margin: 0;
  font-size: 12.5px;
  color: var(--af-up);
}

:deep(.el-input__wrapper) {
  border-radius: 8px;
}

.in-line :deep(.el-input__wrapper) {
  height: 32px;
}
</style>
