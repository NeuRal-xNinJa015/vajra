import { create } from 'zustand'

export type SidebarTab = 'overview' | 'cells' | 'alerts' | 'skill'

// Which forecast product the map shows at a lead time.
export type Product = 'forecast_reflectivity' | 'ml_storm_probability' | 'ml_lightning_probability'

// UI state only; anything the backend owns stays in TanStack Query.
interface UiState {
  radarVisible: boolean
  radarOpacity: number
  cellsVisible: boolean
  lightningVisible: boolean
  // Forecast lead time on screen, in minutes; 0 shows the observation.
  leadTime: number
  // True while the lead times are being stepped through automatically.
  leadAnimating: boolean
  product: Product
  // Selection follows a track, so it stays on the same storm as cycles change.
  selectedTrackId: string | null
  selectedAlertId: string | null
  // Name recorded against every alert decision.
  forecaster: string
  sidebarTab: SidebarTab
  // Navigation sidebar reduced to icons; remembered between sessions.
  navCollapsed: boolean
  setNavCollapsed: (collapsed: boolean) => void
  setRadarVisible: (visible: boolean) => void
  setRadarOpacity: (opacity: number) => void
  setCellsVisible: (visible: boolean) => void
  setLightningVisible: (visible: boolean) => void
  setLeadTime: (leadTime: number) => void
  setLeadAnimating: (animating: boolean) => void
  setProduct: (product: Product) => void
  selectTrack: (trackId: string | null) => void
  selectAlert: (alertId: string | null) => void
  setForecaster: (name: string) => void
  setSidebarTab: (tab: SidebarTab) => void
}

const NAV_KEY = 'vajra-nav-collapsed'

function savedNavCollapsed(): boolean {
  try {
    return localStorage.getItem(NAV_KEY) === '1'
  } catch {
    return false
  }
}

export const useUi = create<UiState>((set) => ({
  radarVisible: true,
  radarOpacity: 0.85,
  cellsVisible: true,
  lightningVisible: true,
  leadTime: 0,
  leadAnimating: false,
  product: 'forecast_reflectivity',
  selectedTrackId: null,
  selectedAlertId: null,
  forecaster: '',
  sidebarTab: 'overview',
  navCollapsed: savedNavCollapsed(),
  setNavCollapsed: (navCollapsed) => {
    try {
      localStorage.setItem(NAV_KEY, navCollapsed ? '1' : '0')
    } catch {
      // Storage unavailable: the choice still applies for this session.
    }
    set({ navCollapsed })
  },
  setRadarVisible: (radarVisible) => set({ radarVisible }),
  setRadarOpacity: (radarOpacity) => set({ radarOpacity }),
  setCellsVisible: (cellsVisible) => set({ cellsVisible }),
  setLightningVisible: (lightningVisible) => set({ lightningVisible }),
  setLeadTime: (leadTime) => set({ leadTime }),
  setLeadAnimating: (leadAnimating) => set({ leadAnimating }),
  setProduct: (product) => set({ product }),
  // Selecting a cell opens its details.
  selectTrack: (selectedTrackId) =>
    set(selectedTrackId ? { selectedTrackId, sidebarTab: 'cells' } : { selectedTrackId }),
  // Selecting an alert opens the alerts tab.
  selectAlert: (selectedAlertId) =>
    set(selectedAlertId ? { selectedAlertId, sidebarTab: 'alerts' } : { selectedAlertId }),
  setForecaster: (forecaster) => set({ forecaster }),
  setSidebarTab: (sidebarTab) => set({ sidebarTab }),
}))
