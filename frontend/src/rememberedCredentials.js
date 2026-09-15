const STORAGE_KEY = 'drawing-marker.login-credentials'

const emptyCredentials = () => ({ username: '', password: '', remembered: true })

export function loadRememberedCredentials(storage) {
  if (!storage?.getItem) return emptyCredentials()
  try {
    const saved = JSON.parse(storage.getItem(STORAGE_KEY) || 'null')
    if (!saved || typeof saved.username !== 'string' || typeof saved.password !== 'string') return emptyCredentials()
    return { username: saved.username, password: saved.password, remembered: true }
  } catch {
    return emptyCredentials()
  }
}

export function saveRememberedCredentials(storage, credentials) {
  storage?.setItem?.(STORAGE_KEY, JSON.stringify({
    username: String(credentials?.username || ''),
    password: String(credentials?.password || ''),
  }))
}

export function clearRememberedCredentials(storage) {
  storage?.removeItem?.(STORAGE_KEY)
}
