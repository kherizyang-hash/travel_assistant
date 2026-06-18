<script setup>
import { ref } from 'vue'
import { apiJson, setAuth } from '../api/client.js'

const emit = defineEmits(['logged-in'])

const mode = ref('login')
const email = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

const submit = async () => {
  error.value = ''
  if (!email.value.trim() || !password.value) {
    error.value = '请填写邮箱和密码'
    return
  }
  loading.value = true
  try {
    const path = mode.value === 'login' ? '/auth/login' : '/auth/register'
    const data = await apiJson(path, {
      method: 'POST',
      body: JSON.stringify({
        email: email.value.trim(),
        password: password.value,
      }),
    })
    setAuth(data.access_token, { user_id: data.user_id, email: data.email })
    emit('logged-in', data)
  } catch (e) {
    error.value = e.message || '操作失败'
  } finally {
    loading.value = false
  }
}

const toggleMode = () => {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  error.value = ''
}
</script>

<template>
  <div class="login-wrapper">
    <div class="login-card">
      <h2>差旅出行助手</h2>
      <p class="login-sub">{{ mode === 'login' ? '登录后开始规划行程' : '注册新账号' }}</p>

      <form @submit.prevent="submit">
        <label>
          邮箱
          <input v-model="email" type="email" placeholder="you@example.com" autocomplete="username" />
        </label>
        <label>
          密码
          <input
            v-model="password"
            type="password"
            placeholder="至少 6 位"
            autocomplete="current-password"
          />
        </label>
        <p v-if="error" class="login-error">{{ error }}</p>
        <button type="submit" class="login-btn" :disabled="loading">
          {{ loading ? '请稍候…' : mode === 'login' ? '登录' : '注册' }}
        </button>
      </form>

      <button type="button" class="toggle-btn" @click="toggleMode">
        {{ mode === 'login' ? '没有账号？注册' : '已有账号？登录' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.login-wrapper {
  width: 100%;
  max-width: 520px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}

.login-card {
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 16px;
  padding: 2.25rem 2.5rem;
  box-shadow: 0 8px 32px rgba(15, 23, 42, 0.08);
}

.login-card h2 {
  margin: 0 0 0.25rem;
  font-size: 1.5rem;
  color: #0f172a;
}

.login-sub {
  margin: 0 0 1.5rem;
  color: #64748b;
  font-size: 0.9rem;
}

label {
  display: block;
  margin-bottom: 1rem;
  font-size: 0.85rem;
  color: #475569;
}

input {
  display: block;
  width: 100%;
  margin-top: 0.35rem;
  padding: 0.65rem 0.75rem;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  font-size: 0.95rem;
  box-sizing: border-box;
}

input:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}

.login-error {
  color: #dc2626;
  font-size: 0.85rem;
  margin: 0 0 0.75rem;
}

.login-btn {
  width: 100%;
  padding: 0.75rem;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: white;
  font-size: 1rem;
  cursor: pointer;
}

.login-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.toggle-btn {
  width: 100%;
  margin-top: 1rem;
  padding: 0.5rem;
  border: none;
  background: transparent;
  color: #3b82f6;
  cursor: pointer;
  font-size: 0.9rem;
}
</style>
