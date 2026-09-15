<script setup>
defineProps({
  modelValue: { type: Boolean, default: false },
  tutorials: { type: Array, default: () => [] },
  loadingId: { type: String, default: '' },
})
defineEmits(['update:modelValue', 'start'])
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="780" @update:model-value="$emit('update:modelValue', $event)">
    <v-card class="tutorial-catalog-card">
      <div class="tutorial-catalog-header">
        <div class="tutorial-catalog-header__icon" aria-hidden="true">◉</div>
        <div><v-card-title>操作教程</v-card-title><v-card-subtitle>根据当前要完成的工作，选择一个分阶段专题教程。</v-card-subtitle></div>
        <v-spacer />
        <v-btn icon variant="text" aria-label="关闭教程目录" @click="$emit('update:modelValue', false)">×</v-btn>
      </div>
      <v-card-text class="tutorial-catalog-grid">
        <button v-for="tutorial in tutorials" :key="tutorial.id" type="button" class="tutorial-catalog-item" :disabled="Boolean(loadingId)" @click="$emit('start', tutorial)">
          <span class="tutorial-catalog-item__icon" aria-hidden="true">{{ tutorial.icon }}</span>
          <span class="tutorial-catalog-item__copy"><strong>{{ tutorial.title }}</strong><small>{{ tutorial.description }}</small></span>
          <span class="tutorial-catalog-item__meta"><i>{{ tutorial.level }}</i><small>{{ tutorial.steps }} 步 · {{ tutorial.duration }}</small></span>
          <v-progress-circular v-if="loadingId === tutorial.id" indeterminate color="secondary" size="22" width="2" />
          <span v-else class="tutorial-catalog-item__arrow" aria-hidden="true">›</span>
        </button>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.tutorial-catalog-card { overflow: hidden; border: 1px solid #d9e4e7; border-radius: 14px !important; }
.tutorial-catalog-header { padding: 20px 22px 15px; display: flex; align-items: center; gap: 13px; border-bottom: 1px solid #e4ebee; background: linear-gradient(135deg, #f7fbfb, #fff); }
.tutorial-catalog-header__icon { width: 42px; height: 42px; display: grid; place-items: center; border-radius: 12px; background: linear-gradient(145deg, #dff3f1, #edf3f5); color: #2d7778; font-size: 22px; }
.tutorial-catalog-header .v-card-title, .tutorial-catalog-header .v-card-subtitle { padding: 0; }
.tutorial-catalog-header .v-card-title { color: #173e57; font-size: 19px; font-weight: 800; }
.tutorial-catalog-header .v-card-subtitle { margin-top: 3px; color: #71838d; font-size: 11px; }
.tutorial-catalog-grid { padding: 18px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; background: #f6f9fa; }
.tutorial-catalog-item { min-height: 128px; padding: 16px; display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; grid-template-rows: 1fr auto; gap: 7px 12px; border: 1px solid #dfe8eb; border-radius: 11px; background: #fff; color: inherit; text-align: left; cursor: pointer; transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease; }
.tutorial-catalog-item:hover:not(:disabled) { border-color: #79b9b5; box-shadow: 0 9px 24px rgba(37, 91, 98, .12); transform: translateY(-1px); }
.tutorial-catalog-item:disabled { cursor: wait; opacity: .72; }
.tutorial-catalog-item__icon { grid-row: 1 / 3; width: 42px; height: 42px; display: grid; place-items: center; border-radius: 10px; background: #eaf5f4; font-size: 21px; }
.tutorial-catalog-item__copy { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.tutorial-catalog-item__copy strong { color: #274d60; font-size: 14px; }
.tutorial-catalog-item__copy small { color: #71838d; font-size: 10px; line-height: 1.55; }
.tutorial-catalog-item__meta { display: flex; align-items: center; gap: 8px; }
.tutorial-catalog-item__meta i { padding: 2px 7px; border-radius: 10px; background: #fff1ec; color: #a54d39; font-size: 9px; font-style: normal; }
.tutorial-catalog-item__meta small { color: #8a9aa2; font-size: 9px; }
.tutorial-catalog-item__arrow { grid-column: 3; grid-row: 1 / 3; align-self: center; color: #6ba39f; font-size: 26px; }
@media (max-width: 650px) { .tutorial-catalog-grid { grid-template-columns: 1fr; } }
</style>
