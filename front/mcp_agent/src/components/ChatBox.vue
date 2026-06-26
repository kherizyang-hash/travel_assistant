<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import MarkdownIt from 'markdown-it'
import LoginPanel from './LoginPanel.vue'
import {
  apiFetch,
  apiJson,
  clearAuth,
  getStoredUser,
  getToken,
} from '../api/client.js'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})
md.validateLink = (url) => /^https?:\/\//i.test(url)

const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

const WELCOME = {
  role: 'ai',
  content: '你好！我是差旅出行助手，可以帮你规划出差行程、查询高铁/机票、天气和路线，并保存行程单。',
}

const isAuthenticated = ref(!!getToken())
const currentUser = ref(getStoredUser())

const messages = ref([{ ...WELCOME }])
const userInput = ref('')
const isLoading = ref(false)
const chatContainer = ref(null)

const sidebarOpen = ref(true)
const conversations = ref([])
const conversationsLoading = ref(false)
const currentThreadId = ref(null)

const showFileModal = ref(false)
const fileList = ref([])
const fileLoading = ref(false)
const fileError = ref('')
const selectedFile = ref(null)
const previewContent = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const deletingFile = ref('')

const resetMessages = () => {
  messages.value = [{ ...WELCOME }]
}

const formatFileSize = (bytes) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const formatTime = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString('zh-CN')
}

const onLoggedIn = (data) => {
  isAuthenticated.value = true
  currentUser.value = { user_id: data.user_id, email: data.email }
  bootstrapSession()
}

const logout = () => {
  clearAuth()
  isAuthenticated.value = false
  currentUser.value = null
  currentThreadId.value = null
  conversations.value = []
  resetMessages()
}

const handleAuthLogout = () => {
  logout()
}

onMounted(() => {
  sidebarOpen.value = window.innerWidth >= 768
  window.addEventListener('auth:logout', handleAuthLogout)
  if (isAuthenticated.value) {
    bootstrapSession()
  }
})

onUnmounted(() => {
  window.removeEventListener('auth:logout', handleAuthLogout)
})

const loadConversations = async () => {
  conversationsLoading.value = true
  try {
    conversations.value = await apiJson('/travel/conversations')
  } catch {
    conversations.value = []
  } finally {
    conversationsLoading.value = false
  }
}

const bootstrapSession = async () => {
  await loadConversations()
  if (conversations.value.length === 0) {
    await createNewConversation()
  } else {
    await selectConversation(conversations.value[0].thread_id)
  }
}

const createNewConversation = async () => {
  try {
    const data = await apiJson('/travel/conversations', { method: 'POST' })
    currentThreadId.value = data.thread_id
    resetMessages()
    await loadConversations()
  } catch (e) {
    console.error(e)
  }
}

const selectConversation = async (threadId) => {
  if (isLoading.value) return
  if (
    threadId === currentThreadId.value &&
    messages.value.some((m) => m.thinking?.lines?.length)
  ) {
    return
  }
  currentThreadId.value = threadId
  try {
    const data = await apiJson(`/travel/conversations/${threadId}/messages`)
    if (data.messages?.length) {
      messages.value = data.messages.map((m) => ({
        role: m.role,
        content: m.content,
      }))
    } else {
      resetMessages()
    }
    await scrollToBottom()
  } catch {
    resetMessages()
  }
}

const toggleSidebar = () => {
  sidebarOpen.value = !sidebarOpen.value
}

const fetchFileList = async () => {
  fileLoading.value = true
  fileError.value = ''
  try {
    fileList.value = await apiJson('/files')
  } catch (e) {
    fileError.value = `加载失败：${e.message}`
    fileList.value = []
  } finally {
    fileLoading.value = false
  }
}

const openFileModal = async () => {
  showFileModal.value = true
  await fetchFileList()
}

const closeFileModal = () => {
  showFileModal.value = false
  selectedFile.value = null
  previewContent.value = ''
  previewError.value = ''
}

const previewFile = async (filename) => {
  selectedFile.value = filename
  previewLoading.value = true
  previewError.value = ''
  previewContent.value = ''
  try {
    const data = await apiJson(`/files/${encodeURIComponent(filename)}/content`)
    previewContent.value = data.content || ''
  } catch (e) {
    previewError.value = `预览失败：${e.message}`
  } finally {
    previewLoading.value = false
  }
}

