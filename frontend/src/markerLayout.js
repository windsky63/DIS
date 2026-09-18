const REGULAR_LEADER_MULTIPLIER = 12
const EMERGENCY_LEADER_MULTIPLIER = 18
const MAX_REPAIR_PASSES = 3
const REOPTIMIZE_FRACTION = .2
const LONG_PIPE_MIN_MARKERS = 4
const LONG_PIPE_ANGLE_TOLERANCE = Math.PI / 24
const REFERENCE_DIAMETER = 56.7
const FIXED_LENGTH_FACTORS = [1.25, 1.55, 1.9, 2.35, 2.9, 3.6, 4.5]
const PERIMETER_LENGTH_FACTORS = [120, 150, 180, 220, 270, 330, 400, 500, 620].map(value => value / REFERENCE_DIAMETER)

const overlap = (a, b, padding = 0) => a.left < b.right + padding && a.right > b.left - padding && a.top < b.bottom + padding && a.bottom > b.top - padding
const cross = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
const segmentsIntersect = (a, b) => cross(a.start, a.end, b.start) * cross(a.start, a.end, b.end) < 0 && cross(b.start, b.end, a.start) * cross(b.start, b.end, a.end) < 0

const segmentDistance = (a, b) => segmentsIntersect(a, b) ? 0 : Math.min(pointToSegmentDistance(a.start, b), pointToSegmentDistance(a.end, b), pointToSegmentDistance(b.start, a), pointToSegmentDistance(b.end, a))
function trimSegmentStart(segment, distance) {
  const length = Math.hypot(segment.end.x - segment.start.x, segment.end.y - segment.start.y)
  if (!length || length <= distance) return segment
  const ratio = distance / length
  return { start: { x: segment.start.x + (segment.end.x - segment.start.x) * ratio, y: segment.start.y + (segment.end.y - segment.start.y) * ratio }, end: segment.end }
}

function segmentIntersectsRectangle(segment, rectangle) {
  if ([segment.start, segment.end].some(point => point.x >= rectangle.left && point.x <= rectangle.right && point.y >= rectangle.top && point.y <= rectangle.bottom)) return true
  const points = [{ x: rectangle.left, y: rectangle.top }, { x: rectangle.right, y: rectangle.top }, { x: rectangle.right, y: rectangle.bottom }, { x: rectangle.left, y: rectangle.bottom }]
  return points.some((point, index) => segmentsIntersect(segment, { start: point, end: points[(index + 1) % 4] }))
}

function segmentRectangleDistance(segment, rectangle) {
  if (segmentIntersectsRectangle(segment, rectangle)) return 0
  const points = [{ x: rectangle.left, y: rectangle.top }, { x: rectangle.right, y: rectangle.top }, { x: rectangle.right, y: rectangle.bottom }, { x: rectangle.left, y: rectangle.bottom }]
  return Math.min(...points.map((point, index) => segmentDistance(segment, { start: point, end: points[(index + 1) % 4] })))
}

function pointToSegmentDistance(point, segment) {
  const dx = segment.end.x - segment.start.x
  const dy = segment.end.y - segment.start.y
  const lengthSquared = dx * dx + dy * dy
  if (!lengthSquared) return Math.hypot(point.x - segment.start.x, point.y - segment.start.y)
  const ratio = Math.max(0, Math.min(1, ((point.x - segment.start.x) * dx + (point.y - segment.start.y) * dy) / lengthSquared))
  return Math.hypot(point.x - (segment.start.x + ratio * dx), point.y - (segment.start.y + ratio * dy))
}

function createSegmentIndex(segments, cellSize = 64) {
  const cells = new Map()
  segments.forEach((segment, index) => {
    const left = Math.min(segment.start.x, segment.end.x); const right = Math.max(segment.start.x, segment.end.x)
    const top = Math.min(segment.start.y, segment.end.y); const bottom = Math.max(segment.start.y, segment.end.y)
    for (let column = Math.floor(left / cellSize); column <= Math.floor(right / cellSize); column += 1) {
      for (let row = Math.floor(top / cellSize); row <= Math.floor(bottom / cellSize); row += 1) {
        const key = `${column}:${row}`
        if (!cells.has(key)) cells.set(key, [])
        cells.get(key).push(index)
      }
    }
  })
  return {
    query(left, top, right, bottom) {
      const indexes = new Set()
      for (let column = Math.floor(left / cellSize); column <= Math.floor(right / cellSize); column += 1) {
        for (let row = Math.floor(top / cellSize); row <= Math.floor(bottom / cellSize); row += 1) {
          ;(cells.get(`${column}:${row}`) || []).forEach(index => indexes.add(index))
        }
      }
      return [...indexes].map(index => segments[index])
    },
  }
}

function dimensions(item, resolveStyle) {
  const style = resolveStyle(item) || {}
  const size = Number(style.frameSize) || 28
  const fontSize = Number(style.fontSize) || 15
  const width = style.shape === 'rectangle' ? Math.max(size * 1.35, 10 + String(item.number || '?').length * fontSize * .68) : size
  return { width, height: size, frame: Math.max(width, size) }
}
const layoutBounds = (item, page) => item._layoutBounds || { left: 0, top: 0, right: Number(page.width), bottom: Number(page.height) }

