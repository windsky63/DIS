import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import {
  VAlert, VApp, VAppBar, VAppBarTitle, VBtn, VBtnToggle, VCard, VCardActions,
  VCardText, VCardTitle, VChip, VContainer, VDialog, VDivider, VFileInput, VList,
  VListItem, VListItemTitle, VMain, VMenu, VNavigationDrawer, VProgressCircular,
  VProgressLinear, VSelect, VSlider, VSnackbar, VSpacer, VTab, VTabs,
  VSwitch, VTextField, VToolbar, VTooltip
} from 'vuetify/components'
import { Ripple } from 'vuetify/directives'
import 'vuetify/styles'
import App from './App.vue'
import './style.css'

const vuetify = createVuetify({
  components: {
    VAlert, VApp, VAppBar, VAppBarTitle, VBtn, VBtnToggle, VCard, VCardActions,
    VCardText, VCardTitle, VChip, VContainer, VDialog, VDivider, VFileInput, VList,
    VListItem, VListItemTitle, VMain, VMenu, VNavigationDrawer, VProgressCircular,
    VProgressLinear, VSelect, VSlider, VSnackbar, VSpacer, VTab, VTabs,
    VSwitch, VTextField, VToolbar, VTooltip
  },
  directives: { Ripple },
  theme: {
    defaultTheme: 'weldLight',
    themes: {
      weldLight: {
        dark: false,
        colors: {
          primary: '#102a43',
          secondary: '#2d8b89',
          accent: '#c45d3c',
          background: '#edf2f5',
          surface: '#ffffff',
          error: '#b64932',
          info: '#2d8b89'
        }
      }
    }
  },
  defaults: {
    VBtn: { rounded: 0 },
    VCard: { rounded: 0 },
    VTextField: { density: 'compact', variant: 'outlined', hideDetails: true },
    VSelect: { density: 'compact', variant: 'outlined', hideDetails: true },
    VFileInput: { density: 'compact', variant: 'outlined', hideDetails: true }
  }
})

createApp(App).use(vuetify).mount('#app')
