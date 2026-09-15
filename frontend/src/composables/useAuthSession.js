import { ref } from 'vue'


export function useAuthSession({ client }) {
  const status = ref('loading')
  const user = ref(null)
  const error = ref('')
  const submitting = ref(false)

  function validate(username, password) {
    const cleanUsername = String(username || '').trim()
    if (cleanUsername.length < 3 || cleanUsername.length > 40) throw new Error('用户名长度必须为 3 到 40 个字符')
    if (String(password || '').length < 6 || String(password || '').length > 128) throw new Error('密码长度必须为 6 到 128 个字符')
    return { username: cleanUsername, password: String(password) }
  }

  async function restore() {
    status.value = 'loading'
    error.value = ''
    try {
      const response = await client.me()
      user.value = response.user
      status.value = 'authenticated'
    } catch (cause) {
      user.value = null
      status.value = 'anonymous'
      if (cause?.status !== 401) error.value = cause?.message || String(cause)
    }
  }

  async function submit(method, username, password) {
    error.value = ''
    submitting.value = true
    try {
      const response = await client[method](validate(username, password))
      user.value = response.user
      status.value = 'authenticated'
      return response.user
    } catch (cause) {
      error.value = cause?.message || String(cause)
      throw cause
    } finally {
      submitting.value = false
    }
  }

  const login = (username, password) => submit('login', username, password)
  const register = (username, password) => submit('register', username, password)

  async function logout() {
    try { await client.logout() } finally {
      user.value = null
      status.value = 'anonymous'
      error.value = ''
    }
  }

  function clearError() { error.value = '' }

  return { status, user, error, submitting, restore, login, register, logout, clearError }
}
