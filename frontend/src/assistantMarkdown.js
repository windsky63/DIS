import MarkdownIt from 'markdown-it'


const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false,
})

markdown.disable('image')
markdown.renderer.rules.link_open = (tokens, index, options, environment, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}

export function renderAssistantMarkdown(content) {
  return markdown.render(String(content || ''))
}
