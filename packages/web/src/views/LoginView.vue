<script setup lang="ts">
/**
 * 登录页（public 路由，不套侧栏与顶栏）。
 *
 * 版面是全屏左右分栏而不是居中悬浮卡：一个 400px 的小卡摆在 1440×900 的画布上，
 * 四周全是空白灰场，看上去像没做完。左栏承担「这是什么东西」——品牌、定位、
 * 以及一张区间阶梯示意（产品最核心的视觉语言，不是装饰图）；右栏只承担一件事：登录。
 *
 * 两种状态：
 * 1. `login` —— 录入账号密码；
 * 2. `change` —— 内置默认密码（admin/admin）登录成功后强制改密。
 *    改密完成前守卫不放行，所以这里不给「返回」入口，避免出现半登录的夹生状态。
 *
 * 会话是 HttpOnly Cookie，页面不接触任何 token：登录成功即代表浏览器已持有凭证，
 * 后续请求由浏览器自动携带，前端只负责把「去哪」算清楚。
 */
import { nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { errorText } from '@/utils/error'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

type Mode = 'login' | 'change'
const mode = ref<Mode>(auth.authenticated && auth.mustChange ? 'change' : 'login')
const loading = ref(false)
const error = ref('')

const form = reactive({
  username: auth.username || 'admin',
  password: '',
  // 强制改密用：登录时输入的密码留在内存里直接复用；页面中途刷新过就没了，
  // 此时界面会多出一个「当前密码」输入框（见 needOld），不靠后端放宽校验
  oldPassword: '',
  newPassword: '',
  confirmPassword: '',
})

/**
 * 刷新后 session 还在、但登录时那个密码已经不在内存里，得让用户补一次。
 *
 * 用 ref 而不是 computed：computed 依赖 `form.oldPassword`，用户刚输入第一个字符
 * 这个字段就会消失——既跳版又像输入没生效。这里只在进入页面时判一次。
 */
const needOld = ref(mode.value === 'change' && !form.oldPassword)

const passwordRef = ref<{ focus: () => void } | null>(null)
const oldPasswordRef = ref<{ focus: () => void } | null>(null)
const newPasswordRef = ref<{ focus: () => void } | null>(null)

const redirect = (): string => {
  const raw = route.query.redirect
  const path = typeof raw === 'string' ? raw : '/'
  return path.startsWith('/login') ? '/' : path
}

/** 左栏的区间阶梯：静态示意，与行情无关，用来说明「区间化信号」长什么样 */
const LADDER = [
  { k: '目标区 TP2', v: '28.40', note: '减 30%', tone: 'up' },
  { k: '目标区 TP1', v: '26.90', note: '减 40%', tone: 'up' },
  { k: '建仓区', v: '24.60 – 25.30', note: '开仓 40%', tone: 'brand', active: true },
  { k: '加仓区', v: '23.40 – 24.10', note: '加 30%', tone: 'brand' },
  { k: '止损区', v: '22.10 – 22.60', note: '跌破失效', tone: 'down' },
]

const title = ref(mode.value === 'login' ? '登录' : '设置新密码')
const subtitle = ref(mode.value === 'login' ? '单用户工作台 · 凭证保存在本机' : '首次登录必须替换内置默认密码')

function errText(e: unknown): string {
  const message = errorText(e, '无法连接服务端，请确认后端已启动')
  // 登录接口的 401 在拦截器里是静默的，这里把话说清楚
  const status = (e as { response?: { status?: number } })?.response?.status
  return status === 401 ? '用户名或密码错误' : message
}

async function submitLogin() {
  if (loading.value) return
  error.value = ''
  if (!form.username.trim()) {
    error.value = '请输入账号'
    return
  }
  if (!form.password) {
    error.value = '请输入密码'
    return
  }
  loading.value = true
  try {
    const result = await auth.login(form.username.trim(), form.password)
    form.oldPassword = form.password
    form.password = ''
    if (result.must_change) {
      // 默认密码不能直接进工作台：原地切到改密态，改完才导航
      mode.value = 'change'
      title.value = '设置新密码'
      subtitle.value = '首次登录必须替换内置默认密码'
      await nextTick()
      newPasswordRef.value?.focus()
      return
    }
    ElMessage.success('已登录')
    await router.replace(redirect())
  } catch (e) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

async function submitChange() {
  if (loading.value) return
  error.value = ''
  if (needOld.value && !form.oldPassword) {
    error.value = '请输入当前密码'
    return
  }
  if (form.newPassword.length < 6) {
    error.value = '新密码至少 6 位'
    return
  }
  if (form.newPassword !== form.confirmPassword) {
    error.value = '两次输入的新密码不一致'
    return
  }
  loading.value = true
  try {
    await auth.changePassword(form.oldPassword, form.newPassword)
    ElMessage.success('密码已更新，下次登录请使用新密码')
    await router.replace(redirect())
  } catch (e) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await nextTick()
  if (mode.value === 'login') passwordRef.value?.focus()
  else if (needOld.value) oldPasswordRef.value?.focus()
  else newPasswordRef.value?.focus()
})
</script>

<template>
  <div class="auth">
    <!-- 左栏：品牌 + 区间阶梯。窄屏只留品牌行，平板去掉示意图，手机整体折叠成窄条 -->
    <aside class="side">
      <div class="side-glow" />

      <div class="brand">
        <span class="mark">AF</span>
        <span class="brand-text">
          <span class="wordmark">AlphaForge</span>
          <span class="tagline">个人量化工作台</span>
        </span>
      </div>

      <p class="lede">
        不猜买卖点。
        <br />
        给一段可以分批执行的区间。
      </p>

      <div class="ladder">
        <div class="ladder-head">
          <span class="ladder-title">区间计划卡</span>
          <span class="ladder-sub num">600519 · 示例</span>
        </div>
        <div
          v-for="row in LADDER"
          :key="row.k"
          class="band"
          :class="[row.tone, { active: row.active }]"
        >
          <span class="band-bar" />
          <span class="band-k">{{ row.k }}</span>
          <span class="band-v num">{{ row.v }}</span>
          <span class="band-note">{{ row.note }}</span>
        </div>
        <div class="ladder-foot">
          <span>ATR(14) 定标尺</span>
          <span class="sep" />
          <span>4 : 3 : 3 分批</span>
          <span class="sep" />
          <span>R : R ≥ 2.4</span>
        </div>
      </div>

      <ul class="points">
        <li>
          <span class="k">全 A 真实行情</span>
          <span class="v">5,500+ 标的日频落本地库，断网可回放</span>
        </li>
        <li>
          <span class="k">34 因子 · 自然语言选股</span>
          <span class="v">一句话转条件，逐条可删可改</span>
        </li>
        <li>
          <span class="k">事前风控</span>
          <span class="v">单票 15% / 行业 30% / 总仓 90%，越界直接拦</span>
        </li>
      </ul>

      <div class="side-foot">
        <span class="ver num">v0.7.0</span>
        <span class="sep" />
        <span>仅供研究，不构成投资建议</span>
      </div>
    </aside>

    <!-- 右栏：表单 -->
    <main class="pane">
      <div class="pane-badge">
        <span class="dot" />
        <span>本地部署 · 无云端同步</span>
      </div>

      <div class="pane-body">
        <section class="form-wrap">
          <header class="head">
            <h1 class="title">{{ title }}</h1>
            <p class="sub">{{ subtitle }}</p>
          </header>

          <div v-if="mode === 'login'" class="form">
            <label class="field">
              <span class="label">账号</span>
              <el-input
                v-model="form.username"
                placeholder="admin"
                maxlength="32"
                autocomplete="username"
                @keyup.enter="submitLogin"
              />
            </label>

            <label class="field">
              <span class="label">密码</span>
              <el-input
                ref="passwordRef"
                v-model="form.password"
                type="password"
                show-password
                placeholder="请输入访问密码"
                autocomplete="current-password"
                @keyup.enter="submitLogin"
              />
            </label>

            <p v-if="error" class="error">{{ error }}</p>

            <el-button type="primary" class="submit" :loading="loading" @click="submitLogin">
              进入工作台
            </el-button>

            <p class="hint">
              首次使用请用内置账号 <code>admin</code> / <code>admin</code> 登录，
              登录后会要求立即修改密码。凭证仅保存在本机 <code>data/auth.json</code>。
            </p>
          </div>

          <div v-else class="form">
            <div class="notice">
              <span class="notice-dot" />
              <span>当前使用的是内置默认密码，为防他人直接打开这台机器上的工作台，必须先设置新密码。</span>
            </div>

            <label v-if="needOld" class="field">
              <span class="label">当前密码</span>
              <el-input
                ref="oldPasswordRef"
                v-model="form.oldPassword"
                type="password"
                show-password
                placeholder="登录时使用的密码"
                autocomplete="current-password"
                @keyup.enter="submitChange"
              />
            </label>

            <label class="field">
              <span class="label">新密码</span>
              <el-input
                ref="newPasswordRef"
                v-model="form.newPassword"
                type="password"
                show-password
                placeholder="至少 6 位"
                autocomplete="new-password"
                @keyup.enter="submitChange"
              />
            </label>

            <label class="field">
              <span class="label">确认新密码</span>
              <el-input
                v-model="form.confirmPassword"
                type="password"
                show-password
                placeholder="再输入一次"
                autocomplete="new-password"
                @keyup.enter="submitChange"
              />
            </label>

            <p v-if="error" class="error">{{ error }}</p>

            <el-button type="primary" class="submit" :loading="loading" @click="submitChange">
              保存并进入工作台
            </el-button>

            <p class="hint">
              账号为 <code>{{ auth.username || 'admin' }}</code>，密码修改后无法找回；
              忘记密码可删除 <code>data/auth.json</code> 后重启服务，恢复内置默认密码。
            </p>
          </div>
        </section>

        <!-- 宽屏右侧信息栏：把「上手三步」铺出来，避免 1440 下右半屏是一整片白 -->
        <aside class="rail">
          <span class="rail-title">上手三步</span>
          <ol class="steps">
            <li>
              <span class="step-no num">01</span>
              <span class="step-text">
                <span class="step-k">同步行情</span>
                <span class="step-v">首次全量约 19 分钟，之后增量 1~2 分钟</span>
              </span>
            </li>
            <li>
              <span class="step-no num">02</span>
              <span class="step-text">
                <span class="step-k">构造条件选股</span>
                <span class="step-v">一句话或表单式条件，逐条可删可改</span>
              </span>
            </li>
            <li>
              <span class="step-no num">03</span>
              <span class="step-text">
                <span class="step-k">读区间计划卡</span>
                <span class="step-v">建仓 / 加仓 / 止损 / 目标四区，附仓位与赔率</span>
              </span>
            </li>
          </ol>
          <div class="rail-note">
            <span class="rail-dot" />
            <span>数据全部落在本机，登录只决定谁能打开它</span>
          </div>
        </aside>
      </div>

      <footer class="pane-foot">
        <span>新浪财经 / 东方财富公开接口</span>
        <span class="sep" />
        <span>本地 SQLite 缓存</span>
        <span class="sep" />
        <span>单用户</span>
      </footer>
    </main>
  </div>
</template>

<style scoped>
.auth {
  height: 100vh;
  display: grid;
  /* 左栏固定宽度、右栏吃满剩余：表单永远居中在右半区，不会随窗口拉成一条缝 */
  grid-template-columns: clamp(360px, 38%, 520px) minmax(0, 1fr);
  background: var(--af-bg);
  overflow: hidden;
}

/* ------------------------------------------------------------------ 左栏 */
.side {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: 30px 34px 24px;
  background: var(--af-ink);
  color: #fff;
  overflow: hidden;
}

/* 细网格 + 右上角主色光斑：给深色区一点仪器台的质感，而不是一块死黑 */
.side::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image: linear-gradient(rgba(255, 255, 255, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.035) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.9), transparent 78%);
  pointer-events: none;
}