const downloadFile = async (filename) => {
  try {
    const res = await apiFetch(`/files/${encodeURIComponent(filename)}?download=1`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    fileError.value = `下载失败：${e.message}`
  }
}

const deleteFile = async (filename) => {
  if (deletingFile.value) return
  if (!confirm(`确定删除「${filename}」？此操作不可恢复。`)) return

  deletingFile.value = filename
  try {
    const res = await apiFetch(`/files/${encodeURIComponent(filename)}`, {
      method: 'DELETE',
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || `HTTP ${res.status}`)
    }
    fileList.value = fileList.value.filter((f) => f.name !== filename)
    if (selectedFile.value === filename) {
      selectedFile.value = null
      previewContent.value = ''
      previewError.value = ''
    }
  } catch (e) {
    fileError.value = `删除失败：${e.message}`
  } finally {
    deletingFile.value = ''
  }
}

const scrollToBottom = async () => {
  await nextTick()
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

const appendThinkingLine = (index, line) => {
  const msg = messages.value[index]
  if (!line || !msg?.thinking || msg.thinking.lines.includes(line)) return
  msg.thinking.lines = [...msg.thinking.lines, line]
}

const toggleThinking = (msg) => {
  if (msg.thinking?.lines?.length) {
    msg.thinking.collapsed = !msg.thinking.collapsed
  }
}

const sendMessage = async () => {
  const content = userInput.value.trim()
  if (!content || isLoading.value || !currentThreadId.value) return

  messages.value.push({ role: 'user', content })
  userInput.value = ''
  isLoading.value = true
  await scrollToBottom()

  const aiMessageIndex = messages.value.length
  messages.value.push({
    role: 'ai',
    content: '',
    thinking: {
      lines: ['正在分析您的需求…'],
      collapsed: false,
      done: false,
    },
  })

  try {
    const response = await apiFetch('/travel/chat/stream', {
      method: 'POST',
      body: JSON.stringify({ message: content, thread_id: currentThreadId.value }),
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        let payload
        try {
          payload = JSON.parse(line.slice(6))
        } catch {
          continue
        }

        if (payload.type === 'status' || payload.type === 'tool_start' || payload.type === 'tool_end') {
          appendThinkingLine(aiMessageIndex, payload.message)
          await scrollToBottom()
        } else if (payload.type === 'done') {
          const msg = messages.value[aiMessageIndex]
          msg.thinking = { ...msg.thinking, done: true, collapsed: true }
          msg.content = payload.content || '(未获取到回复，请重试)'
          await scrollToBottom()
        } else if (payload.type === 'error') {
          const msg = messages.value[aiMessageIndex]
          msg.thinking = { ...msg.thinking, done: true, collapsed: true }
          msg.content = `[系统错误: ${payload.error || '未知错误'}]`
        }
      }
    }

    const msg = messages.value[aiMessageIndex]
    if (!msg.thinking.done) {
      msg.thinking = { ...msg.thinking, done: true, collapsed: true }
      if (!msg.content) {
        msg.content = '(未获取到回复，请重试)'
      }
    }
    await loadConversations()
  } catch (e) {
    const msg = messages.value[aiMessageIndex]
    msg.thinking = { ...msg.thinking, done: true, collapsed: true }
    msg.content = `[网络请求出错: ${e.message}]`
  } finally {
    isLoading.value = false
    window.getSelection()?.removeAllRanges()
    scrollToBottom()
  }
}

const renderMarkdown = (text) => {
  return md.render(text || '')
}
</script>

<template>
  <LoginPanel v-if="!isAuthenticated" @logged-in="onLoggedIn" />

  <div v-else class="chat-wrapper">
    <div class="app-layout" :class="{ 'sidebar-collapsed': !sidebarOpen }">
      <!-- 左侧对话历史 -->
      <aside v-show="sidebarOpen" class="sidebar">
        <div class="sidebar-header">
          <span class="sidebar-title">对话历史</span>
        </div>
        <div class="sidebar-list">
          <div v-if="conversationsLoading" class="sidebar-empty">加载中…</div>
          <div v-else-if="conversations.length === 0" class="sidebar-empty">暂无对话</div>
          <button
            v-for="conv in conversations"
            :key="conv.thread_id"
            type="button"
            class="conv-item"
            :class="{ active: conv.thread_id === currentThreadId }"
            @click="selectConversation(conv.thread_id)"
          >
            <span class="conv-title">{{ conv.title }}</span>
            <span class="conv-time">{{ formatTime(conv.updated_at) }}</span>
          </button>
        </div>
      </aside>

      <div class="chat-container">
      <!-- 顶部栏 -->
      <div class="chat-header">
        <div class="header-left">
          <button type="button" class="icon-btn" title="展开/收起对话历史" @click="toggleSidebar">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
          </button>
          <button type="button" class="new-chat-btn" @click="createNewConversation" :disabled="isLoading">
            + 新对话
          </button>
          <div class="header-content">
            <div class="status-dot"></div>
            <h2>差旅出行助手</h2>
          </div>
        </div>
        <div class="header-right">
          <span class="user-email">{{ currentUser?.email }}</span>
          <button type="button" class="logout-btn" @click="logout">退出</button>
        </div>
      </div>
      
      <!-- 消息列表 -->
      <div class="messages" ref="chatContainer">
        <div v-for="(msg, index) in messages" :key="index" :class="['message-row', msg.role]">
          <div class="avatar">
            <span v-if="msg.role === 'ai'">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2 2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path d="M4 11a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7z"/><path d="M9 16a2 2 0 1 0 4 0 2 2 0 1 0-4 0"/><path d="M15 7v2"/><path d="M9 7v2"/></svg>
            </span>
            <span v-else>
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            </span>
          </div>
          <div class="message-bubble">
            <template v-if="msg.role === 'ai'">
              <div v-if="msg.thinking?.lines?.length" class="thinking-block">
                <button
                  type="button"
                  class="thinking-toggle"
                  @click="toggleThinking(msg)"
                >
                  <span>{{ msg.thinking.done ? '思考完毕' : '思考中…' }}</span>
                  <span class="thinking-arrow">{{ msg.thinking.collapsed ? '▶' : '▼' }}</span>
                </button>
                <div v-show="!msg.thinking.collapsed" class="thinking-lines">
                  <div
                    v-for="(line, li) in msg.thinking.lines"
                    :key="li"
                    class="thinking-line"
                  >{{ line }}</div>
                </div>
              </div>
              <div
                v-if="msg.content"
                class="markdown-body"
                v-html="renderMarkdown(msg.content)"
              ></div>
            </template>
            <div v-else>{{ msg.content }}</div>
          </div>
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="input-area">
        <div class="input-toolbar">
          <button class="history-btn" @click="openFileModal" :disabled="isLoading" title="查看历史文件">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
            </svg>
            历史文件
          </button>
        </div>
        <div class="input-box">
          <input 
            v-model="userInput" 
            @keyup.enter="sendMessage"
            placeholder="输入你的问题..." 
            :disabled="isLoading"
          />
          <button @click="sendMessage" :disabled="isLoading || !userInput.trim()" class="send-btn">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        </div>
      </div>
    </div>
    </div>

    <!-- 历史文件模态框 -->
    <div v-if="showFileModal" class="modal-overlay" @click.self="closeFileModal">
      <div class="modal-panel modal-panel-wide">
        <div class="modal-header">
          <h3>历史文件</h3>
          <button class="modal-close" @click="closeFileModal">&times;</button>
        </div>
        <div class="modal-body modal-split">
          <div class="file-list-panel">
            <div v-if="fileLoading" class="file-loading">加载中...</div>
            <div v-else-if="fileError" class="file-error">{{ fileError }}</div>
            <div v-else-if="fileList.length === 0" class="file-empty">暂无文件，Agent 写入的文件会出现在这里</div>
            <ul v-else class="file-list">
              <li v-for="file in fileList" :key="file.name" class="file-item">
                <button
                  class="file-link"
                  :class="{ active: selectedFile === file.name }"
                  @click="previewFile(file.name)"
                >
                  <span class="file-name">{{ file.name }}</span>
                  <span class="file-meta">{{ formatFileSize(file.size) }} · {{ formatTime(file.modified_at) }}</span>
                </button>
                <button
                  class="file-delete-btn"
                  :disabled="deletingFile === file.name"
                  title="删除文件"
                  @click="deleteFile(file.name)"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="3 6 5 6 21 6"></polyline>
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                  </svg>
                </button>
              </li>
            </ul>
          </div>
          <div class="file-preview-panel">
            <div v-if="!selectedFile" class="file-preview-placeholder">点击左侧文件预览内容</div>
            <div v-else-if="previewLoading" class="file-loading">加载预览...</div>
            <div v-else-if="previewError" class="file-error">{{ previewError }}</div>
            <template v-else>
              <div class="preview-toolbar">
                <span class="preview-title">{{ selectedFile }}</span>
                <button class="download-btn" @click="downloadFile(selectedFile)">下载文件</button>
              </div>
              <div class="preview-content markdown-body" v-html="renderMarkdown(previewContent)"></div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 整体容器 */
.chat-wrapper {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  width: 100%;
  padding: 20px;
  box-sizing: border-box;
  background-image: 
    linear-gradient(rgba(14, 165, 233, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(14, 165, 233, 0.05) 1px, transparent 1px);
  background-size: 40px 40px;
  background-position: center center;
}

.app-layout {
  display: flex;
  height: 100%;
  max-height: 850px;
  width: 100%;
  max-width: 1100px;
  gap: 0;
}

.sidebar {
  width: 240px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(226, 232, 240, 0.8);
  border-radius: 24px 0 0 24px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid #e2e8f0;
}

.sidebar-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: #64748b;
}

.sidebar-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.sidebar-empty {
  padding: 16px;
  text-align: center;
  color: #94a3b8;
  font-size: 0.85rem;
}

.conv-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  width: 100%;
  padding: 10px 12px;
  margin-bottom: 4px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.conv-item:hover {
  background: #f1f5f9;
}

.conv-item.active {
  background: #e0f2fe;
  border-color: #bae6fd;
}

.conv-title {
  font-size: 0.9rem;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.conv-time {
  font-size: 0.7rem;
  color: #94a3b8;
}

.app-layout:not(.sidebar-collapsed) .chat-container {
  border-radius: 0 24px 24px 0;
}

.app-layout.sidebar-collapsed .chat-container {
  border-radius: 24px;
}

.chat-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  flex: 1;
  min-width: 0;
  /* 浅色玻璃拟态：高亮白底 + 模糊 */
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.8);
  border-radius: 24px;
  box-shadow: 
    0 20px 40px rgba(0, 0, 0, 0.08), /* 柔和的阴影 */
    0 0 0 1px rgba(255, 255, 255, 0.5) inset; /* 内发光增强质感 */
  overflow: hidden;
  transition: all 0.3s ease;
  position: relative;
}

/* 顶部装饰线 - 保留但变浅 */
.chat-container::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #38bdf8, #818cf8);
  opacity: 1;
}