const angleDifference = (a, b) => Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b)))
const axisAngle = angle => {
  let normalized = Math.atan2(Math.sin(angle), Math.cos(angle))
  if (normalized < -Math.PI / 2) normalized += Math.PI
  if (normalized >= Math.PI / 2) normalized -= Math.PI
  return normalized
}
const axisDifference = (a, b) => Math.min(angleDifference(a, b), Math.abs(Math.PI - angleDifference(a, b)))
const family = angle => Math.abs(Math.cos(angle)) > .999 ? 'horizontal' : Math.abs(Math.sin(angle)) > .999 ? 'vertical' : 'diagonal'
function leaderConnection(anchor, center, width, height) {
  const dx = anchor.x - center.x
  const dy = anchor.y - center.y
  if (!dx && !dy) return center
  const factors = []
  if (Math.abs(dx) > 1e-9) factors.push((width / 2) / Math.abs(dx))
  if (Math.abs(dy) > 1e-9) factors.push((height / 2) / Math.abs(dy))
  const factor = Math.min(...factors)
  return { x: center.x + dx * factor, y: center.y + dy * factor }
}
function uniqueAngles(values) {
  return values.filter((angle, index, result) => result.findIndex(other => angleDifference(angle, other) < .035) === index)
}

function nearestPipe(item, pipes, resolveStyle) {
  const anchor = { x: Number(item.x) || 0, y: Number(item.y) || 0 }
  return pipes.reduce((best, pipe, index) => {
    const distance = pointToSegmentDistance(anchor, pipe)
    return !best || distance < best.distance ? { pipe, index, distance } : best
  }, null)
}

function rayDistanceToPage(x, y, dx, dy, page) {
  const distances = []
  if (dx > 1e-6) distances.push((page.width - x) / dx)
  else if (dx < -1e-6) distances.push(-x / dx)
  if (dy > 1e-6) distances.push((page.height - y) / dy)
  else if (dy < -1e-6) distances.push(-y / dy)
  return Math.min(...distances.filter(value => value >= 0))
}

function buildLongPipeGroups(items, pipes, page, resolveStyle) {
  const related = items.map((item, index) => {
    const nearest = nearestPipe(item, pipes, resolveStyle)
    const frame = dimensions(item, resolveStyle).frame
    if (!nearest || nearest.distance > Math.max(18, frame * .9)) return null
    return { item, index, x: Number(item.x) || 0, y: Number(item.y) || 0, axisAngle: axisAngle(Math.atan2(nearest.pipe.end.y - nearest.pipe.start.y, nearest.pipe.end.x - nearest.pipe.start.x)) }
  }).filter(Boolean)
  const clusters = []
  related.forEach(candidate => {
    const match = clusters.find(cluster => {
      if (axisDifference(cluster.axisAngle, candidate.axisAngle) > LONG_PIPE_ANGLE_TOLERANCE) return false
      const normal = { x: -Math.sin(cluster.axisAngle), y: Math.cos(cluster.axisAngle) }
      const offset = Math.abs((candidate.x - cluster.anchorX) * normal.x + (candidate.y - cluster.anchorY) * normal.y)
      return offset <= Math.max(18, dimensions(candidate.item, resolveStyle).frame * .9)
    })
    if (match) match.items.push(candidate)
    else clusters.push({ axisAngle: candidate.axisAngle, anchorX: candidate.x, anchorY: candidate.y, items: [candidate] })
  })
  const groups = new Map()
  let groupIndex = 0
  clusters.forEach(cluster => {
    if (cluster.items.length < LONG_PIPE_MIN_MARKERS) return
    const tangent = { x: Math.cos(cluster.axisAngle), y: Math.sin(cluster.axisAngle) }
    const normal = { x: -tangent.y, y: tangent.x }
    const ordered = cluster.items.map(item => ({ ...item, projection: item.x * tangent.x + item.y * tangent.y })).sort((a, b) => a.projection - b.projection)
    const averageFrame = ordered.reduce((sum, entry) => sum + dimensions(entry.item, resolveStyle).frame, 0) / ordered.length
    if (ordered.at(-1).projection - ordered[0].projection < Math.max(averageFrame * 4, 80)) return
    const center = { x: ordered.reduce((sum, item) => sum + item.x, 0) / ordered.length, y: ordered.reduce((sum, item) => sum + item.y, 0) / ordered.length }
    const preferredSide = rayDistanceToPage(center.x, center.y, normal.x, normal.y, page) >= rayDistanceToPage(center.x, center.y, -normal.x, -normal.y, page) ? 1 : -1
    const laneEnds = [-Infinity, -Infinity]
    ordered.forEach((entry, orderedIndex) => {
      const minimumGap = dimensions(entry.item, resolveStyle).frame + 2
      const eligible = laneEnds.map((end, lane) => entry.projection - end >= minimumGap ? lane : -1).filter(lane => lane >= 0)
      const lane = eligible.length ? eligible[0] : laneEnds[0] <= laneEnds[1] ? 0 : 1
      const requiredProjection = Math.max(entry.projection, laneEnds[lane] + minimumGap)
      laneEnds[lane] = requiredProjection
      groups.set(entry.item, { groupId: `long-pipe-${groupIndex + 1}`, axisAngle: cluster.axisAngle, tangent, normal, preferredSide, lane, orderedIndex, groupSize: ordered.length, tangentShift: requiredProjection - entry.projection })
    })
    groupIndex += 1
  })
  return groups
}

