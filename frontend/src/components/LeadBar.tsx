import { useEffect } from 'react'
import { Pause, Play } from 'lucide-react'
import { utcTime } from '@/lib/format'
import { useDisplayedLayer, useForecast, useStatus } from '@/lib/queries'
import { cn } from '@/lib/utils'
import { useShallow } from 'zustand/react/shallow'
import { useUi, type Product } from '@/store/ui'

const STEP_MS = 900

const PRODUCTS: { value: Product; label: string }[] = [
  { value: 'forecast_reflectivity', label: 'Reflectivity' },
  { value: 'ml_storm_probability', label: 'Storm probability (ML)' },
  { value: 'ml_lightning_probability', label: 'Lightning probability (ML)' },
]

// Lead-time selector under the map: the observation, then each forecast lead.
export function LeadBar() {
  const leads = useStatus().data?.lead_times_min
  const forecast = useForecast().data
  const { layer, isForecast } = useDisplayedLayer()
  const { leadTime, leadAnimating, product, setLeadTime, setLeadAnimating, setProduct } = useUi(
    useShallow((s) => ({
      leadTime: s.leadTime,
      leadAnimating: s.leadAnimating,
      product: s.product,
      setLeadTime: s.setLeadTime,
      setLeadAnimating: s.setLeadAnimating,
      setProduct: s.setProduct,
    })),
  )
  const available = forecast?.available ?? false

  // Step through the observation and every lead, looping, while animating.
  useEffect(() => {
    if (!leadAnimating || !leads || !available) return
    const steps = [0, ...leads]
    const timer = setInterval(() => {
      const current = useUi.getState().leadTime
      setLeadTime(steps[(steps.indexOf(current) + 1) % steps.length])
    }, STEP_MS)
    return () => clearInterval(timer)
  }, [leadAnimating, leads, available, setLeadTime])

  if (!leads || !layer) return null

  return (
    // Container queries: the bar drops its labels as the map column narrows, never wraps.
    <div className="bg-background @container shrink-0 border-t">
    <div className="flex h-11 items-center gap-3 px-4">
      <span className="text-muted-foreground hidden text-[0.68rem] font-semibold tracking-[0.12em] whitespace-nowrap uppercase @3xl:inline">
        Lead time
      </span>
      <button
        type="button"
        disabled={!available}
        onClick={() => setLeadAnimating(!leadAnimating)}
        title={leadAnimating ? 'Stop the forecast loop' : 'Loop through the forecast'}
        aria-label={leadAnimating ? 'Stop the forecast loop' : 'Loop through the forecast'}
        className="hover:bg-accent flex size-7 items-center justify-center rounded-md transition-colors disabled:opacity-40 disabled:hover:bg-transparent"
      >
        {leadAnimating ? <Pause className="size-3.5 fill-current" /> : <Play className="size-3.5 fill-current" />}
      </button>

      <div className="bg-muted flex rounded-md p-0.5" role="group" aria-label="Forecast lead time">
        {[0, ...leads].map((lead) => {
          const selected = lead === leadTime
          return (
            <button
              key={lead}
              type="button"
              disabled={lead > 0 && !available}
              onClick={() => {
                setLeadAnimating(false)
                setLeadTime(lead)
              }}
              aria-pressed={selected}
              title={lead > 0 && !available ? (forecast?.reason ?? 'No forecast for this cycle') : undefined}
              className={cn(
                'rounded px-2.5 py-1 font-mono text-[0.72rem] tabular-nums transition-colors disabled:opacity-40',
                selected
                  ? lead === 0
                    ? 'bg-background text-foreground shadow-sm'
                    : 'bg-primary text-primary-foreground shadow-sm'
                  : 'text-muted-foreground enabled:hover:text-foreground',
              )}
            >
              {lead === 0 ? 'Observed' : `+${lead}`}
            </button>
          )
        })}
      </div>

      <select
        value={product}
        onChange={(e) => setProduct(e.target.value as Product)}
        disabled={!available}
        aria-label="Forecast product"
        className="bg-muted min-w-0 shrink rounded-md border-0 px-2 py-1 text-[0.72rem] outline-none disabled:opacity-40"
      >
        {PRODUCTS.map(({ value, label }) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>

      <div className="ml-auto flex min-w-0 items-baseline gap-2 text-[0.72rem] whitespace-nowrap">
        <span className={cn('hidden font-medium @2xl:inline', isForecast && 'text-primary')}>
          {isForecast ? `Forecast +${layer.lead_time_min} min` : 'Latest observation'}
        </span>
        <span className="text-muted-foreground truncate font-mono tabular-nums">
          {isForecast
            ? `valid ${utcTime(layer.valid_time)} UTC`
            : layer.obs_time
              ? `scanned ${utcTime(layer.obs_time)} UTC`
              : 'no radar scan'}
        </span>
      </div>
    </div>
    </div>
  )
}
