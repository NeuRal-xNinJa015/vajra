import type { ComponentType, ReactNode } from 'react'
import { Grid3x3, Server, TrendingUp } from 'lucide-react'
import { compass, titleCase, utcDate, utcTime, utcTimeSeconds } from '@/lib/format'
import {
  useConnectionStatus,
  useForecast,
  useLightning,
  useObservation,
  useReplay,
  useStatus,
} from '@/lib/queries'
import { cn } from '@/lib/utils'

type Icon = ComponentType<{ className?: string }>

function Section({ icon: Icon, title, children }: { icon: Icon; title: string; children: ReactNode }) {
  return (
    <section className="border-b px-4 py-3.5">
      <h2 className="text-muted-foreground mb-2.5 flex items-center gap-2 text-[0.68rem] font-semibold tracking-[0.12em] uppercase">
        <Icon className="size-3.5" />
        {title}
      </h2>
      {children}
    </section>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-[3px] text-[0.8rem]">
      <dt className="text-muted-foreground shrink-0">{label}</dt>
      <dd className="min-w-0 truncate text-right tabular-nums">{children}</dd>
    </div>
  )
}

function Badge({ tone, children }: { tone: 'ok' | 'warn' | 'error' | 'idle'; children: ReactNode }) {
  const tones = {
    ok: 'border-ok/30 bg-ok/10 text-ok',
    warn: 'border-warn/30 bg-warn/10 text-warn',
    error: 'border-danger/30 bg-danger/10 text-danger',
    idle: 'border-border bg-muted text-muted-foreground',
  }
  return (
    <span className={cn('rounded-full border px-2 py-0.5 text-[0.65rem] font-medium', tones[tone])}>
      {children}
    </span>
  )
}

export function CurrentCycle() {
  const replay = useReplay().data
  const status = useStatus().data
  const layer = useObservation().data
  const lightning = useLightning().data
  if (!replay?.cycle_time || !status) return null

  const age = layer?.data_age_min ?? null
  const observed = layer ? layer.obs_time !== null : null
  // Fresh within one cycle interval; ageing beyond that, up to the configured limit.
  const ageTone = age === null ? 'idle' : age <= status.cycle_interval_min ? 'ok' : 'warn'
  const coverage = layer?.valid_fraction ?? null

  return (
    <section className="border-b px-4 py-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-muted-foreground text-[0.68rem] font-semibold tracking-[0.12em] uppercase">
            Current cycle
          </div>
          <div className="mt-1 font-mono text-3xl leading-none font-semibold tabular-nums">
            {utcTime(replay.cycle_time)}
            <span className="text-muted-foreground ml-1.5 text-xs font-normal">UTC</span>
          </div>
          <div className="text-muted-foreground mt-1 text-xs">{utcDate(replay.cycle_time)}</div>
        </div>
        {observed !== null && (
          <Badge tone={observed ? 'ok' : 'warn'}>{observed ? 'Observed' : 'No observation'}</Badge>
        )}
      </div>

      <dl className="mt-3.5">
        <Row label="Radar scan">
          {layer?.obs_time ? `${utcTimeSeconds(layer.obs_time)} UTC` : '—'}
        </Row>
        <Row label="Data age">
          {age === null ? '—' : <Badge tone={ageTone}>{age.toFixed(0)} min</Badge>}
        </Row>
        <Row label="Valid coverage">
          {coverage === null ? '—' : `${(coverage * 100).toFixed(1)}%`}
        </Row>
        {lightning && (
          <Row label={`Lightning, last ${status.cycle_interval_min} min`}>
            {lightning.stats.flashes !== undefined
              ? `${lightning.stats.flashes.toLocaleString('en')} flashes`
              : 'No data'}
          </Row>
        )}
      </dl>
      {coverage !== null && (
        <div className="bg-muted mt-1.5 h-1 overflow-hidden rounded-full">
          <div className="bg-primary h-full rounded-full" style={{ width: `${coverage * 100}%` }} />
        </div>
      )}
      <div className="text-muted-foreground mt-3 truncate font-mono text-[0.65rem]" title={replay.cycle_id ?? ''}>
        {replay.cycle_id}
      </div>
    </section>
  )
}

export function NowcastSection() {
  const forecast = useForecast().data
  if (!forecast) return null

  return (
    <Section icon={TrendingUp} title="Nowcast">
      <dl>
        <Row label="Status">
          <Badge tone={forecast.available ? 'ok' : 'warn'}>{forecast.available ? 'Issued' : 'Not issued'}</Badge>
        </Row>
        {forecast.available ? (
          <>
            <Row label="Storm motion">
              {forecast.motion_speed_kmh !== null && forecast.motion_toward_deg !== null
                ? `${forecast.motion_speed_kmh.toFixed(0)} km/h toward ${compass(forecast.motion_toward_deg)}`
                : '—'}
            </Row>
            <Row label="Ensemble">{forecast.ensemble_members} members</Row>
            <Row label="Compute time">
              {forecast.compute_seconds !== null ? `${forecast.compute_seconds.toFixed(1)} s` : '—'}
            </Row>
          </>
        ) : (
          <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{forecast.reason}</p>
        )}
      </dl>
      {forecast.model_name && (
        <div className="text-muted-foreground mt-2 truncate text-[0.68rem]" title={forecast.model_name}>
          {forecast.model_name}
        </div>
      )}
    </Section>
  )
}

// Forecast domain and engine state.
export function SystemPanel() {
  const connection = useConnectionStatus()
  const status = useStatus().data

  return (
    <>

      {status && (
        <Section icon={Grid3x3} title="Forecast domain">
          <dl>
            <Row label="Region">{titleCase(status.region_name)}</Row>
            <Row label="Longitude">
              {status.bounds[0].toFixed(2)}° to {status.bounds[2].toFixed(2)}°
            </Row>
            <Row label="Latitude">
              {status.bounds[1].toFixed(2)}° to {status.bounds[3].toFixed(2)}°
            </Row>
            <Row label="Grid">
              {status.grid.width} × {status.grid.height} · {status.grid.resolution_km} km
            </Row>
            <Row label="Cycle interval">{status.cycle_interval_min} min</Row>
          </dl>
          <div className="mt-2 flex items-center justify-between gap-4 text-[0.8rem]">
            <span className="text-muted-foreground">Lead times</span>
            <span className="flex gap-1">
              {status.lead_times_min.map((lead) => (
                <span key={lead} className="bg-muted rounded px-1.5 py-0.5 font-mono text-[0.68rem] tabular-nums">
                  +{lead}
                </span>
              ))}
            </span>
          </div>
        </Section>
      )}

      <Section icon={Server} title="System">
        <dl>
          <Row label="Forecast engine">
            <Badge tone={connection === 'connected' ? 'ok' : connection === 'connecting' ? 'warn' : 'error'}>
              {connection === 'connected' ? 'Online' : connection === 'connecting' ? 'Starting' : 'Offline'}
            </Badge>
          </Row>
          <Row label="Database">
            {status ? <Badge tone={status.database_ok ? 'ok' : 'error'}>{status.database_ok ? 'Healthy' : 'Error'}</Badge> : '—'}
          </Row>
          <Row label="Storm events">{status ? status.events.length : '—'}</Row>
        </dl>
      </Section>
    </>
  )
}