function candidates(item, page, pipes, placed, resolveStyle, emergency, options = {}) {
  const anchor = { x: Number(item.x) || 0, y: Number(item.y) || 0 }
  const original = { x: Number(item.labelX ?? anchor.x), y: Number(item.labelY ?? anchor.y) }
  const size = dimensions(item, resolveStyle)
  const halfWidth = size.width / 2
  const halfHeight = size.height / 2
  const base = Math.hypot(halfWidth, halfHeight) + 12
  const currentAngle = Math.atan2(original.y - anchor.y, original.x - anchor.x)
  const nearest = nearestPipe(item, pipes, resolveStyle)
  const normals = []
  if (nearest && nearest.distance < Math.max(24, size.frame * 1.5)) {
    const pipeAngle = Math.atan2(nearest.pipe.end.y - nearest.pipe.start.y, nearest.pipe.end.x - nearest.pipe.start.x)
    normals.push(pipeAngle - Math.PI / 2, pipeAngle + Math.PI / 2)
  }
  let rawAngles = options.angles
  if (!rawAngles) {
    if (emergency) rawAngles = Array.from({ length: 72 }, (_, index) => -Math.PI + index * 2 * Math.PI / 72)
    else {
      const horizontal = anchor.x < page.width / 2 ? Math.PI : 0
      const vertical = anchor.y < page.height / 2 ? -Math.PI / 2 : Math.PI / 2
      rawAngles = [vertical + (horizontal === Math.PI ? -Math.PI / 4 : Math.PI / 4), vertical, horizontal, currentAngle, ...normals, 0, Math.PI / 2, Math.PI, -Math.PI / 2, Math.PI / 4, 3 * Math.PI / 4, -3 * Math.PI / 4, -Math.PI / 4]
    }
  }
  const angles = uniqueAngles(rawAngles)
  const maximum = size.frame * (emergency ? EMERGENCY_LEADER_MULTIPLIER : REGULAR_LEADER_MULTIPLIER)
  const factors = options.distanceFactors || (emergency ? [13.5, 15, 16.5, 18] : FIXED_LENGTH_FACTORS)
  const rawDistances = factors.map(value => size.frame * value)
  const distances = [...new Set(rawDistances.filter(value => value <= maximum + 1e-6).map(value => Math.max(base, Math.min(maximum, value))))].sort((a, b) => a - b)
  const tracksX = [...new Set(placed.map(entry => Math.round(entry.x * 1000) / 1000))].sort((a, b) => Math.abs(a - anchor.x) - Math.abs(b - anchor.x)).slice(0, 4)
  const tracksY = [...new Set(placed.map(entry => Math.round(entry.y * 1000) / 1000))].sort((a, b) => Math.abs(a - anchor.y) - Math.abs(b - anchor.y)).slice(0, 4)
  const trackGrid = Math.max(4, size.frame * 28 / REFERENCE_DIAMETER)
  const result = []
  const seen = new Set()
  distances.forEach(distance => angles.forEach((angle, direction) => {
    const rawX = anchor.x + Math.cos(angle) * distance
    const rawY = anchor.y + Math.sin(angle) * distance
    const directionFamily = family(angle)
    let variants
    if (directionFamily === 'horizontal') {
      const gridY = Math.round(rawY / trackGrid) * trackGrid
      variants = [[rawX, rawY, 2], ...tracksY.filter(track => Math.abs(track - rawY) <= trackGrid * 2).map(track => [rawX, track, 0]), [rawX, gridY, 1], [rawX, gridY - trackGrid, 1], [rawX, gridY + trackGrid, 1]]
    } else if (directionFamily === 'vertical') {
      const gridX = Math.round(rawX / trackGrid) * trackGrid
      variants = [[rawX, rawY, 2], ...tracksX.filter(track => Math.abs(track - rawX) <= trackGrid * 2).map(track => [track, rawY, 0]), [gridX, rawY, 1], [gridX - trackGrid, rawY, 1], [gridX + trackGrid, rawY, 1]]
    } else {
      const diagonalGrid = trackGrid / 2
      variants = [[Math.round(rawX / diagonalGrid) * diagonalGrid, Math.round(rawY / diagonalGrid) * diagonalGrid, 1], [rawX, rawY, 2]]
    }
    variants.forEach(([candidateX, candidateY, sourcePenalty]) => {
      const bounds = layoutBounds(item, page)
      const x = Math.max(Number(bounds.left) + halfWidth + 5, Math.min(Number(bounds.right) - halfWidth - 5, candidateX))
      const y = Math.max(Number(bounds.top) + halfHeight + 5, Math.min(Number(bounds.bottom) - halfHeight - 5, candidateY))
      const distanceFromAnchor = Math.hypot(x - anchor.x, y - anchor.y)
      const key = `${Math.round(x * 10)}:${Math.round(y * 10)}`
      if (distanceFromAnchor > maximum + 1e-6 || seen.has(key)) return
      seen.add(key)
      result.push({ x, y, rectangle: { left: x - halfWidth, right: x + halfWidth, top: y - halfHeight, bottom: y + halfHeight }, leader: { start: anchor, end: leaderConnection(anchor, { x, y }, size.width, size.height) }, distance: distanceFromAnchor, angle, currentAngle, direction, sourcePenalty, source: options.source || (emergency ? 'non-crossing-emergency' : 'relaxed-direction'), family: family(angle), reusedTrack: sourcePenalty === 0, trackOffset: Math.hypot(x - rawX, y - rawY), pipeAngle: nearest ? Math.atan2(nearest.pipe.end.y - nearest.pipe.start.y, nearest.pipe.end.x - nearest.pipe.start.x) : null })
    })
  }))
  return result
}

