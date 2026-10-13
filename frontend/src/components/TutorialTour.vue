<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { createTutorialTargetTracker } from '../tutorialTargetTracker.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  steps: { type: Array, default: () => [] },
  tutorialTitle: { type: String, default: '操作教程' },
  targetReady: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'close', 'step-change'])
const currentIndex = ref(0)
const targetRect = ref(null)
const cardStyle = ref({ left: '50%', top: '50%', transform: 'translate(-50%, -50%)' })
const cardElement = ref(null)
let lastTarget
let cardResizeObserver

const currentStep = computed(() => props.steps[currentIndex.value] || {})
const isLastStep = computed(() => currentIndex.value === props.steps.length - 1)

function positionCard(value) {
  lastTarget = value
  if (!value) {
    targetRect.value = null
    cardStyle.value = { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }
    return
  }
  const visibleRect = value.rect
  const padding = 8
  const highlighted = {
    left: Math.max(8, visibleRect.left - padding), top: Math.max(8, visibleRect.top - padding),
    right: Math.min(window.innerWidth - 8, visibleRect.right + padding), bottom: Math.min(window.innerHeight - 8, visibleRect.bottom + padding)
  }
  highlighted.width = highlighted.right - highlighted.left
  highlighted.height = highlighted.bottom - highlighted.top
  targetRect.value = highlighted

  const cardWidth = Math.min(400, window.innerWidth - 32)
  const cardHeight = Math.min(cardElement.value?.offsetHeight || 330, window.innerHeight - 32)
  const gap = 18
  let left = highlighted.right + gap
  let top = Math.max(16, highlighted.top)
  if (left + cardWidth > window.innerWidth - 16) left = highlighted.left - cardWidth - gap
  if (left < 16) {
    left = Math.max(16, Math.min(window.innerWidth - cardWidth - 16, highlighted.left))
    top = highlighted.bottom + gap
    if (top + cardHeight > window.innerHeight - 16) top = highlighted.top - cardHeight - gap
  }
  top = Math.max(16, Math.min(window.innerHeight - cardHeight - 16, top))
  cardStyle.value = { left: `${left}px`, top: `${top}px`, width: `${cardWidth}px` }
}

let scrolledTarget
const tracker = createTutorialTargetTracker({
  isReady: () => props.modelValue && props.targetReady,
  findTarget: () => {
    const target = currentStep.value.target ? document.querySelector(currentStep.value.target) : null
    if (target && target !== scrolledTarget) {
      scrolledTarget = target
      target.scrollIntoView?.({ block: 'nearest', inline: 'nearest', behavior: 'instant' })
    }
    return target
  },
  onTarget: positionCard,
})
function refreshTarget() {
  if (props.modelValue) tracker.start()
}

function close(completed = false) {
  tracker.stop()
  emit('update:modelValue', false)
  emit('close', { completed })
}
function next() {
  if (isLastStep.value) return close(true)
  currentIndex.value += 1
}
function previous() {
  if (currentIndex.value > 0) currentIndex.value -= 1
}
function onKeydown(event) {
  if (!props.modelValue) return
  if (event.key === 'Escape') close(false)
  if (event.key === 'ArrowRight') next()
  if (event.key === 'ArrowLeft') previous()
}

