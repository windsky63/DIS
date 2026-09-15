import assert from 'node:assert/strict'
import test from 'node:test'

import { renderAssistantMarkdown } from '../src/assistantMarkdown.js'


test('assistant markdown renders structured guidance and safe external links', () => {
  const html = renderAssistantMarkdown(`## 操作步骤

1. **上传图纸**
2. 点击 \`智能编号\`

> 关键结果需要人工复核。

| 组件 | 作用 |
| --- | --- |
| 解析队列 | 查看进度 |

[查看文档](https://example.com/guide)

\`\`\`text
W / V / F / S / M
\`\`\``)

  assert.match(html, /<h2>操作步骤<\/h2>/)
  assert.match(html, /<ol>/)
  assert.match(html, /<strong>上传图纸<\/strong>/)
  assert.match(html, /<code>智能编号<\/code>/)
  assert.match(html, /<blockquote>/)
  assert.match(html, /<table>/)
  assert.match(html, /<pre><code class="language-text">/)
  assert.match(html, /href="https:\/\/example\.com\/guide"/)
  assert.match(html, /target="_blank"/)
  assert.match(html, /rel="noopener noreferrer"/)
})

test('assistant markdown escapes raw HTML and never loads images or unsafe links', () => {
  const html = renderAssistantMarkdown(
    '<script>alert(1)</script>\n\n![远程图片](https://example.com/track.png)\n\n[危险链接](javascript:alert(1))',
  )

  assert.doesNotMatch(html, /<script/i)
  assert.match(html, /&lt;script&gt;/)
  assert.doesNotMatch(html, /<img/i)
  assert.doesNotMatch(html, /href="javascript:/i)
})
