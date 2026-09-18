function samePoint(left, right) {
  return Array.isArray(left) && Array.isArray(right)
    && Number(left[0]) === Number(right[0])
    && Number(left[1]) === Number(right[1])
}

export function sameReferenceHint(left, right) {
  return Boolean(left && right)
    && String(left.file || '') === String(right.file || '')
    && Number(left.page) === Number(right.page)
    && String(left.label || '') === String(right.label || '')
    && String(left.type || '') === String(right.type || '')
    && samePoint(left.point, right.point)
}

export function referenceHintClickDecision(currentFocus, nextFocus) {
  return {
    focus: nextFocus,
    locate: sameReferenceHint(currentFocus, nextFocus),
  }
}