watch(() => props.modelValue, async open => {
  if (open) {
    currentIndex.value = 0
    emit('step-change', { index: 0, step: currentStep.value })
    await nextTick()
    if (props.modelValue) refreshTarget()
  } else tracker.stop()
})
watch(currentIndex, async () => {
  tracker.stop()
  scrolledTarget = null
  positionCard(null)
  emit('step-change', { index: currentIndex.value, step: currentStep.value })
  await nextTick()
  if (props.modelValue) refreshTarget()
})
watch(cardElement, element => {
  cardResizeObserver?.disconnect()
  if (element) cardResizeObserver?.observe(element)
}, { flush: 'post' })
onMounted(() => {
  window.addEventListener('resize', refreshTarget)
  window.addEventListener('keydown', onKeydown)
  cardResizeObserver = new ResizeObserver(() => {
    if (lastTarget && props.modelValue) positionCard(lastTarget)
  })
  if (cardElement.value) cardResizeObserver.observe(cardElement.value)
  if (props.modelValue) {
    emit('step-change', { index: currentIndex.value, step: currentStep.value })
    void nextTick().then(refreshTarget)
  }
})
onBeforeUnmount(() => {
  tracker.stop()
  cardResizeObserver?.disconnect()
  window.removeEventListener('resize', refreshTarget)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="tutorial-tour" role="dialog" aria-modal="true" :aria-label="currentStep.title">
      <div class="tutorial-tour__shade" :class="{ 'tutorial-tour__shade--opaque': !targetRect }" />
      <div v-if="targetRect" class="tutorial-tour__focus" :style="{ left: `${targetRect.left}px`, top: `${targetRect.top}px`, width: `${targetRect.width}px`, height: `${targetRect.height}px` }" />
      <section ref="cardElement" class="tutorial-tour__card" :style="cardStyle">
        <header class="tutorial-tour__header">
          <span class="tutorial-tour__eyebrow">{{ tutorialTitle }} · {{ currentStep.stage || '操作讲解' }} · {{ currentIndex + 1 }} / {{ steps.length }}</span>
          <button type="button" class="tutorial-tour__close" aria-label="退出教程" @click="close(false)">×</button>
        </header>
        <div class="tutorial-tour__progress" aria-hidden="true"><i v-for="(_, index) in steps" :key="index" :class="{ active: index <= currentIndex }" /></div>
        <div class="tutorial-tour__icon" aria-hidden="true">{{ currentStep.icon }}</div>
        <h2>{{ currentStep.title }}</h2>
        <p>{{ currentStep.description }}</p>
        <ul v-if="currentStep.tips?.length"><li v-for="tip in currentStep.tips" :key="tip">{{ tip }}</li></ul>
        <footer>
          <span />
          <button v-if="currentIndex" type="button" class="tutorial-tour__previous" @click="previous">上一步</button>
          <button type="button" class="tutorial-tour__next" @click="next">{{ isLastStep ? '完成教程' : '下一步' }}</button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.tutorial-tour { position: fixed; inset: 0; z-index: 10000; font-family: Arial, 'Microsoft YaHei UI', sans-serif; }
.tutorial-tour__shade { position: absolute; inset: 0; z-index: 10000; }
.tutorial-tour__shade--opaque { background: rgba(8, 25, 36, .72); backdrop-filter: blur(1px); }
.tutorial-tour__focus { position: fixed; z-index: 10001; border: 2px solid #74d5cf; border-radius: 12px; box-shadow: 0 0 0 9999px rgba(8, 25, 36, .72), 0 0 0 4px rgba(116, 213, 207, .2), 0 0 30px rgba(116, 213, 207, .42); pointer-events: none; animation: tutorial-pulse 1.8s ease-in-out infinite; }
.tutorial-tour__card { position: fixed; z-index: 10002; max-height: calc(100vh - 32px); padding: 22px 24px 17px; overflow-y: auto; border: 1px solid rgba(116, 213, 207, .42); border-radius: 14px; background: #fff; color: #304c5d; box-shadow: 0 22px 70px rgba(0, 0, 0, .38); }
.tutorial-tour__header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.tutorial-tour__eyebrow { color: #2d8b89; font-size: 11px; font-weight: 800; letter-spacing: .1em; }
.tutorial-tour__close { width: 30px; height: 30px; border: 0; border-radius: 50%; background: #edf3f5; color: #5d7481; font-size: 22px; line-height: 1; cursor: pointer; }
.tutorial-tour__close:hover { background: #dfe9ec; color: #173e57; }
.tutorial-tour__progress { height: 3px; margin: 13px 0 20px; display: flex; gap: 5px; }
.tutorial-tour__progress i { flex: 1; border-radius: 2px; background: #dce5e8; transition: background .2s ease; }
.tutorial-tour__progress i.active { background: #2d8b89; }
.tutorial-tour__icon { width: 44px; height: 44px; margin-bottom: 13px; display: grid; place-items: center; border-radius: 12px; background: linear-gradient(135deg, #e4f5f3, #edf3f5); font-size: 23px; }
.tutorial-tour h2 { margin: 0 0 9px; color: #173e57; font-size: 21px; line-height: 1.3; }
.tutorial-tour p { margin: 0; color: #526c7a; font-size: 13px; line-height: 1.75; }
.tutorial-tour ul { margin: 12px 0 0; padding-left: 19px; color: #667d89; font-size: 12px; line-height: 1.7; }
.tutorial-tour li::marker { color: #c45d3c; }
.tutorial-tour footer { margin-top: 21px; display: flex; align-items: center; gap: 8px; }
.tutorial-tour footer span { flex: 1; }
.tutorial-tour footer button { min-height: 36px; padding: 0 14px; border-radius: 7px; font-size: 12px; font-weight: 700; cursor: pointer; }
.tutorial-tour__previous { border: 1px solid rgb(var(--v-theme-outline)); background: #fff; color: #496575; }
.tutorial-tour__next { border: 1px solid #2d7778; background: linear-gradient(135deg, #347f83, #2d6f78); color: #fff; box-shadow: 0 5px 13px rgba(45, 111, 120, .22); }
@keyframes tutorial-pulse { 50% { border-color: #a4eee8; } }
@media (max-width: 600px) { .tutorial-tour__card { left: 16px !important; right: 16px; top: auto !important; bottom: 16px; width: auto !important; transform: none !important; } }
</style>
