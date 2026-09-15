import assert from 'node:assert/strict'
import test from 'node:test'

import { validateAuthForm } from '../src/authFormValidation.js'


test('login form reports username and password requirements', () => {
  assert.deepEqual(validateAuthForm({ mode: 'login', username: 'ab', password: 'short' }), {
    username: '用户名长度必须为 3 到 40 个字符',
    password: '密码长度必须为 6 到 128 个字符',
    confirmPassword: '',
    valid: false,
  })
})

test('six-character password satisfies the minimum length', () => {
  assert.equal(validateAuthForm({ mode: 'login', username: 'admin', password: '123456' }).valid, true)
})

test('registration requires matching password confirmation', () => {
  assert.equal(validateAuthForm({
    mode: 'register', username: 'alice', password: 'correct-horse', confirmPassword: 'different-value',
  }).confirmPassword, '两次输入的密码不一致')
  assert.equal(validateAuthForm({
    mode: 'register', username: 'alice', password: 'correct-horse', confirmPassword: 'correct-horse',
  }).valid, true)
})