function specialCandidate(item, page, resolveStyle, x, y, source, meta = {}) {
  const size = dimensions(item, resolveStyle)
  const anchor = { x: Number(item.x) || 0, y: Number(item.y) || 0 }
  const bounds = layoutBounds(item, page)
  x = Math.max(Number(bounds.left) + size.width / 2 + 5, Math.min(Number(bounds.right) - size.width / 2 - 5, x))
  y = Math.max(Number(bounds.top) + size.height / 2 + 5, Math.min(Number(bounds.bottom) - size.height / 2 - 5, y))
  const distance = Math.hypot(x - anchor.x, y - anchor.y)
  if (distance > size.frame * REGULAR_LEADER_MULTIPLIER + 1e-6) return null
  const angle = Math.atan2(y - anchor.y, x - anchor.x)
  return { x, y, rectangle: { left: x - size.width / 2, right: x + size.width / 2, top: y - size.height / 2, bottom: y + size.height / 2 }, leader: { start: anchor, end: leaderConnection(anchor, { x, y }, size.width, size.height) }, distance, angle, currentAngle: angle, direction: meta.rank || 0, sourcePenalty: 0, source, family: family(angle), reusedTrack: false, trackOffset: 0, ...meta }
}

function localPipeBandCandidates(item, page, pipe, resolveStyle) {
  const frame = dimensions(item, resolveStyle).frame
  const anchor = { x: Number(item.x) || 0, y: Number(item.y) || 0 }
  const pipeAngle = Math.atan2(pipe.end.y - pipe.start.y, pipe.end.x - pipe.start.x)
  const tangent = { x: Math.cos(pipeAngle), y: Math.sin(pipeAngle) }
  const normal = { x: -tangent.y, y: tangent.x }
  const result = []
  let rank = 0
  ;[1.35, 1.65, 2, 2.4, 2.9, 3.5, 4.25].forEach(bandFactor => {
    ;[0, -.56, .56, -1.13, 1.13, -1.7, 1.7, -2.25, 2.25, -3.1, 3.1, -3.95, 3.95].forEach(shiftFactor => {
      ;[-1, 1].forEach(side => {
        const candidate = specialCandidate(item, page, resolveStyle, anchor.x + normal.x * side * frame * bandFactor + tangent.x * frame * shiftFactor, anchor.y + normal.y * side * frame * bandFactor + tangent.y * frame * shiftFactor, 'local-pipe-band', { rank, bandDistance: frame * bandFactor, tangentShift: frame * shiftFactor, bandSide: side, pipeAngle, perpendicularDeviation: Math.atan2(Math.abs(shiftFactor), bandFactor) })
        rank += 1
        if (candidate) result.push(candidate)
      })
    })
  })
  return result
}

function longPipeCandidates(item, page, group, resolveStyle) {
  const frame = dimensions(item, resolveStyle).frame
  const anchor = { x: Number(item.x) || 0, y: Number(item.y) || 0 }
  const laneBase = 2.03 + group.lane * 1.13
  const distances = [...new Set([1.35, 1.65, laneBase, laneBase + .42, laneBase + .88, 3.5, 4.25])].sort((a, b) => a - b)
  const baseShift = group.tangentShift / Math.max(1, frame)
  const shifts = [...new Set([baseShift, 0, baseShift - .25, baseShift + .25, baseShift - .5, baseShift + .5])]
  const result = []
  let rank = 0
  ;[group.preferredSide, -group.preferredSide].forEach(side => distances.forEach(distance => shifts.forEach(shift => {
    const candidate = specialCandidate(item, page, resolveStyle, anchor.x + group.normal.x * side * frame * distance + group.tangent.x * frame * shift, anchor.y + group.normal.y * side * frame * distance + group.tangent.y * frame * shift, 'long-pipe-group', { rank, bandDistance: frame * distance, tangentShift: frame * shift, longPipeGroupId: group.groupId, longPipeLane: group.lane, longPipeSide: side, preferredLongPipeSide: group.preferredSide, pipeAngle: group.axisAngle, perpendicularDeviation: Math.atan2(Math.abs(shift), distance) })
    rank += 1
    if (candidate) result.push(candidate)
  })))
  return result
}

function perimeterCandidates(item, page, placed, focusBounds, resolveStyle) {
  const anchor = { x: Number(item.x) || 0, y: Number(item.y) || 0 }
  const center = { x: (focusBounds.left + focusBounds.right) / 2, y: (focusBounds.top + focusBounds.bottom) / 2 }
  const radial = Math.atan2(anchor.y - center.y, anchor.x - center.x)
  return candidates(item, page, [], placed, resolveStyle, false, { angles: [radial, radial - Math.PI / 8, radial + Math.PI / 8, radial - Math.PI / 4, radial + Math.PI / 4], distanceFactors: PERIMETER_LENGTH_FACTORS, source: 'perimeter' })
}

