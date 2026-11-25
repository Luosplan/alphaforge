<script setup lang="ts">
/**
 * 登录页（public 路由，不套侧栏与顶栏）。
 *
 * 版面是居中单列，一屏只放一件事：登录。左栏的品牌故事、区间示意、上手三步
 * 全部移除——那三块内容对每天都要登录的人来说，每次进来就重复一次噪音。
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

const title = ref(mode.value === 'login' ? '登录' : '设置新密码')
const subtitle = ref(mode.value === 'login' ? '数据保存在本机' : '首次登录需要先改密码')

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
      subtitle.value = '首次登录需要先改密码'
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
    <div class="card">
      <div class="brand">
        <span class="mark">AF</span>
        <span class="name">AlphaForge</span>
      </div>

      <header class="head">
        <h1>{{ title }}</h1>
        <p>{{ subtitle }}</p>
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
            placeholder="请输入密码"
            autocomplete="current-password"
            @keyup.enter="submitLogin"
          />
        </label>

        <p v-if="error" class="error">{{ error }}</p>

        <el-button type="primary" class="submit" :loading="loading" @click="submitLogin">
          登录
        </el-button>

        <p class="tip">首次使用 admin / admin</p>
      </div>

      <div v-else class="form">
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
          保存并进入
        </el-button>

        <p class="tip">忘记密码：删除 data/auth.json 后重启</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth {
  position: relative;
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: #fff;
  padding: 24px;
}

.card {
  position: relative;
  width: 328px;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  gap: 30px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
}

.mark {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: var(--af-ink);
  color: #fff;
  display: grid;
  place-items: center;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.02em;
}

.name {
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.01em;
}

.head h1 {
  margin: 0;
  font-size: 24px;
  font-weight: 500;
  letter-spacing: -0.02em;
  color: var(--af-ink);
}

.head p {
  margin: 7px 0 0;
  font-size: 13px;
  color: var(--af-ink-3);
}

.form {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.label {
  font-size: 12.5px;
  color: var(--af-ink-2);
}

.error {
  margin: -8px 0 0;
  font-size: 12.5px;
  color: var(--af-up);
}

.submit {
  width: 100%;
  height: 44px;
  font-size: 14px;
}

.tip {
  margin: 0;
  font-size: 12.5px;
  color: var(--af-ink-3);
  text-align: center;
}

:deep(.el-input__wrapper) {
  height: 44px;
  padding: 0 14px;
  border-radius: 8px;
}

:deep(.el-input__inner) {
  font-size: 14px;
}
</style>
