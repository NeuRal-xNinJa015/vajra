import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, CloudLightning, X } from 'lucide-react'
import type { Severity, StormCellTrack } from '@/lib/api'
import { compass, duration, trackLabel, utcTime } from '@/lib/format'
import { useAlertActions, useExplanation, useForecast, useReplay, useStormCells } from '@/lib/queries'
import { RISK } from '@/lib/risk'
import { useCanAct } from '@/lib/session'
import { cn } from '@/lib/utils'
import { useUi } from '@/store/ui'

const SEVERITY: Record<Severity, { label: string; dot: string; badge: string }> = {
  severe: { label: 'Severe', dot: 'bg-danger', badge: 'border-danger/40 bg-danger/15 text-danger' },
  strong: { label: 'Strong', dot: 'bg-high', badge: 'border-high/40 bg-high/15 text-high' },
  moderate: { label: 'Moderate', dot: 'bg-warn', badge: 'border-warn/40 bg-warn/15 text-warn' },
}

function motion(track: StormCellTrack) {
  const { speed_kmh, motion_dir_deg } = track.cell
  return speed_kmh !== null && motion_dir_deg !== null
    ? `${speed_kmh.toFixed(0)} km/h ${compass(motion_dir_deg)}`
    : null
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-muted-foreground text-[0.65rem]">{label}</div>
      <div className="text-[0.82rem] tabular-nums">{value}</div>
    </div>
  )
}

// Peak reflectivity of the cell at each cycle it has been tracked, as a small line.
function Trend({ history }: { history: StormCellTrack['history'] }) {
  const points = history.filter((h): h is typeof h & { max_dbz: number } => h.max_dbz !== null)
  if (points.length < 2) return null
  const values = points.map((h) => h.max_dbz)
  const low = Math.min(...values)
  const high = Math.max(...values)
  const span = high - low || 1
  const line = points
    .map((h, i) => `${((i / (points.length - 1)) * 100).toFixed(1)},${(26 - ((h.max_dbz - low) / span) * 24).toFixed(1)}`)
    .join(' ')

  return (
    <div className="mt-4">
      <div className="text-muted-foreground mb-1 flex justify-between text-[0.65rem]">
        <span>Peak reflectivity since first tracked</span>
        <span className="tabular-nums">
          {low.toFixed(0)}–{high.toFixed(0)} dBZ
        </span>
      </div>
      <svg viewBox="0 0 100 28" preserveAspectRatio="none" className="text-primary h-9 w-full" role="img" aria-label={`Peak reflectivity over ${points.length} cycles, from ${values[0].toFixed(0)} to ${values[values.length - 1].toFixed(0)} dBZ`}>
        <polyline points={line} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="text-muted-foreground flex justify-between font-mono text-[0.62rem] tabular-nums">
        <span>{utcTime(points[0].cycle_time)}</span>
        <span>{utcTime(points[points.length - 1].cycle_time)} UTC</span>
      </div>
    </div>
  )
}

type TabKey = 'overview' | 'forecast' | 'evidence'
const TABS: { key: TabKey; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'forecast', label: 'Forecast' },
  { key: 'evidence', label: 'Evidence' },
]

