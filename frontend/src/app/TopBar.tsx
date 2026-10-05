import { History, MapPin, PanelLeft, Pause, Play } from 'lucide-react'
import { titleCase, utcDate, utcTime } from '@/lib/format'
import {
  useConnectionStatus,
  useReplay,
  useReplayControls,
  useStatus,
  type ConnectionStatus,
} from '@/lib/queries'
import { useCanAct } from '@/lib/session'
import { cn } from '@/lib/utils'
import { useUi } from '@/store/ui'

const CONNECTION: Record<ConnectionStatus, { label: string; dot: string }> = {
  connecting: { label: 'Starting engine', dot: 'bg-warn animate-pulse' },
  connected: { label: 'Engine online', dot: 'bg-ok' },
  disconnected: { label: 'Engine offline', dot: 'bg-danger' },
}

// Current cycle with play and pause, for pages that do not carry the full replay bar.
function CycleControl() {
  const replay = useReplay().data
  const { play, pause } = useReplayControls()
  const canAct = useCanAct()
  if (!replay?.cycle_time) return null
  const playing = replay.status === 'playing'

  return (
    <div className="flex items-center gap-1.5 rounded-md border py-0.5 pr-2.5 pl-0.5">
      <button
        type="button"
        disabled={!canAct}
        onClick={() => (playing ? pause() : play())}
        title={canAct ? (playing ? 'Pause the replay' : 'Play the replay') : 'Your account cannot control the replay'}
        aria-label={playing ? 'Pause the replay' : 'Play the replay'}
        className="hover:bg-accent focus-visible:ring-ring flex size-6 items-center justify-center rounded transition-colors outline-none focus-visible:ring-2 disabled:opacity-40 disabled:hover:bg-transparent"
      >
        {playing ? <Pause className="size-3 fill-current" /> : <Play className="size-3 fill-current" />}
      </button>
      <span className="font-mono text-[0.78rem] font-semibold tabular-nums">{utcTime(replay.cycle_time)}</span>
      <span className="text-muted-foreground font-mono text-[0.68rem] tabular-nums">
        {replay.cycle_index + 1}/{replay.cycle_count}
      </span>
    </div>
  )
}

export function TopBar({ title, showCycle }: { title: string; showCycle: boolean }) {
  const connection = CONNECTION[useConnectionStatus()]
  const status = useStatus().data
  const cycleTime = useReplay().data?.cycle_time
  const collapsed = useUi((s) => s.navCollapsed)
  const setCollapsed = useUi((s) => s.setNavCollapsed)

  return (
    <header className="bg-background flex h-12 shrink-0 items-center gap-3 border-b px-3">
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        aria-expanded={!collapsed}
        className="text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-ring flex size-8 items-center justify-center rounded-md transition-colors outline-none focus-visible:ring-2"
      >
        <PanelLeft className="size-4" />
      </button>
      <h1 className="text-sm font-semibold">{title}</h1>
      {status && (
        <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <MapPin className="size-3.5" />
          {titleCase(status.region_name)}
        </div>
      )}

      <div className="ml-auto flex items-center gap-3">
        {showCycle ? (
          <CycleControl />
        ) : (
          cycleTime && (
            <span className="text-muted-foreground font-mono text-xs tabular-nums">
              {utcDate(cycleTime)} <span className="text-foreground font-semibold">{utcTime(cycleTime)}</span> UTC
            </span>
          )
        )}

        {/* The data on screen is recorded, not live; this indicator is never hidden. */}
        <div
          className="border-warn/40 bg-warn/10 text-warn flex items-center gap-1.5 rounded-md border px-2 py-1 text-[0.68rem] font-semibold tracking-[0.14em]"
          title="Archive mode: replaying recorded observations. This is not a live feed."
        >
          <History className="size-3.5" />
          ARCHIVE
        </div>

        <div className="flex items-center gap-2 text-xs">
          <span className={cn('size-2 rounded-full', connection.dot)} />
          {connection.label}
        </div>
      </div>
    </header>
  )
}
