import { useEffect, useState, type ComponentType, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Bell, CloudLightning, TriangleAlert, Zap } from 'lucide-react'
import { CurrentCycle, NowcastSection } from '@/components/Sidebar'
import { backendRequestHeaders, type RiskLevel } from '@/lib/api'
import { trackLabel, utcTime } from '@/lib/format'
import {
  useAlertsSoFar,
  useDataStatus,
  useLightning,
  useObservation,
  useReplay,
  useReplayControls,
  useStatus,
  useStormCells,
  useTimeline,
} from '@/lib/queries'
import { useCanAct } from '@/lib/session'
import { RISK } from '@/lib/risk'
import { cn } from '@/lib/utils'
import { useUi } from '@/store/ui'

const RISK_ORDER: RiskLevel[] = ['severe', 'high', 'moderate', 'low']
const SOURCE_LABEL = { radar: 'Radar', satellite: 'Satellite', lightning: 'Lightning', model: 'Model data' }

function Card({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="bg-card overflow-hidden rounded-lg border">
      <div className="flex items-center justify-between border-b px-4 py-2.5">
        <h2 className="text-muted-foreground text-[0.68rem] font-semibold tracking-[0.12em] uppercase">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )
}

// One headline number. `tone` colours it only when it needs attention.
function Tile({
  icon: Icon,
  label,
  value,
  note,
  tone,
}: {
  icon: ComponentType<{ className?: string }>
  label: string
  value: string
  note: string
  tone?: 'danger' | 'warn'
}) {
  return (
    <div className="bg-card flex items-start gap-3 rounded-lg border px-4 py-3">
      <div
        className={cn(
          'mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md',
          tone === 'danger' ? 'bg-danger/10 text-danger' : tone === 'warn' ? 'bg-warn/10 text-warn' : 'bg-primary/10 text-primary',
        )}
      >
        <Icon className="size-[1.1rem]" />
      </div>
      <div className="min-w-0">
        <div className="text-muted-foreground truncate text-[0.7rem]">{label}</div>
        <div className="mt-0.5 font-mono text-2xl leading-none font-semibold tabular-nums">{value}</div>
        <div className="text-muted-foreground mt-1.5 truncate text-[0.68rem]">{note}</div>
      </div>
    </div>
  )
}