.side-glow {
  position: absolute;
  inset: -30% -10% auto auto;
  width: 420px;
  height: 420px;
  background: radial-gradient(circle, rgba(31, 79, 216, 0.5), transparent 66%);
  pointer-events: none;
}

.side > * {
  position: relative;
  z-index: 1;
}

/* 光斑是装饰层：上面那条 `.side > *` 会把它从 absolute 拉回文档流并占掉 420px，
   所以这里必须重新声明一次绝对定位（同权重、靠后者生效） */
.side > .side-glow {
  position: absolute;
  z-index: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
}

.mark {
  width: 30px;
  height: 30px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  background: #fff;
  color: var(--af-ink);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.brand-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.wordmark {
  font-size: 14px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.tagline {
  font-size: 10.5px;
  color: rgba(255, 255, 255, 0.55);
  letter-spacing: 0.03em;
}

.lede {
  margin: 6px 0 0;
  font-size: 17px;
  line-height: 1.5;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: rgba(255, 255, 255, 0.94);
}

/* ------------------------------------------------------------ 区间阶梯 */
.ladder {
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: var(--af-radius);
  background: rgba(255, 255, 255, 0.03);
  overflow: hidden;
}

.ladder-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 11px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.ladder-title {
  font-size: 11.5px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.82);
  letter-spacing: 0.01em;
}

.ladder-sub {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.45);
}

