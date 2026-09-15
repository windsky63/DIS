export function validateAuthForm({ mode, username, password, confirmPassword = '' }) {
  const cleanUsername = String(username || '').trim()
  const passwordValue = String(password || '')
  const errors = {
    username: cleanUsername.length >= 3 && cleanUsername.length <= 40 ? '' : '用户名长度必须为 3 到 40 个字符',
    password: passwordValue.length >= 6 && passwordValue.length <= 128 ? '' : '密码长度必须为 6 到 128 个字符',
    confirmPassword: mode === 'register' && passwordValue !== String(confirmPassword || '') ? '两次输入的密码不一致' : '',
  }
  return { ...errors, valid: !errors.username && !errors.password && !errors.confirmPassword }
}