/* 头部样式 */
.chat-header {
  padding: 16px 20px;
  background: rgba(255, 255, 255, 0.9);
  border-bottom: 1px solid rgba(226, 232, 240, 0.8);
  display: flex;
  justify-content: space-between;
  align-items: center;
  z-index: 10;
  gap: 12px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
  color: #475569;
  cursor: pointer;
}

.new-chat-btn {
  padding: 6px 12px;
  font-size: 0.85rem;
  font-weight: 500;
  color: #0284c7;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  border-radius: 10px;
  cursor: pointer;
  white-space: nowrap;
}

.new-chat-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.user-email {
  font-size: 0.8rem;
  color: #64748b;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout-btn {
  padding: 6px 12px;
  font-size: 0.8rem;
  color: #64748b;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  cursor: pointer;
}

.header-content {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-dot {
  width: 8px;
  height: 8px;
  background: #10b981; /* 绿色代表在线，更友好 */
  border-radius: 50%;
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.2);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { opacity: 0.6; transform: scale(0.95); }
  50% { opacity: 1; transform: scale(1.05); }
  100% { opacity: 0.6; transform: scale(0.95); }
}

.chat-header h2 {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 700;
  color: #1e293b; /* 深灰黑 */
  letter-spacing: -0.5px;
  font-family: 'Inter', sans-serif;
}

