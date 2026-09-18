<script setup>
import ProjectRuleViewer from './ProjectRuleViewer.vue'

defineProps({
  config: { type: Object, required: true }, projects: { type: Array, default: () => [] },
  activeProjectId: String, referenceFiles: { type: Array, default: () => [] },
})
const emit = defineEmits(['switchProject', 'switchRule', 'assignReferenceRule'])
const open = defineModel({ type: Boolean, required: true })
</script>

<template>
  <v-dialog v-model="open" max-width="820" scrollable>
    <v-card class="settings-card project-rules-card">
      <div class="draft-manager-header settings-header"><span class="draft-manager-header__icon settings-header__icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="9" cy="6" r="2" /><circle cx="15" cy="12" r="2" /><circle cx="9" cy="18" r="2" /></svg></span><div><v-card-title>项目识别规则</v-card-title><v-card-subtitle>切换识别项目与出图来源，展开查看只读规则。</v-card-subtitle></div></div>
      <v-card-text class="settings-content">
        <ProjectRuleViewer v-if="open && projects.length" :config="config" :projects="projects" :active-project-id="activeProjectId" :reference-files="referenceFiles" @switch-project="emit('switchProject', $event)" @switch-rule="emit('switchRule', $event)" @assign-reference-rule="emit('assignReferenceRule', $event)" />
      </v-card-text>
      <v-card-actions class="settings-actions"><v-spacer /><v-btn variant="text" @click="open = false">关闭</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>
