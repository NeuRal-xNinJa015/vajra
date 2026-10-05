import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { MapPage } from '@/pages/MapPage'
import { cn } from '@/lib/utils'
import { NavSidebar } from './NavSidebar'
import { MAP_PATH, PAGES } from './pages'
import { TopBar } from './TopBar'

const SystemPage = lazy(() => import('@/pages/SystemPage'))
const AlertsPage = lazy(() => import('@/pages/AlertsPage'))
const OverviewPage = lazy(() => import('@/pages/OverviewPage'))
const AnalysisPage = lazy(() => import('@/pages/AnalysisPage'))

export function AppShell() {
  const { pathname } = useLocation()
  const onMap = pathname === MAP_PATH
  const page = PAGES.find((p) => p.path === pathname)

  return (
    <div className="flex h-screen overflow-hidden">
      <NavSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar title={page?.label ?? ''} showCycle={!onMap} />
        {/* The map is hidden, not unmounted, so its view and loaded layers survive. */}
        <main className={cn('flex min-h-0 min-w-0 flex-1', !onMap && 'hidden')} aria-hidden={!onMap}>
          <MapPage active={onMap} />
        </main>
        {!onMap && (
          <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
            <Suspense fallback={null}>
              <Routes>
                {PAGES.filter((p) => p.path !== MAP_PATH).map((p) => (
                  <Route key={p.path} path={p.path} element={p.path === '/alerts' ? <AlertsPage /> : p.path === '/overview' ? <OverviewPage /> : p.path === '/analysis' ? <AnalysisPage /> : <SystemPage />} />
                ))}
                <Route path="*" element={<Navigate to={MAP_PATH} replace />} />
              </Routes>
            </Suspense>
          </main>
        )}
      </div>
    </div>
  )
}