.header-subtitle {
  font-size: 0.75rem;
  color: #64748b;
  font-weight: 600;
  background: #f1f5f9;
  padding: 4px 10px;
  border-radius: 20px; /* 圆润标签 */
  border: 1px solid #e2e8f0;
}

/* 消息列表区域 */
.messages {
  flex: 1;
  padding: 32px;
  overflow-y: auto;
  background: transparent;
  display: flex;
  flex-direction: column;
  gap: 28px;
}

/* 滚动条样式 - 浅色适配 */
.messages::-webkit-scrollbar {
  width: 6px;
}
.messages::-webkit-scrollbar-track {
  background: transparent;
}
.messages::-webkit-scrollbar-thumb {
  background: rgba(203, 213, 225, 0.8);
  border-radius: 3px;
}
.messages::-webkit-scrollbar-thumb:hover {
  background: rgba(148, 163, 184, 0.8);
}

.message-row {
  display: flex;
  gap: 16px;
  max-width: 85%;
  animation: fadeIn 0.4s cubic-bezier(0.2, 0.8, 0.2, 1);
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

.message-row.user {
  flex-direction: row-reverse;
  align-self: flex-end;
}

.avatar {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: #fff;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
}

.message-row.ai .avatar {
  background: #fff;
  color: #0ea5e9;
  border: 1px solid #e0f2fe;
}

.message-row.user .avatar {
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
  color: #fff;
  box-shadow: 0 4px 10px rgba(37, 99, 235, 0.2);
}

.message-bubble {
  padding: 16px 20px;
  border-radius: 16px;
  font-size: 1rem;
  line-height: 1.65;
  position: relative;
  word-break: break-word;
  box-shadow: 0 2px 4px rgba(0,0,0,0.04);
}

.message-row.ai .message-bubble {
  background: #ffffff;
  border: 1px solid #f1f5f9;
  color: #334155;
  border-top-left-radius: 4px;
}

.message-row.user .message-bubble {
  background: linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%);
  color: #ffffff;
  border-top-right-radius: 4px;
  box-shadow: 0 8px 16px -4px rgba(14, 165, 233, 0.3); /* 更柔和的彩色阴影 */
}