// The radar field at the current cycle, as rendered by the backend. A still image,
// so the dashboard does not carry a second map.
function RadarPreview() {
  const layer = useObservation().data
  const url = layer?.obs_time ? layer.image_url : null
  const [image, setImage] = useState<{ url: string; src: string } | null>(null)

  useEffect(() => {
    if (!url) return
    let cancelled = false
    let src: string | null = null
    fetch(url, { headers: backendRequestHeaders(url) })
      .then((response) => (response.ok ? response.blob() : Promise.reject(response.status)))
      .then((blob) => {
        if (cancelled) return
        src = URL.createObjectURL(blob)
        setImage({ url, src })
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      if (src) URL.revokeObjectURL(src)
    }
  }, [url])

  if (!layer) return <p className={emptyClass}>Loading the radar field</p>
  if (!url) return <p className={emptyClass}>No radar observation for this cycle.</p>
  const shown = image?.url === url ? image.src : null

  return (
    <div className="relative flex h-56 items-center justify-center bg-[#0a101c]">
      {shown ? (
        <img src={shown} alt="Radar reflectivity at the current cycle" className="h-full w-full object-contain" />
      ) : (
        <span className="text-xs text-white/50">Loading the radar field</span>
      )}
      <span className="absolute top-2 right-2 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[0.68rem] text-white tabular-nums">
        {utcTime(layer.valid_time)} UTC
      </span>
    </div>
  )
}

// Lightning flashes in each cycle of the event, up to the cycle the replay is on.
// Later cycles are left empty: the replay has not reached them yet.
function LightningActivity() {
  const timeline = useTimeline().data
  const replay = useReplay().data
  const { seek } = useReplayControls()
  const canAct = useCanAct()
  if (!timeline || !replay) return <p className={emptyClass}>Loading the event timeline</p>

  const current = replay.cycle_index
  const counts = timeline.cycles.map((cycle, i) => (i <= current ? (cycle.lightning?.stats.flashes ?? null) : null))
  const peak = Math.max(1, ...counts.map((count) => count ?? 0))

  return (
    <div className="px-4 pt-4 pb-3">
      <div className="flex h-44 items-end gap-1">
        {timeline.cycles.map((cycle, i) => {
          const count = counts[i]
          const label =
            i > current
              ? `${utcTime(cycle.cycle_time)} UTC: not reached yet`
              : `${utcTime(cycle.cycle_time)} UTC: ${count !== null ? `${count.toLocaleString('en')} flashes` : 'no lightning data'}`
          return (
            <button
              key={cycle.cycle_id}
              type="button"
              disabled={!canAct || i > current}
              onClick={() => seek(i)}
              title={label}
              aria-label={label}
              className="group flex h-full flex-1 items-end outline-none disabled:cursor-default"
            >
              <span
                className={cn(
                  'w-full rounded-t-sm transition-colors group-focus-visible:ring-2 group-focus-visible:ring-ring',
                  i > current || count === null
                    ? 'bg-muted'
                    : i === current
                      ? 'bg-primary'
                      : 'bg-primary/45 group-enabled:group-hover:bg-primary/70',
                )}
                style={{ height: i > current || count === null ? '3px' : `${Math.max(3, (count / peak) * 100)}%` }}
              />
            </button>
          )
        })}
      </div>
      <div className="text-muted-foreground mt-1.5 flex justify-between font-mono text-[0.65rem] tabular-nums">
        <span>{utcTime(timeline.cycles[0].cycle_time)}</span>
        <span>Peak so far {peak.toLocaleString('en')} flashes per cycle</span>
        <span>{utcTime(timeline.cycles[timeline.cycles.length - 1].cycle_time)} UTC</span>
      </div>
    </div>
  )
}

const linkClass = 'text-primary text-[0.72rem] font-medium hover:underline'
const emptyClass = 'text-muted-foreground px-4 py-4 text-xs'

// The situation at the current cycle on one screen; each block leads to the page with the detail.
export default function OverviewPage() {
  const navigate = useNavigate()
  const cells = useStormCells().data?.cells
  const { alerts, loading } = useAlertsSoFar()
  const dataStatus = useDataStatus().data
  const flashes = useLightning().data?.stats.flashes
  const interval = useStatus().data?.cycle_interval_min

  // Highest risk first; within a level, the more likely cell first.
  const ranked = cells
    ?.slice()
    .sort(
      (a, b) =>
        RISK_ORDER.indexOf(a.risk.level) - RISK_ORDER.indexOf(b.risk.level) ||
        (b.risk.likelihood ?? -1) - (a.risk.likelihood ?? -1),
    )
    .slice(0, 6)
  const awaiting = alerts.filter((alert) => alert.status === 'suggested')
  const highRisk = cells?.filter((track) => track.risk.level === 'severe' || track.risk.level === 'high').length

  return (
    <div className="mx-auto grid max-w-5xl gap-4 p-5 lg:grid-cols-2">
      <div className="grid grid-cols-2 gap-4 lg:col-span-2 lg:grid-cols-4">
        <Tile icon={CloudLightning} label="Storm cells" value={cells ? String(cells.length) : '—'} note="Tracked at this cycle" />
        <Tile
          icon={TriangleAlert}
          label="High or severe risk"
          value={highRisk !== undefined ? String(highRisk) : '—'}
          note="Cells at this cycle"
          tone={highRisk ? 'danger' : undefined}
        />
        <Tile
          icon={Zap}
          label="Lightning flashes"
          value={flashes !== undefined ? flashes.toLocaleString('en') : '—'}
          note={flashes !== undefined && interval ? `In the last ${interval} min` : 'No lightning data for this cycle'}
        />
        <Tile
          icon={Bell}
          label="Alerts awaiting review"
          value={loading ? '—' : String(awaiting.length)}
          note="Up to this cycle"
          tone={awaiting.length ? 'warn' : undefined}
        />
      </div>
      <Card title="Radar at this cycle" action={<Link to="/" className={linkClass}>Open map</Link>}>
        <RadarPreview />
      </Card>
      <Card title="Lightning activity by cycle">
        <LightningActivity />
      </Card>

      <div className="bg-card overflow-hidden rounded-lg border [&>section]:border-b-0">
        <CurrentCycle />
      </div>
      <div className="bg-card overflow-hidden rounded-lg border [&>section]:border-b-0">
        <NowcastSection />
      </div>

      <Card title="Highest-risk storm cells" action={<Link to="/" className={linkClass}>Open map</Link>}>
        {!ranked ? (
          <p className={emptyClass}>Loading storm cells</p>
        ) : ranked.length === 0 ? (
          <p className={emptyClass}>No storm cells at this cycle.</p>
        ) : (
          <ul>
            {ranked.map((track) => (
              <li key={track.track_id} className="border-b last:border-b-0">
                <button
                  type="button"
                  onClick={() => {
                    useUi.getState().selectTrack(track.track_id)
                    navigate('/')
                  }}
                  className="hover:bg-accent/50 focus-visible:ring-ring flex w-full items-center gap-3 px-4 py-2 text-left text-[0.8rem] outline-none focus-visible:ring-2 focus-visible:ring-inset"
                >
                  <span className="w-8 font-mono font-medium">{trackLabel(track.track_id)}</span>
                  <span className={cn('rounded-full border px-2 py-0.5 text-[0.65rem] font-medium', RISK[track.risk.level].badge)}>
                    {RISK[track.risk.level].label}
                  </span>
                  <span className="tabular-nums">
                    {track.cell.max_dbz !== null ? `${track.cell.max_dbz.toFixed(0)} dBZ` : '—'}
                  </span>
                  <span className="text-muted-foreground ml-auto tabular-nums">
                    {track.risk.likelihood !== null ? `${(track.risk.likelihood * 100).toFixed(0)}% likelihood` : 'No likelihood'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title={`Alerts awaiting review${awaiting.length ? ` · ${awaiting.length}` : ''}`}
        action={<Link to="/alerts" className={linkClass}>Open alerts</Link>}
      >
        {awaiting.length === 0 ? (
          <p className={emptyClass}>{loading ? 'Loading alerts' : 'No alerts are awaiting review.'}</p>
        ) : (
          <ul>
            {awaiting.slice(0, 6).map((alert) => (
              <li key={alert.alert_id} className="border-b last:border-b-0">
                <button
                  type="button"
                  onClick={() => {
                    useUi.getState().selectAlert(alert.alert_id)
                    navigate('/alerts')
                  }}
                  className="hover:bg-accent/50 focus-visible:ring-ring flex w-full items-center gap-3 px-4 py-2 text-left text-[0.8rem] outline-none focus-visible:ring-2 focus-visible:ring-inset"
                >
                  <span className={cn('rounded-full border px-2 py-0.5 text-[0.65rem] font-medium', RISK[alert.risk_level].badge)}>
                    {RISK[alert.risk_level].label}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{alert.headline}</span>
                  <span className="text-muted-foreground font-mono text-[0.68rem] tabular-nums">
                    {utcTime(alert.valid_from)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="lg:col-span-2">
        <Card title="Data sources" action={<Link to="/system" className={linkClass}>Open data &amp; system</Link>}>
          {!dataStatus ? (
            <p className={emptyClass}>Loading data status</p>
          ) : (
            <ul className="flex flex-wrap gap-x-8 gap-y-2 px-4 py-3 text-[0.8rem]">
              {(Object.keys(SOURCE_LABEL) as (keyof typeof SOURCE_LABEL)[]).map((type) => {
                const missing = dataStatus.missing.includes(type)
                return (
                  <li key={type} className="flex items-center gap-2">
                    <span className={cn('size-2 rounded-full', missing ? 'bg-danger' : 'bg-ok')} />
                    {SOURCE_LABEL[type]}
                    <span className="text-muted-foreground text-xs">{missing ? 'Not connected' : 'Connected'}</span>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}
