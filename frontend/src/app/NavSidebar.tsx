import { NavLink } from 'react-router-dom'
import { LogOut, Monitor, Moon, Sun, Zap } from 'lucide-react'
import { api } from '@/lib/api'
import { useAlerts, useHealth } from '@/lib/queries'
import { useSession } from '@/lib/session'
import { useTheme, type ThemeChoice } from '@/lib/theme'
import { cn } from '@/lib/utils'
import { useUi } from '@/store/ui'
import { PAGES } from './pages'

const THEMES: Record<ThemeChoice, { label: string; icon: typeof Sun; next: ThemeChoice }> = {
  system: { label: 'System theme', icon: Monitor, next: 'light' },
  light: { label: 'Light theme', icon: Sun, next: 'dark' },
  dark: { label: 'Dark theme', icon: Moon, next: 'system' },
}

function ThemeButton({ collapsed }: { collapsed: boolean }) {
  const choice = useTheme((s) => s.choice)
  const setChoice = useTheme((s) => s.setChoice)
  const { label, icon: Icon, next } = THEMES[choice]

  return (
    <button
      type="button"
      onClick={() => setChoice(next)}
      title={`${label}. Switch to ${THEMES[next].label.toLowerCase()}`}
      aria-label={`${label}. Switch to ${THEMES[next].label.toLowerCase()}`}
      className="text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground focus-visible:ring-ring flex h-9 w-full items-center gap-3 rounded-md px-2.5 text-[0.8rem] transition-colors outline-none focus-visible:ring-2"
    >
      <Icon className="size-4 shrink-0" />
      {!collapsed && <span className="truncate">{label}</span>}
    </button>
  )
}

const ROLE_LABEL = { viewer: 'Viewer', forecaster: 'Forecaster', admin: 'Administrator' }

// The signed-in account and its sign-out. Absent when the backend runs without sign-in.
function Account({ collapsed }: { collapsed: boolean }) {
  const user = useSession((s) => s.user)
  if (!user) return null

  // The backend session is ended first; the local one ends even if that request fails.
  const signOut = () => {
    api.logout().catch(() => undefined).finally(() => useSession.getState().end())
  }

  return (
    <div className="mt-1 flex items-center gap-2 border-t pt-2">
      {!collapsed && (
        <div className="min-w-0 flex-1 px-2.5 leading-tight">
          <div className="truncate text-[0.8rem] font-medium" title={user.display_name}>
            {user.display_name}
          </div>
          <div className="text-muted-foreground truncate text-[0.68rem]">{ROLE_LABEL[user.role]}</div>
        </div>
      )}
      <button
        type="button"
        onClick={signOut}
        title={`Sign out ${user.display_name}`}
        aria-label="Sign out"
        className={cn(
          'text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground focus-visible:ring-ring flex h-9 shrink-0 items-center justify-center rounded-md transition-colors outline-none focus-visible:ring-2',
          collapsed ? 'w-full' : 'w-9',
        )}
      >
        <LogOut className="size-4" />
      </button>
    </div>
  )
}

export function NavSidebar() {
  const collapsed = useUi((s) => s.navCollapsed)
  const version = useHealth().data?.version
  // Alerts still awaiting a forecaster's decision in the current cycle.
  const awaiting = useAlerts().data?.alerts.filter((alert) => alert.status === 'suggested').length ?? 0

  return (
    <nav
      aria-label="Main"
      className={cn(
        'bg-sidebar text-sidebar-foreground flex shrink-0 flex-col border-r',
        collapsed ? 'w-14' : 'w-52',
      )}
    >
      <div className="flex h-12 shrink-0 items-center gap-2.5 px-3.5">
        <Zap className="text-primary size-6 shrink-0 fill-current" />
        {!collapsed && <span className="text-base font-bold tracking-[0.08em]">VAJRA</span>}
      </div>

      <ul className="flex-1 space-y-0.5 px-2 py-2">
        {PAGES.map(({ path, label, icon: Icon }) => {
          const badge = path === '/alerts' && awaiting > 0 ? awaiting : null
          return (
            <li key={path}>
              <NavLink
                to={path}
                end
                title={collapsed ? label : undefined}
                className={({ isActive }) =>
                  cn(
                    'focus-visible:ring-ring relative flex h-9 items-center gap-3 rounded-md px-2.5 text-[0.8rem] transition-colors outline-none focus-visible:ring-2',
                    isActive
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-foreground',
                  )
                }
              >
                <Icon className="size-4 shrink-0" />
                {!collapsed && <span className="flex-1 truncate">{label}</span>}
                {badge !== null &&
                  (collapsed ? (
                    <span className="bg-warn absolute top-1.5 right-1.5 size-2 rounded-full" aria-hidden />
                  ) : (
                    <span className="bg-warn/15 text-warn rounded-full px-1.5 py-px font-mono text-[0.62rem] tabular-nums">
                      {badge}
                    </span>
                  ))}
                {badge !== null && <span className="sr-only">{badge} awaiting review</span>}
              </NavLink>
            </li>
          )
        })}
      </ul>

      <div className="shrink-0 border-t px-2 py-2">
        <ThemeButton collapsed={collapsed} />
        <Account collapsed={collapsed} />
        {!collapsed && version && (
          <div className="text-muted-foreground px-2.5 pt-1.5 font-mono text-[0.62rem]">Engine {version}</div>
        )}
      </div>
    </nav>
  )
}