/* 思考气泡 */
.thinking-block {
  margin-bottom: 10px;
}

.thinking-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: none;
  background: none;
  color: #94a3b8;
  font-size: 0.85rem;
  cursor: pointer;
}

.thinking-toggle:hover {
  color: #64748b;
}

.thinking-arrow {
  font-size: 0.7rem;
}

.thinking-lines {
  margin-top: 8px;
  max-height: 160px;
  overflow-y: auto;
  padding-left: 4px;
  border-left: 2px solid #e2e8f0;
}

.thinking-line {
  color: #94a3b8;
  font-size: 0.85rem;
  line-height: 1.6;
  padding: 2px 0 2px 10px;
  animation: fadeIn 0.3s ease;
}

@keyframes bounce {
  0%, 80%, 100% {
    transform: scale(0.6);
    opacity: 0.4;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

/* 输入区域 */
.input-area {
  padding: 16px 32px 24px;
  background: rgba(255, 255, 255, 0.9);
  border-top: 1px solid rgba(226, 232, 240, 0.6);
}

.input-toolbar {
  display: flex;
  margin-bottom: 10px;
}

.history-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  font-size: 0.85rem;
  font-weight: 500;
  color: #475569;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.history-btn:hover:not(:disabled) {
  color: #0284c7;
  border-color: #bae6fd;
  background: #f0f9ff;
}

.history-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 模态框 */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.4);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.modal-panel {
  width: 100%;
  max-width: 520px;
  max-height: 70vh;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 20px;
  border: 1px solid rgba(226, 232, 240, 0.8);
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.12);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.modal-panel-wide {
  max-width: 900px;
  max-height: 80vh;
}

.modal-body {
  padding: 16px 24px 24px;
  overflow-y: auto;
}

.modal-split {
  display: flex;
  gap: 16px;
  padding: 0;
  overflow: hidden;
  min-height: 400px;
}

.file-list-panel {
  width: 280px;
  flex-shrink: 0;
  border-right: 1px solid #e2e8f0;
  padding: 16px;
  overflow-y: auto;
}

