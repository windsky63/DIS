import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import {
  VAlert, VApp, VAppBar, VAppBarTitle, VBtn, VBtnToggle, VCard, VCardActions,
  VCardText, VCardTitle, VChip, VContainer, VDialog, VDivider, VFileInput, VList,
  VListItem, VListItemTitle, VMain, VMenu, VNavigationDrawer, VProgressCircular,
  VForm, VProgressLinear, VSelect, VSlider, VSnackbar, VSpacer, VTab, VTabs,
  VSwitch, VTextField, VToolbar, VTooltip
} from 'vuetify/components'
import { Ripple } from 'vuetify/directives'
import 'vuetify/styles'
import App from './App.vue'
import ReferenceWindowApp from './components/ReferenceWindowApp.vue'
import './style.css'
import { APP_THEMES, loadThemePreference, resolveThemeName } from './themePreferences.js'

const vuetify = createVuetify({
  components: {
    VAlert, VApp, VAppBar, VAppBarTitle, VBtn, VBtnToggle, VCard, VCardActions,
    VCardText, VCardTitle, VChip, VContainer, VDialog, VDivider, VFileInput, VList,
    VListItem, VListItemTitle, VMain, VMenu, VNavigationDrawer, VProgressCircular,
    VForm, VProgressLinear, VSelect, VSlider, VSnackbar, VSpacer, VTab, VTabs,
    VSwitch, VTextField, VToolbar, VTooltip
  },
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

const rootComponent = new URLSearchParams(window.location.search).get('view') === 'reference'
  ? ReferenceWindowApp
  : App

createApp(rootComponent).use(vuetify).mount('#app')
