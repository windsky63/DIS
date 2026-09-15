<script setup>
defineProps({
  status: { type: String, default: '' },
  sources: { type: Array, default: () => [] },
})
</script>

<template>
  <p v-if="status" class="assistant-retrieval-status" role="status" aria-live="polite">
    <span aria-hidden="true" />{{ status }}
  </p>
  <details v-if="sources.length" class="assistant-sources">
    <summary>参考资料（{{ sources.length }}）</summary>
    <ul>
      <li v-for="source in sources" :key="`${source.documentId}:${source.section}:${source.startLine}`">
        <strong>{{ source.title }}</strong>
        <span>{{ source.section }}</span>
        <small>{{ source.path }} · 第 {{ source.startLine }} 行</small>
      </li>
    </ul>
  </details>
</template>

<style scoped>
.assistant-retrieval-status { margin: 8px 0 0; display: flex; align-items: center; gap: 7px; color: #547480; font-size: 11px; }
.assistant-retrieval-status span { width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; background: #2d8b89; box-shadow: 0 0 0 4px rgba(45,139,137,.12); animation: retrieval-pulse 1.25s ease-in-out infinite; }
.assistant-sources { width: min(100%, 560px); margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(64,103,119,.16); color: #526d79; font-size: 11px; }
.assistant-sources summary { width: fit-content; border-radius: 5px; color: #34777a; font-weight: 700; cursor: pointer; }
.assistant-sources summary:focus-visible { outline: 2px solid rgba(45,139,137,.45); outline-offset: 3px; }
.assistant-sources ul { margin: 8px 0 0; padding: 0; display: grid; gap: 7px; list-style: none; }
.assistant-sources li { min-width: 0; padding-left: 10px; display: grid; gap: 2px; border-left: 2px solid rgba(45,139,137,.28); }
.assistant-sources strong, .assistant-sources span, .assistant-sources small { overflow-wrap: anywhere; }
.assistant-sources strong { color: #345563; font-size: 11px; }
.assistant-sources span { color: #617985; }
.assistant-sources small { color: #82949c; font-size: 9px; }
@keyframes retrieval-pulse { 50% { opacity: .45; transform: scale(.82); } }
@media (prefers-reduced-motion: reduce) { .assistant-retrieval-status span { animation: none; } }
</style>