function evaluate(candidate, item, entries, anchors, texts, pipes, graphics) {
  let label = 0; let anchor = 0; let leaderLabel = 0; let leaderCross = 0; let leaderAnchor = 0
  const frame = candidate.rectangle.bottom - candidate.rectangle.top
  const leaderLabelGap = Math.max(2, frame * 8 / REFERENCE_DIAMETER)
  const leaderLeaderGap = Math.max(3, frame * 18 / REFERENCE_DIAMETER)
  const trimmedLeader = trimSegmentStart(candidate.leader, frame * 20 / REFERENCE_DIAMETER)
  entries.forEach(entry => {
    if (entry.id === item.id) return
    label += overlap(candidate.rectangle, entry.rectangle, 2) ? 1 : 0
    leaderLabel += segmentRectangleDistance(candidate.leader, entry.rectangle) < leaderLabelGap ? 1 : 0
    leaderLabel += segmentRectangleDistance(entry.leader, candidate.rectangle) < leaderLabelGap ? 1 : 0
    leaderCross += segmentsIntersect(candidate.leader, entry.leader) || segmentDistance(trimmedLeader, trimSegmentStart(entry.leader, frame * 20 / REFERENCE_DIAMETER)) < leaderLeaderGap ? 1 : 0
  })
  anchors.forEach(point => {
    if (point.id === item.id) return
    anchor += point.x > candidate.rectangle.left - 7 && point.x < candidate.rectangle.right + 7 && point.y > candidate.rectangle.top - 7 && point.y < candidate.rectangle.bottom + 7 ? 1 : 0
    leaderAnchor += pointToSegmentDistance(point, candidate.leader) < 4 ? 1 : 0
  })
  const text = texts.filter(rectangle => overlap(candidate.rectangle, rectangle, leaderLabelGap)).length
  const expanded = { left: candidate.rectangle.left - 2, right: candidate.rectangle.right + 2, top: candidate.rectangle.top - 2, bottom: candidate.rectangle.bottom + 2 }
  const pipe = pipes.filter(segment => segmentIntersectsRectangle(segment, expanded)).length
  const nearbyGraphics = graphics.query(Math.min(expanded.left, candidate.leader.start.x, candidate.leader.end.x) - 6, Math.min(expanded.top, candidate.leader.start.y, candidate.leader.end.y) - 6, Math.max(expanded.right, candidate.leader.start.x, candidate.leader.end.x) + 6, Math.max(expanded.bottom, candidate.leader.start.y, candidate.leader.end.y) + 6)
  const graphic = nearbyGraphics.filter(segment => segmentIntersectsRectangle(segment, expanded)).length
  const graphicClutter = nearbyGraphics.filter(segment => segmentRectangleDistance(segment, candidate.rectangle) < 7).length
  const hard = [label + anchor + text + pipe + graphic, leaderLabel + leaderCross]
  const pipeClearance = pipes.filter(segment => pointToSegmentDistance({ x: candidate.x, y: candidate.y }, segment) < Math.max(8, (candidate.rectangle.bottom - candidate.rectangle.top) / 2 + 6)).length
  const leaderText = texts.filter(rectangle => segmentIntersectsRectangle(candidate.leader, rectangle)).length
  const leaderPipe = pipes.filter(segment => segmentDistance(trimmedLeader, segment) < Math.max(3, frame * 10 / REFERENCE_DIAMETER)).length
  const leaderGraphic = nearbyGraphics.filter(segment => segmentsIntersect(trimmedLeader, segment)).length
  const sourcePenalty = { 'long-pipe-group': -35, 'local-pipe-band': -22, 'segment-normal': -15, 'nearby-free-space': -5, perimeter: -8, 'relaxed-direction': 0, 'non-crossing-emergency': 40 }[candidate.source] || 0
  const soft = candidate.distance + angleDifference(candidate.angle, candidate.currentAngle) * 5 + pipeClearance * 25 + leaderPipe * 12 + leaderAnchor * 12 + leaderText * 8 + leaderGraphic * 3 + graphicClutter * 10 + candidate.sourcePenalty + Math.abs(candidate.trackOffset || 0) * 2.4 + (candidate.reusedTrack ? 0 : 3) + sourcePenalty + candidate.direction * .001
  const hardCollisionSummary = { leaderIntersection: leaderCross, leaderLeader: leaderCross, leaderLabel, labelLabel: label + anchor, labelText: text, labelGraphic: pipe + graphic, pageBounds: 0, collisionViolationCount: label + anchor + leaderLabel + leaderCross, constraintViolationCount: text + pipe + graphic }
  hardCollisionSummary.total = hardCollisionSummary.collisionViolationCount + hardCollisionSummary.constraintViolationCount
  return { hard, hardCount: hard[0] + hard[1], soft, visualClutter: graphicClutter, hardCollisionSummary, collisionViolationCount: hardCollisionSummary.collisionViolationCount, constraintViolationCount: hardCollisionSummary.constraintViolationCount }
}