.band {
  display: grid;
  grid-template-columns: 3px 74px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 6px 11px 6px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 11px;
}

.band:first-of-type {
  border-top: 0;
}

.band-bar {
  width: 3px;
  height: 16px;
  border-radius: 0 2px 2px 0;
  background: rgba(255, 255, 255, 0.25);
}

.band.up .band-bar {
  background: var(--af-up);
}
.band.down .band-bar {
  background: var(--af-down);
}
.band.brand .band-bar {
  background: #6d8ff0;
}

.band.active {
  background: rgba(31, 79, 216, 0.22);
}

.band-k {
  padding-left: 8px;
  color: rgba(255, 255, 255, 0.62);
  white-space: nowrap;
}

.band-v {
  color: rgba(255, 255, 255, 0.95);
  font-weight: 600;
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.band-note {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.42);
  white-space: nowrap;
}

.ladder-foot {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  padding: 7px 11px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  font-size: 10px;
  color: rgba(255, 255, 255, 0.42);
}

/* -------------------------------------------------------------- 能力点 */
.points {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.points li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-left: 11px;
  border-left: 1px solid rgba(255, 255, 255, 0.16);
}

.points .k {
  font-size: 11.5px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.9);
}

.points .v {
  font-size: 10.5px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.5);
}

.side-foot {
  margin-top: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 10.5px;
  color: rgba(255, 255, 255, 0.4);
}

