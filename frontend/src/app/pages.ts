import type { ComponentType } from 'react'
import { Bell, ChartLine, Database, LayoutDashboard, Map as MapIcon } from 'lucide-react'

export interface PageInfo {
  path: string
  label: string
  icon: ComponentType<{ className?: string }>
}

// The map is the home page and stays mounted; every other page loads on first visit.
export const MAP_PATH = '/'

export const PAGES: PageInfo[] = [
  { path: '/overview', label: 'Overview', icon: LayoutDashboard },
  { path: MAP_PATH, label: 'Map', icon: MapIcon },
  { path: '/analysis', label: 'Analysis', icon: ChartLine },
  { path: '/alerts', label: 'Alerts', icon: Bell },
  { path: '/system', label: 'Data & System', icon: Database },
]