const compare = (a, b) => a.hard[0] - b.hard[0] || a.hard[1] - b.hard[1] || a.soft - b.soft
function evaluatedCandidates(pool, item, placed, anchors, texts, pipes, graphics) {
  const seen = new Set()
  return pool.filter(candidate => {
    const key = `${candidate.x.toFixed(1)}:${candidate.y.toFixed(1)}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  }).map(candidate => Object.assign(candidate, evaluate(candidate, item, placed, anchors, texts, pipes, graphics)))
}
const chooseCollisionFree = pool => pool.filter(candidate => candidate.hardCount === 0).sort((a, b) => (a.visualClutter || 0) - (b.visualClutter || 0) || a.distance - b.distance || a.soft - b.soft || a.direction - b.direction)[0] || null
function chooseFallback(pool) {
  const key = candidate => { const summary = candidate.hardCollisionSummary; return [summary.pageBounds, summary.labelGraphic + summary.labelText + summary.labelLabel, summary.leaderLabel, summary.leaderLeader, candidate.soft, candidate.distance, candidate.direction] }
  const nonCrossing = pool.filter(candidate => !candidate.hardCollisionSummary.leaderIntersection)
  return (nonCrossing.length ? nonCrossing : pool).sort((a, b) => { const first = key(a); const second = key(b); for (let i = 0; i < first.length; i += 1) if (first[i] !== second[i]) return first[i] - second[i]; return 0 })[0] || null
}
const markSelection = (candidate, selectionStage, usedFallback = false) => candidate ? Object.assign(candidate, { selectionStage, usedFallback, isCollisionFree: candidate.hardCount === 0 }) : null
function bestPosition(item, page, pipes, placed, anchors, texts, graphics, resolveStyle, group = null, usePerimeter = false, focusBounds = null) {
  const nearest = nearestPipe(item, pipes, resolveStyle)
  const all = []
  if (group) {
    const pool = evaluatedCandidates(longPipeCandidates(item, page, group, resolveStyle), item, placed, anchors, texts, pipes, graphics); all.push(...pool)
    const frame = dimensions(item, resolveStyle).frame
    const best = chooseCollisionFree(pool.filter(candidate => candidate.bandDistance <= frame * 3.2 && candidate.distance <= frame * 3.5))
    if (best) return markSelection(best, 'long-pipe-group')
  }
  if (usePerimeter && nearest) {
    const local = evaluatedCandidates(localPipeBandCandidates(item, page, nearest.pipe, resolveStyle), item, placed, anchors, texts, pipes, graphics); all.push(...local)
    const frame = dimensions(item, resolveStyle).frame
    const compact = local.filter(candidate => candidate.bandDistance <= frame * 3.2 && Math.abs(candidate.tangentShift) <= frame * 1.7)
    const compactBest = chooseCollisionFree(compact); if (compactBest) return markSelection(compactBest, 'local-pipe-band-compact')
    const freeAngles = Array.from({ length: 24 }, (_, index) => -Math.PI + index * 2 * Math.PI / 24)
    const nearby = evaluatedCandidates(candidates(item, page, [], placed, resolveStyle, false, { angles: freeAngles, source: 'nearby-free-space' }), item, placed, anchors, texts, pipes, graphics); all.push(...nearby)
    const nearbyBest = chooseCollisionFree([...local, ...nearby])
    if (nearbyBest) return markSelection(nearbyBest, nearbyBest.source === 'nearby-free-space' ? 'nearby-free-space' : 'local-pipe-band')
  }
  if (usePerimeter && focusBounds) {
    const pool = evaluatedCandidates(perimeterCandidates(item, page, placed, focusBounds, resolveStyle), item, placed, anchors, texts, pipes, graphics); all.push(...pool)
    const best = chooseCollisionFree(pool); if (best) return markSelection(best, 'perimeter-distribution')
  }
  if (!usePerimeter) {
    if (nearest && nearest.distance < Math.max(24, dimensions(item, resolveStyle).frame * 1.5)) {
      const angle = Math.atan2(nearest.pipe.end.y - nearest.pipe.start.y, nearest.pipe.end.x - nearest.pipe.start.x)
      const strict = evaluatedCandidates(candidates(item, page, pipes, placed, resolveStyle, false, { angles: [angle - Math.PI / 2, angle + Math.PI / 2], source: 'segment-normal' }), item, placed, anchors, texts, pipes, graphics); all.push(...strict)
      const best = chooseCollisionFree(strict); if (best) return markSelection(best, 'strict-perpendicular')
    }
    const relaxed = evaluatedCandidates(candidates(item, page, [], placed, resolveStyle, false, { source: 'relaxed-direction' }), item, placed, anchors, texts, pipes, graphics); all.push(...relaxed)
    const best = chooseCollisionFree(relaxed); if (best) return markSelection(best, 'relaxed-direction')
  }
  const emergency = evaluatedCandidates(candidates(item, page, pipes, placed, resolveStyle, true, { source: 'non-crossing-emergency' }), item, placed, anchors, texts, pipes, graphics); all.push(...emergency)
  const bestEmergency = chooseCollisionFree(emergency)
  return bestEmergency ? markSelection(bestEmergency, 'non-crossing-emergency') : markSelection(chooseFallback(all), 'fallback-best-effort', true)
}

function candidateAt(item, x, y, resolveStyle) {
  const size = dimensions(item, resolveStyle)
  const anchor = { x: Number(item.x) || 0, y: Number(item.y) || 0 }
  const bounds = item._layoutBounds
  if (bounds && !(Number(bounds.left) + size.width/2 + 5 <= x && x <= Number(bounds.right) - size.width/2 - 5 && Number(bounds.top) + size.height/2 + 5 <= y && y <= Number(bounds.bottom) - size.height/2 - 5)) return null
  const distance = Math.hypot(x - anchor.x, y - anchor.y)
  if (distance > size.frame * EMERGENCY_LEADER_MULTIPLIER + 1e-6) return null
  const angle = Math.atan2(y - anchor.y, x - anchor.x)
  return { x, y, rectangle: { left: x - size.width / 2, right: x + size.width / 2, top: y - size.height / 2, bottom: y + size.height / 2 }, leader: { start: anchor, end: leaderConnection(anchor, { x, y }, size.width, size.height) }, distance, angle, currentAngle: angle, sourcePenalty: 0, direction: 0, source: 'endpoint-swap', family: family(angle), reusedTrack: false, trackOffset: 0 }
}

function untangleCrossedLeaders(entries, anchors, texts, pipes, graphics, resolveStyle) {
  let changed = false
  for (let firstIndex = 0; firstIndex < entries.length; firstIndex += 1) {
    for (let secondIndex = firstIndex + 1; secondIndex < entries.length; secondIndex += 1) {
      const first = entries[firstIndex]
      const second = entries[secondIndex]
      if (!segmentsIntersect(first.leader, second.leader)) continue
      const swappedFirst = candidateAt(first.item, second.x, second.y, resolveStyle)
      const swappedSecond = candidateAt(second.item, first.x, first.y, resolveStyle)
      if (!swappedFirst || !swappedSecond) continue
      Object.assign(swappedFirst, { id: first.id, item: first.item })
      Object.assign(swappedSecond, { id: second.id, item: second.item })
      const others = entries.filter((_, index) => index !== firstIndex && index !== secondIndex)
      const oldStates = [evaluate(first, first.item, entries, anchors, texts, pipes, graphics), evaluate(second, second.item, entries, anchors, texts, pipes, graphics)]
      const newStates = [evaluate(swappedFirst, first.item, [...others, swappedSecond], anchors, texts, pipes, graphics), evaluate(swappedSecond, second.item, [...others, swappedFirst], anchors, texts, pipes, graphics)]
      const key = states => [states.reduce((sum, state) => sum + state.hard[0], 0), states.reduce((sum, state) => sum + state.hard[1], 0), states.reduce((sum, state) => sum + state.soft, 0)]
      const oldKey = key(oldStates)
      const newKey = key(newStates)
      if (newKey[0] < oldKey[0] || (newKey[0] === oldKey[0] && (newKey[1] < oldKey[1] || (newKey[1] === oldKey[1] && newKey[2] < oldKey[2])))) {
        Object.assign(swappedFirst, newStates[0], { selectionStage: 'endpoint-swap', usedFallback: false, isCollisionFree: newStates[0].hardCount === 0 })
        Object.assign(swappedSecond, newStates[1], { selectionStage: 'endpoint-swap', usedFallback: false, isCollisionFree: newStates[1].hardCount === 0 })
        entries[firstIndex] = swappedFirst
        entries[secondIndex] = swappedSecond
        changed = true
      }
    }
  }
  return changed
}

export function reflowLabelPositions(pageList, resolveStyle) {
  const totals = { moved: 0, placed: 0, remainingCollisions: 0, repairPasses: 0 }
  ;(pageList || []).forEach(page => {
    const items = (page.candidates || []).filter(item => item.included !== false).slice()
    const anchors = items.map(item => ({ x: Number(item.x) || 0, y: Number(item.y) || 0, id: item.id }))
    const texts = (page.layoutObstacles?.textRects || []).map(box => ({ left: Number(box[0]), top: Number(box[1]), right: Number(box[2]), bottom: Number(box[3]) }))
    const pipes = (page.layoutObstacles?.processSegments || []).map(segment => ({ start: { x: Number(segment.start?.[0]), y: Number(segment.start?.[1]) }, end: { x: Number(segment.end?.[0]), y: Number(segment.end?.[1]) } }))
    const graphics = createSegmentIndex((page.layoutObstacles?.graphicSegments || []).map(segment => ({ start: { x: Number(segment.start?.[0]), y: Number(segment.start?.[1]) }, end: { x: Number(segment.end?.[0]), y: Number(segment.end?.[1]) } })))
    const region = page.layoutObstacles?.mainGraphicRegion || { left: 0, top: 0, right: Number(page.width), bottom: Number(page.height) }
    items.forEach(item => { item._layoutBounds = region })
    const focusBounds = anchors.length
      ? { left: Math.min(...anchors.map(point => point.x)), top: Math.min(...anchors.map(point => point.y)), right: Math.max(...anchors.map(point => point.x)), bottom: Math.max(...anchors.map(point => point.y)) }
      : { left: 0, top: 0, right: 0, bottom: 0 }
    const usePerimeter = items.length >= 8 && (focusBounds.right - focusBounds.left >= 160 || focusBounds.bottom - focusBounds.top >= 160)
    const longPipeGroups = buildLongPipeGroups(items, pipes, page, resolveStyle)
    const originals = new Map(items.map(item => [item, { x: Number(item.labelX ?? item.x), y: Number(item.labelY ?? item.y) }]))
    const congestion = item => {
      const point = { x: Number(item.x) || 0, y: Number(item.y) || 0 }
      return anchors.filter(other => other.id !== item.id && Math.hypot(other.x - point.x, other.y - point.y) < 120).length * 8
        + texts.filter(rectangle => point.x > rectangle.left - 70 && point.x < rectangle.right + 70 && point.y > rectangle.top - 70 && point.y < rectangle.bottom + 70).length * 3
        + pipes.filter(pipe => pointToSegmentDistance(point, pipe) < 50).length
    }
    items.sort((a, b) => congestion(b) - congestion(a) || Number(a.y) - Number(b.y) || Number(a.x) - Number(b.x))
    const entries = []
    items.forEach(item => {
      const best = bestPosition(item, page, pipes, entries, anchors, texts, graphics, resolveStyle, longPipeGroups.get(item), usePerimeter, focusBounds)
      if (best) entries.push(Object.assign(best, { id: item.id, item }))
    })
    for (let pass = 0; pass < MAX_REPAIR_PASSES; pass += 1) {
      const untangled = untangleCrossedLeaders(entries, anchors, texts, pipes, graphics, resolveStyle)
      const conflicted = entries.map(entry => ({ entry, state: evaluate(entry, entry.item, entries, anchors, texts, pipes, graphics) })).filter(value => value.state.hardCount).sort((a, b) => b.state.hardCount - a.state.hardCount)
      if (!conflicted.length) break
      totals.repairPasses = Math.max(totals.repairPasses, pass + 1)
      let changed = untangled
      conflicted.slice(0, Math.max(1, Math.ceil(entries.length * REOPTIMIZE_FRACTION))).forEach(({ entry }) => {
        const others = entries.filter(other => other !== entry)
        const replacement = bestPosition(entry.item, page, pipes, others, anchors, texts, graphics, resolveStyle, longPipeGroups.get(entry.item), usePerimeter, focusBounds)
        const oldState = evaluate(entry, entry.item, others, anchors, texts, pipes, graphics)
        if (replacement && compare(replacement, oldState) < 0) {
          Object.assign(replacement, { id: entry.id, item: entry.item })
          entries[entries.indexOf(entry)] = replacement
          changed = true
        }
      })
      if (!changed) break
    }
    for (let pass = 0; pass < 2; pass += 1) {
      let changed = false
      ;[...entries].sort((a, b) => b.distance - a.distance).forEach(entry => {
        const others = entries.filter(other => other !== entry)
        const replacement = bestPosition(entry.item, page, pipes, others, anchors, texts, graphics, resolveStyle, longPipeGroups.get(entry.item), usePerimeter, focusBounds)
        if (!replacement) return
        const oldState = evaluate(entry, entry.item, others, anchors, texts, pipes, graphics)
        const newKey = [replacement.hardCount, replacement.visualClutter || 0, Math.round(replacement.distance * 1e6), replacement.soft]
        const oldKey = [oldState.hardCount, oldState.visualClutter || 0, Math.round(entry.distance * 1e6), oldState.soft]
        const better = newKey.some((value, index) => value !== oldKey[index] && newKey.slice(0, index).every((prior, priorIndex) => prior === oldKey[priorIndex]) && value < oldKey[index])
        if (better) {
          Object.assign(replacement, { id: entry.id, item: entry.item, selectionStage: `neighbourhood-${replacement.selectionStage || 'compact'}` })
          entries[entries.indexOf(entry)] = replacement
          changed = true
        }
      })
      if (!changed) break
    }
    entries.forEach(entry => {
      const item = entry.item
      const finalState = evaluate(entry, item, entries, anchors, texts, pipes, graphics)
      Object.assign(item, { labelX: entry.x, labelY: entry.y, labelXNorm: entry.x / page.width, labelYNorm: entry.y / page.height,
        layoutDiagnostics: { family: entry.family || '', source: entry.source || '', lineLength: entry.distance, selectionStage: entry.selectionStage || 'fallback-best-effort', usedFallback: entry.usedFallback === true, isCollisionFree: finalState.hardCount === 0, hardCollisionCount: finalState.hardCount, leaderIntersectionCount: finalState.hardCollisionSummary.leaderIntersection, collisionViolationCount: finalState.collisionViolationCount, constraintViolationCount: finalState.constraintViolationCount, hardCollisionSummary: finalState.hardCollisionSummary, penalty: finalState.soft, reusedTrack: entry.reusedTrack === true, trackOffset: entry.trackOffset || 0, longPipeGroupId: entry.longPipeGroupId || '', longPipeLane: entry.longPipeLane ?? -1, pipeAngleDegrees: Number.isFinite(entry.pipeAngle) ? Math.round(entry.pipeAngle * 1800 / Math.PI) / 10 : null, perpendicularDeviationDegrees: Number.isFinite(entry.perpendicularDeviation) ? Math.round(entry.perpendicularDeviation * 1800 / Math.PI) / 10 : null } })
      totals.placed += 1
      if (Math.hypot(entry.x - originals.get(item).x, entry.y - originals.get(item).y) > 2) totals.moved += 1
      totals.remainingCollisions += finalState.hardCount
      delete item._layoutBounds
    })
  })
  return totals
}

export function reflowCurrentPageLabelPositions(page, resolveStyle) {
  return reflowLabelPositions(page ? [page] : [], resolveStyle)
}