.ver {
  color: rgba(255, 255, 255, 0.68);
  font-weight: 600;
}

.sep {
  width: 1px;
  height: 10px;
  background: currentColor;
  opacity: 0.4;
}

/* ------------------------------------------------------------------ 右栏 */
.pane {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 32px 24px;
  background: var(--af-surface);
  overflow-y: auto;
}

.pane-badge {
  position: absolute;
  top: 18px;
  right: 22px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  border: 1px solid var(--af-border);
  border-radius: 999px;
  font-size: 10.5px;
  color: var(--af-ink-3);
  background: var(--af-surface-2);
}

.pane-badge .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--af-down);
}

/* 表单 + 信息栏：两块一起在右半区居中，信息栏在窄屏隐藏后表单自然居中 */
.pane-body {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 40px;
  width: 100%;
}

.form-wrap {
  width: min(348px, 100%);
  display: flex;
  flex-direction: column;
  gap: 22px;
}

/* ------------------------------------------------------------ 右侧信息栏 */
/* 只在 ≥1280 出现：1080 这类窗口右半区本来就不宽，再加一栏会把表单挤成窄条 */
.rail {
  display: none;
  width: 208px;
  flex: 0 0 208px;
  flex-direction: column;
  gap: 14px;
  padding-left: 26px;
  border-left: 1px solid var(--af-border);
}

@media (min-width: 1280px) {
  .rail {
    display: flex;
  }
}

.rail-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--af-ink-3);
  text-transform: uppercase;
}

.steps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.steps li {
  display: flex;
  gap: 9px;
}

.step-no {
  flex: 0 0 auto;
  font-size: 10.5px;
  font-weight: 650;
  color: var(--af-ink-4);
  line-height: 1.6;
}

.step-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.step-k {
  font-size: 12px;
  font-weight: 600;
  color: var(--af-ink);
}

