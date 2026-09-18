import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import { Ripple } from 'vuetify/directives'
import 'vuetify/styles'
import ReferenceWindowApp from './components/ReferenceWindowApp.vue'
import RouterRoot from './RouterRoot.vue'
import { router } from './router.js'
import './style.css'
import { APP_THEMES, loadThemePreference, resolveThemeName } from './themePreferences.js'

const vuetify = createVuetify({
  directives: { Ripple },
  theme: {
    defaultTheme: resolveThemeName(loadThemePreference()),
    themes: APP_THEMES,
  },
  defaults: {
    VBtn: { rounded: 0 },
    VCard: { rounded: 0 },
    VTextField: { density: 'compact', variant: 'outlined', hideDetails: true },
    VSelect: { density: 'compact', variant: 'outlined', hideDetails: true },
    VFileInput: { density: 'compact', variant: 'outlined', hideDetails: true }
  }
})

const referenceWindow = new URLSearchParams(window.location.search).get('view') === 'reference'
const rootComponent = referenceWindow ? ReferenceWindowApp : RouterRoot
const app = createApp(rootComponent).use(vuetify)

if (!referenceWindow) app.use(router)
app.mount('#app')
