import assert from 'node:assert/strict'
import test from 'node:test'
import { markerGeometry } from '../src/markerGeometry.js'

test('round and diamond frames stay fixed for long text and larger fonts', () => {
  for (const shape of ['circle', 'diamond', 'split-circle']) {
    const style = { shape, frameSize: 32, fontSize: 12 }
    const short = markerGeometry({ number: '1', secondaryText: '2' }, style)
    for (const item of [{ number: '123456789012345', secondaryText: '2' }, { number: '1', secondaryText: '123456789012345' }]) {
      const long = markerGeometry(item, { ...style, fontSize: 24 })
      assert.equal(long.width, 32)
      assert.equal(long.height, 32)
      if (item.number.length > 1 || shape === 'split-circle') assert.ok(long.fontSize < short.fontSize)
    }
    assert.equal(markerGeometry({ number: '1' }, { ...style, frameSize: 48 }).width, 48)
  }
})
test('rectangle keeps the original content-driven width and independent frame height', () => {
  const style = { shape: 'rectangle', frameSize: 20, fontSize: 12 }
  const short = markerGeometry({ number: '1' }, style)
  const long = markerGeometry({ number: '123456789012345' }, style)
  assert.equal(short.width, 27)
  assert.equal(long.height, 20)
  assert.ok(long.width > short.width)
})
test('missing frame size uses the original fallback and frame limits still apply', () => {
  assert.equal(markerGeometry({ number: '1' }, { shape: 'circle' }).width, 28)
  assert.equal(markerGeometry({ number: '1' }, { frameSize: 999 }).width, 64)
})