.step-v {
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--af-ink-3);
}

.rail-note {
  display: flex;
  gap: 7px;
  margin-top: 2px;
  padding-top: 12px;
  border-top: 1px solid var(--af-border);
  font-size: 10px;
  line-height: 1.55;
  color: var(--af-ink-4);
}

.rail-dot {
  flex: 0 0 5px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--af-brand);
  margin-top: 5px;
}

.head {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.title {
  margin: 0;
  font-size: 21px;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: var(--af-ink);
}

.sub {
  margin: 0;
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.label {
  font-size: 12px;
  color: var(--af-ink-2);
  font-weight: 550;
}

.field :deep(.el-input__wrapper) {
  padding: 2px 11px;
}

.field :deep(.el-input__inner) {
  height: 32px;
}

.submit {
  width: 100%;
  height: 38px;
  margin-top: 2px;
  font-size: 13px;
}

.error {
  margin: 0;
  padding: 7px 10px;
  border-radius: var(--af-radius-sm);
  background: var(--af-up-soft);
  border: 1px solid #f6d3cf;
  color: var(--af-up);
  font-size: 11.5px;
  line-height: 1.5;
}

.notice {
  display: flex;
  gap: 8px;
  padding: 9px 11px;
  border-radius: var(--af-radius-sm);
  background: var(--af-warn-soft);
  border: 1px solid #f3ddc2;
  color: var(--af-warn);
  font-size: 11.5px;
  line-height: 1.55;
}

.notice-dot {
  flex: 0 0 5px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  margin-top: 6px;
}

.hint {
  margin: 2px 0 0;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--af-ink-4);
}

.hint code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--af-surface-3);
  color: var(--af-ink-2);
}

.pane-foot {
  position: absolute;
  bottom: 16px;
  left: 32px;
  right: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 10.5px;
  color: var(--af-ink-4);
}

/* --------------------------------------------------------------- 响应式 */
/* 平板：左栏收窄，阶梯只留「标签 + 数值」两列，能力清单压缩行距但仍保留 */
@media (max-width: 1023px) {
  .auth {
    grid-template-columns: minmax(0, 300px) minmax(0, 1fr);
  }
  .side {
    padding: 26px 24px 22px;
  }
  .lede {
    font-size: 15px;
  }
  .band {
    grid-template-columns: 3px 68px minmax(0, 1fr);
  }
  .band-note {
    display: none;
  }
  .points {
    gap: 10px;
  }
  .points .v {
    line-height: 1.45;
  }
}

/* 手机：整页纵向，左栏压成一条品牌带，表单铺满剩余高度。
   这里必须换成 flex：grid 的 align-content 默认 stretch 会把「品牌带」那一行
   拉到整屏一半高，品牌就在一块 250px 的深色块里飘着 */
@media (max-width: 767px) {
  .auth {
    height: auto;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    grid-template-columns: none;
  }
  .side {
    flex: 0 0 auto;
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 15px 18px;
  }
  /* 手机上左栏就是一条品牌带，阶梯和说明都收起来——整屏高度要留给表单 */
  .lede,
  .ladder,
  .points,
  .side-foot {
    display: none;
  }
  .pane {
    flex: 1;
    justify-content: flex-start;
    padding: 24px 18px 18px;
  }
  .pane-badge {
    display: none;
  }
  /* 表单贴着品牌带往下排（拇指友好），多余空间留给脚注上方 */
  .form-wrap {
    width: 100%;
    gap: 18px;
  }
  .pane-foot {
    position: static;
    margin-top: auto;
    justify-content: flex-start;
  }
}

/* 矮屏（笔记本小窗、分屏）：只压缩间距，内容全留——
   阶梯 + 能力清单加起来约 490px，567px 高的窗口正好装得下 */
@media (max-height: 640px) and (min-width: 768px) {
  .side {
    gap: 12px;
    padding: 20px 30px 16px;
  }
  .lede {
    font-size: 15px;
    margin-top: 0;
  }
  .band {
    padding-top: 5px;
    padding-bottom: 5px;
  }
  .points {
    gap: 9px;
  }
  .points .v {
    line-height: 1.4;
  }
}
</style>