.file-preview-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 16px 20px;
  overflow: hidden;
  min-width: 0;
}

.file-preview-placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #94a3b8;
  font-size: 0.9rem;
}

.preview-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e2e8f0;
  gap: 12px;
}

.preview-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.download-btn {
  flex-shrink: 0;
  padding: 6px 14px;
  font-size: 0.85rem;
  font-weight: 500;
  color: #0284c7;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.download-btn:hover {
  background: #e0f2fe;
  border-color: #7dd3fc;
}

.preview-content {
  flex: 1;
  overflow-y: auto;
  padding-right: 8px;
}

.file-link.active {
  background: #e0f2fe;
  border-color: #38bdf8;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 24px;
  border-bottom: 1px solid #e2e8f0;
}

.modal-header h3 {
  margin: 0;
  font-size: 1.1rem;
  color: #1e293b;
}

.modal-close {
  background: none;
  border: none;
  font-size: 1.5rem;
  color: #94a3b8;
  cursor: pointer;
  line-height: 1;
  padding: 0 4px;
}

.modal-close:hover {
  color: #475569;
}

.file-loading,
.file-empty,
.file-error {
  text-align: center;
  padding: 32px 16px;
  color: #64748b;
  font-size: 0.9rem;
}

.file-error {
  color: #dc2626;
}

.file-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-item {
  margin: 0;
  display: flex;
  align-items: stretch;
  gap: 8px;
}

.file-link {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 12px 16px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  text-align: left;
}

.file-delete-btn {
  flex-shrink: 0;
  align-self: center;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  background: #fff;
  border: 1px solid #fecaca;
  border-radius: 10px;
  color: #ef4444;
  cursor: pointer;
  transition: all 0.2s ease;
}

.file-delete-btn:hover:not(:disabled) {
  background: #fef2f2;
  border-color: #f87171;
}

.file-delete-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.file-link:hover {
  background: #f0f9ff;
  border-color: #bae6fd;
}

.file-name {
  font-size: 0.95rem;
  font-weight: 500;
  color: #0284c7;
}

.file-meta {
  font-size: 0.75rem;
  color: #94a3b8;
}

.input-box {
  display: flex;
  gap: 12px;
  background: #f8fafc;
  padding: 8px;
  border-radius: 16px;
  border: 1px solid #e2e8f0;
  transition: all 0.3s ease;
  box-shadow: inset 0 2px 4px rgba(0,0,0,0.03);
}

.input-box:focus-within {
  background: #fff;
  border-color: #38bdf8;
  box-shadow: 0 0 0 4px rgba(56, 189, 248, 0.15), 0 4px 12px rgba(0,0,0,0.05);
}

input {
  flex: 1;
  background: transparent;
  border: none;
  padding: 12px 16px;
  color: #1e293b;
  font-size: 1rem;
  outline: none;
}

input::placeholder {
  color: #94a3b8;
}

.send-btn {
  background: #0ea5e9;
  border: none;
  color: white;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 2px 6px rgba(14, 165, 233, 0.2);
}

.send-btn:hover:not(:disabled) {
  background: #0284c7;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(14, 165, 233, 0.3);
}

.send-btn:disabled {
  background: #e2e8f0;
  color: #94a3b8;
  cursor: not-allowed;
  box-shadow: none;
}

/* Markdown 样式适配浅色模式 */
:deep(.markdown-body) {
  color: #334155;
  font-size: 1rem;
  line-height: 1.7;
}

:deep(.markdown-body p) {
  margin-bottom: 0.8em;
}

:deep(.markdown-body a) {
  color: #0284c7;
  text-decoration: none;
  font-weight: 500;
}

:deep(.markdown-body code) {
  background: #f1f5f9;
  padding: 2px 6px;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85em;
  color: #0f172a;
  border: 1px solid #e2e8f0;
}

:deep(.markdown-body pre) {
  background: #1e293b; /* 代码块保持深色，对比度高 */
  padding: 20px;
  border-radius: 12px;
  overflow-x: auto;
  border: 1px solid #334155;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

:deep(.markdown-body pre code) {
  background: transparent;
  color: #e2e8f0;
  border: none;
  padding: 0;
}
</style>
