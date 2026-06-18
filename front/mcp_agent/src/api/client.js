const API_BASE =
  import.meta.env.VITE_API_BASE ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8001')

const TOKEN_KEY = 'travel_access_token'
const USER_KEY = 'travel_user'

export function getApiBase() {
  return API_BASE
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function getStoredUser() {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

export function setAuth(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function onUnauthorized() {
  clearAuth()
  window.dispatchEvent(new CustomEvent('auth:logout'))
}

export async function apiFetch(path, options = {}) {
  const headers = {
    ...(options.headers || {}),
  }
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json'
  }
  const token = getToken()
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })

  if (res.status === 401 && !path.startsWith('/auth/')) {
    onUnauthorized()
    throw new Error('未登录或登录已过期')
  }

  return res
}

export async function apiJson(path, options = {}) {
  const res = await apiFetch(path, options)
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `HTTP ${res.status}`)
  }
  if (res.status === 204) return null
  return res.json()
}
