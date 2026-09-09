export async function readEditablePdfAttachments(document) {
  const attachments = await document?.getAttachments?.()
  const entries = attachments instanceof Map ? [...attachments.entries()] : Object.entries(attachments || {})
  let embedded = null
  let sourceContent = null

  for (const [attachmentName, attachment] of entries) {
    let content = attachment?.content || null
    if (!content && document?.getAttachmentContent) content = await document.getAttachmentContent(attachmentName).catch(() => null)
    if (!content) continue
    const fileName = String(attachment?.filename || '').toLowerCase()
    const storageName = String(attachmentName || '').toLowerCase()
    if (fileName.endsWith('weld-marker-source.pdf') || storageName.endsWith('weld-marker-source.pdf')) {
      sourceContent = content
      continue
    }
    try {
      const parsed = JSON.parse(new TextDecoder().decode(content))
      if (parsed?.schema === 'weld-marker.editable.v1' && parsed.pages?.length) embedded = parsed
    } catch { /* attachment is not an editable workspace */ }
  }
  return { embedded, sourceContent }
}
