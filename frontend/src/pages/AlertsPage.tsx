import { useState } from 'react'
import { Bell } from 'lucide-react'
import { AlertReview } from '@/components/AlertReview'
import type { Alert, RiskLevel } from '@/lib/api'
import { trackLabel, utcTime } from '@/lib/format'
import { useAlertsSoFar } from '@/lib/queries'
import { ALERT_STATUS, RISK } from '@/lib/risk'
import { cn } from '@/lib/utils'
import { useUi } from '@/store/ui'

type Filter = 'all' | 'awaiting'

function AlertRow({ alert, active }: { alert: Alert; active: boolean }) {
  const selectAlert = useUi((s) => s.selectAlert)
  const awaiting = alert.status === 'suggested'
  return (
    <li>
      <button
        type="button"
        onClick={() => selectAlert(alert.alert_id)}
        aria-current={active}
        className={cn(
          'focus-visible:ring-ring flex w-full flex-col gap-1 border-b px-4 py-3 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-inset',
          active ? 'bg-accent' : 'hover:bg-accent/50',
        )}
      >
        <span className="flex items-center gap-2">
          <span className={cn('rounded-full border px-2 py-0.5 text-[0.65rem] font-medium', RISK[alert.risk_level].badge)}>
            {RISK[alert.risk_level].label}
          </span>
          {alert.track_id && <span className="font-mono text-xs font-medium">{trackLabel(alert.track_id)}</span>}
          <span className={cn('ml-auto text-[0.68rem]', awaiting ? 'text-warn font-medium' : 'text-muted-foreground')}>
            {ALERT_STATUS[alert.status]}
          </span>
        </span>
        <span className="line-clamp-2 text-[0.8rem] leading-snug">{alert.headline}</span>
        <span className="text-muted-foreground font-mono text-[0.68rem] tabular-nums">
          Valid {utcTime(alert.valid_from)}–{utcTime(alert.valid_to)} UTC
        </span>
      </button>
    </li>
  )
}

// Alerts raised from the start of the event up to the current cycle: a list to pick
// from and the review of the selected one.
export default function AlertsPage() {
  const { alerts, loading } = useAlertsSoFar()
  const selectedAlertId = useUi((s) => s.selectedAlertId)
  const [filter, setFilter] = useState<Filter>('all')
  const [risk, setRisk] = useState<RiskLevel | 'all'>('all')

  const awaitingCount = alerts.filter((alert) => alert.status === 'suggested').length
  const shown = alerts.filter(
    (alert) => (filter === 'all' || alert.status === 'suggested') && (risk === 'all' || alert.risk_level === risk),
  )
  const selected = alerts.find((alert) => alert.alert_id === selectedAlertId)

  return (
    <div className="flex h-full min-h-0">
      <section aria-label="Alerts" className="flex w-[22rem] shrink-0 flex-col border-r">
        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-2.5">
          <div className="bg-muted flex rounded-md p-0.5" role="group" aria-label="Filter alerts">
            {(['all', 'awaiting'] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setFilter(value)}
                aria-pressed={filter === value}
                className={cn(
                  'rounded px-2.5 py-1 text-[0.72rem] transition-colors',
                  filter === value ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {value === 'all' ? `All ${alerts.length}` : `Awaiting review ${awaitingCount}`}
              </button>
            ))}
          </div>
          <select
            value={risk}
            onChange={(e) => setRisk(e.target.value as RiskLevel | 'all')}
            aria-label="Filter by risk level"
            className="bg-muted ml-auto rounded-md border-0 px-2 py-1 text-[0.72rem] outline-none"
          >
            <option value="all">All risk levels</option>
            {(Object.keys(RISK) as RiskLevel[]).map((level) => (
              <option key={level} value={level}>
                {RISK[level].label}
              </option>
            ))}
          </select>
        </div>
        {shown.length > 0 ? (
          <ul className="min-h-0 flex-1 overflow-y-auto">
            {shown.map((alert) => (
              <AlertRow key={alert.alert_id} alert={alert} active={alert.alert_id === selectedAlertId} />
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground px-4 py-6 text-xs leading-relaxed">
            {loading
              ? 'Loading alerts'
              : alerts.length > 0
                ? 'No alerts match these filters.'
                : 'No alerts up to this cycle. Alerts are suggested for cells at severe risk, or drafted from a storm cell on the Map.'}
          </p>
        )}
      </section>

      <section aria-label="Alert review" className="min-w-0 flex-1 overflow-y-auto">
        {selected ? (
          <div className="mx-auto max-w-2xl px-6 py-5">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className={cn('rounded-full border px-2 py-0.5 text-[0.68rem] font-medium', RISK[selected.risk_level].badge)}>
                {RISK[selected.risk_level].label} risk
              </span>
              {selected.track_id && <span className="font-mono text-sm font-semibold">{trackLabel(selected.track_id)}</span>}
              <span className="text-muted-foreground font-mono text-xs tabular-nums">
                Valid {utcTime(selected.valid_from)}–{utcTime(selected.valid_to)} UTC
              </span>
              <span className="text-muted-foreground ml-auto text-xs">{ALERT_STATUS[selected.status]}</span>
            </div>
            {selected.status !== 'suggested' && <h2 className="mb-3 text-sm font-semibold">{selected.headline}</h2>}
            {/* Keyed so the edit fields reset when another alert is picked or this one is decided. */}
            <AlertReview key={`${selected.alert_id}:${selected.status}`} alert={selected} />
          </div>
        ) : (
          <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 text-xs">
            <Bell className="size-5" />
            {alerts.length > 0 ? 'Select an alert to review it.' : 'Nothing to review.'}
          </div>
        )}
      </section>
    </div>
  )
}
