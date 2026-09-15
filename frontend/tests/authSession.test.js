import assert from 'node:assert/strict'
import test from 'node:test'

import { useAuthSession } from '../src/composables/useAuthSession.js'


test('restore gates the workspace until authentication resolves', async () => {
  const auth = useAuthSession({ client: { me: async () => ({ user: { userId: 'u1', username: 'alice' } }) } })
  assert.equal(auth.status.value, 'loading')
  await auth.restore()
  assert.equal(auth.status.value, 'authenticated')
  assert.equal(auth.user.value.username, 'alice')
})

test('failed restore and logout clear the current user', async () => {
  const client = {
    me: async () => { throw Object.assign(new Error('请先登录'), { status: 401 }) },
    logout: async () => ({ loggedOut: true }),
  }
  const auth = useAuthSession({ client })
  await auth.restore()
  assert.equal(auth.status.value, 'anonymous')
  auth.user.value = { username: 'alice' }
  auth.status.value = 'authenticated'
  await auth.logout()
  assert.equal(auth.user.value, null)
  assert.equal(auth.status.value, 'anonymous')
})

test('register and login expose validation errors without storing tokens', async () => {
  const calls = []
  const client = {
    register: async payload => { calls.push(['register', payload]); return { user: { username: payload.username } } },
    login: async payload => { calls.push(['login', payload]); return { user: { username: payload.username } } },
  }
  const auth = useAuthSession({ client })
  await assert.rejects(() => auth.register('ab', 'correct-horse-battery'), /3 到 40/)
  await auth.register('alice', 'correct-horse-battery')
  assert.equal(auth.status.value, 'authenticated')
  assert.deepEqual(calls[0], ['register', { username: 'alice', password: 'correct-horse-battery' }])
})

test('authentication errors can be cleared when switching forms', async () => {
  const auth = useAuthSession({
    client: { login: async () => { throw new Error('用户名或密码错误') } },
  })
  await assert.rejects(() => auth.login('alice', 'correct-horse-battery'))
  assert.equal(auth.error.value, '用户名或密码错误')
  auth.clearError()
  assert.equal(auth.error.value, '')
})