function CellDetails({ track }: { track: StormCellTrack }) {
  const cycleTime = useReplay().data?.cycle_time
  const { preview } = useAlertActions()
  const canAct = useCanAct()
  const navigate = useNavigate()
  const cell = track.cell
  // Explain the nearest lead time the ML model predicted for this cell.
  const explainLead = track.projections.find((p) => p.ml_lightning_probability !== null)?.lead_time_min ?? null
  const explanation = useExplanation(cell.cycle_id, cell.cell_id, explainLead).data
  const severity = SEVERITY[cell.severity ?? 'moderate']
  const trackedMin = cycleTime ? (Date.parse(cycleTime) - Date.parse(track.first_seen)) / 60000 : null

  const [tab, setTab] = useState<TabKey>('overview')

  return (
    <div className="px-4 py-3.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-lg font-semibold">{trackLabel(track.track_id)}</span>
          <span className={cn('rounded-full border px-2 py-0.5 text-[0.65rem] font-medium', severity.badge)}>
            {severity.label}
          </span>
        </div>
        <button
          type="button"
          onClick={() => useUi.getState().selectTrack(null)}
          className="text-muted-foreground hover:bg-accent hover:text-foreground rounded p-1"
          aria-label="Close cell details"
        >
          <X className="size-3.5" />
        </button>
      </div>

      <div className="mt-3 flex border-b" role="tablist" aria-label="Storm cell details">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cn(
              'focus-visible:ring-ring -mb-px border-b-2 px-3 py-1.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2',
              tab === key ? 'border-primary text-foreground' : 'text-muted-foreground hover:text-foreground border-transparent',
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'overview' && (
        <>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2.5">
        <Stat label="Peak reflectivity" value={cell.max_dbz !== null ? `${cell.max_dbz.toFixed(1)} dBZ` : '—'} />
        <Stat label="Mean reflectivity" value={cell.mean_dbz !== null ? `${cell.mean_dbz.toFixed(1)} dBZ` : '—'} />
        <Stat label="Area" value={`${cell.area_km2.toLocaleString('en', { maximumFractionDigits: 0 })} km²`} />
        <Stat label="Motion" value={motion(track) ?? 'Not yet estimated'} />
        <Stat label="Tracked for" value={trackedMin !== null ? duration(trackedMin) : '—'} />
        <Stat
          label="Position"
          value={`${Math.abs(cell.centroid[1]).toFixed(2)}°${cell.centroid[1] >= 0 ? 'N' : 'S'} ${Math.abs(cell.centroid[0]).toFixed(2)}°${cell.centroid[0] >= 0 ? 'E' : 'W'}`}
        />
      </div>

      <Trend history={track.history} />

      {/* Risk level with the facts it was derived from, and a way to raise an alert. */}
      <div className="mt-4 rounded-md border px-3 py-2.5">
        <div className="flex items-center justify-between">
          <span className={cn('rounded-full border px-2 py-0.5 text-[0.65rem] font-medium', RISK[track.risk.level].badge)}>
            {RISK[track.risk.level].label} risk
          </span>
          {/* Drafting an alert needs the forecaster role. */}
          {canAct && (
            <button
              type="button"
              disabled={preview.isPending}
              onClick={() =>
                preview.mutate(
                  { cycleId: cell.cycle_id, cellId: cell.cell_id },
                  {
                    onSuccess: (alert) => {
                      useUi.getState().selectAlert(alert.alert_id)
                      navigate('/alerts')
                    },
                  },
                )
              }
              className="text-primary text-[0.72rem] font-medium hover:underline disabled:opacity-50"
            >
              Open alert
            </button>
          )}
        </div>
        <ul className="text-muted-foreground mt-2 space-y-0.5 text-[0.7rem] leading-snug">
          {track.risk.factors.map((factor) => (
            <li key={factor}>{factor}</li>
          ))}
        </ul>
        {preview.error && <p className="text-destructive mt-2 text-[0.7rem]">{preview.error.message}</p>}
      </div>

        </>
      )}

      {tab === 'evidence' && (
        <>
      {explanation?.available && explanation.probability !== null && (
        <div className="mt-4">
          <div className="text-muted-foreground mb-1.5 text-[0.65rem]">
            Why the model gives {(explanation.probability * 100).toFixed(0)}% lightning at +
            {explanation.lead_time_min} min: inputs that moved it most
          </div>
          <ul className="space-y-1">
            {explanation.contributions.slice(0, 5).map((item) => (
              <li key={item.feature} className="flex items-baseline gap-2 text-[0.72rem]">
                <span
                  className={cn('w-9 shrink-0 text-right font-mono tabular-nums', item.contribution >= 0 ? 'text-danger' : 'text-primary')}
                  title="Effect on the model's output, in log-odds. Positive raises the probability."
                >
                  {item.contribution >= 0 ? '+' : ''}
                  {item.contribution.toFixed(1)}
                </span>
                <span className="min-w-0 flex-1 leading-snug">{item.description}</span>
                <span className="text-muted-foreground shrink-0 text-[0.62rem]">{item.source}</span>
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground mt-1.5 text-[0.62rem] leading-snug">
            These show how the model reached its number, not what causes the storm.
          </p>
        </div>
      )}

          {!(explanation?.available && explanation.probability !== null) && (
            <p className="text-muted-foreground mt-4 text-xs leading-relaxed">
              {explanation?.reason ?? 'No model explanation is available for this cell at this cycle.'}
            </p>
          )}
        </>
      )}

      {tab === 'forecast' && (
        <>
      {track.projections.length > 0 ? (
        <div className="mt-4">
          <div className="text-muted-foreground mb-1.5 flex justify-between text-[0.65rem]">
            <span>Storm probability along the projected track</span>
            <span>Lightning (ML) · Spread</span>
          </div>
          <ul className="space-y-1.5">
            {track.projections.map((p) => (
              <li key={p.lead_time_min} className="flex items-center gap-2 text-[0.75rem]">
                <span className="w-9 font-mono tabular-nums">+{p.lead_time_min}</span>
                <div className="bg-muted h-1.5 flex-1 overflow-hidden rounded-full">
                  <div className="bg-primary h-full rounded-full" style={{ width: `${(p.probability ?? 0) * 100}%` }} />
                </div>
                <span className="w-9 text-right tabular-nums">
                  {p.probability !== null ? `${(p.probability * 100).toFixed(0)}%` : '—'}
                </span>
                <span className="w-9 text-right tabular-nums" title="Lightning probability from the ML model">
                  {p.ml_lightning_probability !== null ? `${(p.ml_lightning_probability * 100).toFixed(0)}%` : '—'}
                </span>
                <span className="text-muted-foreground w-14 text-right tabular-nums">
                  {p.uncertainty !== null ? `±${p.uncertainty.toFixed(1)} dBZ` : '—'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-muted-foreground mt-3 text-xs leading-relaxed">
          No projection for this cycle: the nowcast was not issued, so the cell has no motion estimate.
        </p>
      )}
        </>
      )}
    </div>
  )
}

// Details of the selected storm cell, beside the map. Rendered only while a cell is selected.
export function StormPanel() {
  const cells = useStormCells().data?.cells
  const selectedTrackId = useUi((s) => s.selectedTrackId)
  if (!selectedTrackId) return null
  const selected = cells?.find((track) => track.track_id === selectedTrackId)

  return (
    <aside aria-label="Storm cell details" className="bg-card w-80 shrink-0 overflow-y-auto border-l">
      {selected ? (
        <CellDetails track={selected} />
      ) : (
        cells && (
          <div className="flex items-start justify-between gap-3 px-4 py-3.5">
            <p className="text-muted-foreground text-xs leading-relaxed">
              Cell {trackLabel(selectedTrackId)} is not present at this cycle. It stays selected and
              reappears if the replay reaches a cycle that has it.
            </p>
            <button
              type="button"
              onClick={() => useUi.getState().selectTrack(null)}
              className="text-muted-foreground hover:bg-accent hover:text-foreground rounded p-1"
              aria-label="Close cell details"
            >
              <X className="size-3.5" />
            </button>
          </div>
        )
      )}
    </aside>
  )
}

// Storm cells at the current cycle, as a collapsible list over the map.
export function CellList() {
  const cells = useStormCells().data?.cells
  const forecast = useForecast().data
  const selectedTrackId = useUi((s) => s.selectedTrackId)
  const selectTrack = useUi((s) => s.selectTrack)
  const [open, setOpen] = useState(true)
  if (!cells) return null

    // Sits below the row where the map shows its forecast or data notice.
  return (
    <div className="bg-card/90 absolute top-12 left-14 w-60 rounded-lg border shadow-md backdrop-blur">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="text-muted-foreground flex w-full items-center gap-2 px-3 py-2 text-[0.68rem] font-semibold tracking-[0.12em] uppercase"
      >
        <CloudLightning className="size-3.5" />
        Storm cells
        <span className="bg-muted rounded-full px-1.5 py-px font-mono text-[0.62rem] tracking-normal tabular-nums">
          {cells.length}
        </span>
        <ChevronDown className={cn('ml-auto size-3.5 transition-transform', !open && '-rotate-90')} />
      </button>
      {open &&
        (cells.length === 0 ? (
          <p className="text-muted-foreground border-t px-3 py-2.5 text-xs">No storm cells at this cycle.</p>
        ) : (
          <>
            <ul className="max-h-56 overflow-y-auto border-t py-1">
              {cells.map((track) => {
                const severity = SEVERITY[track.cell.severity ?? 'moderate']
                const active = track.track_id === selectedTrackId
                return (
                  <li key={track.track_id}>
                    <button
                      type="button"
                      onClick={() => selectTrack(active ? null : track.track_id)}
                      aria-pressed={active}
                      title={`${severity.label} · ${RISK[track.risk.level].label} risk`}
                      className={cn(
                        'flex w-full items-center gap-2 px-3 py-1.5 text-left text-[0.75rem] transition-colors',
                        active ? 'bg-accent' : 'hover:bg-accent/50',
                      )}
                    >
                      <span className={cn('size-2 shrink-0 rounded-full', severity.dot)} />
                      <span className="w-8 font-mono font-medium">{trackLabel(track.track_id)}</span>
                      <span className="tabular-nums">{track.cell.max_dbz?.toFixed(0)} dBZ</span>
                      <span className="text-muted-foreground ml-auto tabular-nums">{motion(track) ?? '—'}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
            {!forecast?.available && (
              <p className="text-muted-foreground border-t px-3 py-1.5 text-[0.65rem]">No projections this cycle.</p>
            )}
          </>
        ))}
    </div>
  )
}
