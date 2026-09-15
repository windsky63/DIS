export function createDefaultMarkerAppearance() {
  return {
    weld: { shape: 'circle', frameSize: 28, fontSize: 15, lineWidth: 1.35, color: '#d4143c', fillOpacity: 0 },
    components: {
      valve: { shape: 'rectangle', frameSize: 20, fontSize: 12, lineWidth: 1.35, color: '#1769d2', fillOpacity: 0 },
      flange: { shape: 'rectangle', frameSize: 20, fontSize: 12, lineWidth: 1.35, color: '#1769d2', fillOpacity: 0 },
      support: { shape: 'rectangle', frameSize: 20, fontSize: 12, lineWidth: 1.35, color: '#1769d2', fillOpacity: 0 },
    },
  }
}

export function createSpecialMarkerAppearance() {
  return { shape: 'rectangle', frameSize: 20, fontSize: 12, lineWidth: 1.35, color: '#c45d3c', fillOpacity: 0 }
}
