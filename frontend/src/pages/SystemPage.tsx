import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { SystemPanel } from '@/components/Sidebar'
import { api } from '@/lib/api'
import { utcDate, utcTimeSeconds } from '@/lib/format'
import { useDataStatus, useReplay } from '@/lib/queries'
import { cn } from '@/lib/utils'

const SHARE = ['bg-primary', 'bg-primary/60', 'bg-warn', 'bg-ok']
const LEVEL: Record<string, string> = { warning: 'bg-warn', error: 'bg-danger' }

function Card({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="bg-card overflow-hidden rounded-lg border">
      <div className="flex items-baseline justify-between gap-4 border-b px-4 py-2.5">
        <h2 className="text-muted-foreground text-[0.68rem] font-semibold tracking-[0.12em] uppercase">{title}</h2>
        {note && <span className="text-muted-foreground truncate text-[0.68rem]">{note}</span>}
      </div>
      {children}
    </section>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1 text-[0.8rem]">
      <dt className="text-muted-foreground shrink-0">{label}</dt>
      <dd className="min-w-0 truncate text-right tabular-nums">{children}</dd>
    </div>
  )
}

const empty = 'text-muted-foreground px-4 py-4 text-xs'

const SOURCE_LABEL = { radar: 'Radar', satellite: 'Satellite', lightning: 'Lightning', model: 'Model data' }

// Every configured source with what has been ingested from it: archive files, not a live feed.
function Sources() {
  const { data, isError } = useDataStatus()
  return (
    <Card title="Data sources" note="Archive files on disk">
      {isError ? (
        <p className={empty}>Data status could not be loaded.</p>
      ) : !data ? (
        <p className={empty}>Loading data status</p>
      ) : (
        <table className="w-full text-[0.8rem] tabular-nums">
          <thead>
            <tr className="text-muted-foreground border-b text-left text-[0.68rem]">
              <th className="px-4 py-2 font-normal">Source</th>
              <th className="py-2 font-normal">ID</th>
              <th className="py-2 text-right font-normal">Files</th>
              <th className="px-4 py-2 text-right font-normal">Status</th>
            </tr>
          </thead>
          <tbody>
            {data.sources.map((source) => (
              <tr key={`${source.source_type}-${source.source_id}`} className="border-b last:border-b-0">
                <td className="px-4 py-2 font-medium">{SOURCE_LABEL[source.source_type]}</td>
                <td className="text-muted-foreground py-2 font-mono text-xs">{source.source_id}</td>
                <td className="py-2 text-right">{source.file_count.toLocaleString('en')}</td>
                <td className="px-4 py-2 text-right">
                  <span className="inline-flex items-center gap-1.5 text-xs">
                    <span className={cn('size-2 rounded-full', source.file_count > 0 ? 'bg-ok' : 'bg-warn')} />
                    {source.file_count > 0 ? 'Available' : 'No files'}
                  </span>
                </td>
              </tr>
            ))}
            {data.missing.map((type) => (
              <tr key={type} className="border-b last:border-b-0">
                <td className="px-4 py-2 font-medium">{SOURCE_LABEL[type]}</td>
                <td colSpan={2} className="text-muted-foreground py-2">
                  No source configured
                </td>
                <td className="px-4 py-2 text-right">
                  <span className="inline-flex items-center gap-1.5 text-xs">
                    <span className="bg-danger size-2 rounded-full" />
                    Not connected
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

function Model() {
  const { data, isError } = useQuery({ queryKey: ['model-info'], queryFn: api.modelInfo })
  const active = data?.active
  return (
    <Card title="Nowcast model">
      {isError ? (
        <p className={empty}>Model information could not be loaded.</p>
      ) : !data ? (
        <p className={empty}>Loading model information</p>
      ) : !active ? (
        <p className={empty}>No trained model is active.</p>
      ) : (
        <dl className="px-4 py-2.5">
          <Row label="Active version">
            <span className="font-mono text-xs">{active.model_version}</span>
          </Row>
          <Row label="Type">{active.kind}</Row>
          <Row label="Trained">
            {utcDate(active.trained_at)} {utcTimeSeconds(active.trained_at)} UTC
          </Row>
          <Row label="Training events">{active.train_events.join(', ') || '—'}</Row>
          <Row label="Versions on record">{data.versions.length}</Row>
        </dl>
      )}
    </Card>
  )
}

// Measured run time of the processing pipeline for this event.
function Processing() {
  const eventId = useReplay().data?.event_id ?? null
  const { data, isError } = useQuery({
    queryKey: ['performance', eventId],
    queryFn: () => api.performance(eventId!),
    enabled: eventId !== null,
    retry: false,
  })
  return (
    <Card title="Processing time" note={data ? `Measured ${utcDate(data.measured_at)}` : undefined}>
      {isError ? (
        <p className={empty}>No processing measurement exists for this event.</p>
      ) : !data ? (
        <p className={empty}>Loading measurement</p>
      ) : (
        <dl className="px-4 py-2.5">
          {/* Share of the total run time taken by each stage group. */}
          <div className="bg-muted mt-1 mb-2.5 flex h-2 overflow-hidden rounded-full" aria-hidden>
            {Object.entries(data.seconds_by_group).map(([group, seconds], i) => (
              <span
                key={group}
                title={`${group}: ${seconds.toFixed(1)} s`}
                className={SHARE[i % SHARE.length]}
                style={{ width: `${(seconds / data.total_seconds) * 100}%` }}
              />
            ))}
          </div>
          <Row label="Cycles processed">{data.cycles}</Row>
          <Row label="Mean per cycle">{data.mean_seconds_per_cycle.toFixed(1)} s</Row>
          <Row label="Total">{data.total_seconds.toFixed(0)} s</Row>
          {Object.entries(data.seconds_by_group).map(([group, seconds]) => (
            <Row key={group} label={group.charAt(0).toUpperCase() + group.slice(1)}>
              <span className={cn('mr-2 inline-block size-2 rounded-sm', SHARE[Object.keys(data.seconds_by_group).indexOf(group) % SHARE.length])} />
              {seconds.toFixed(1)} s
            </Row>
          ))}
        </dl>
      )}
    </Card>
  )
}

function Events() {
  const { data, isError } = useQuery({ queryKey: ['system-events'], queryFn: api.systemEvents, refetchInterval: 10000 })
  return (
    <Card title="System events" note="Most recent 50">
      {isError ? (
        <p className={empty}>System events could not be loaded.</p>
      ) : !data ? (
        <p className={empty}>Loading events</p>
      ) : data.length === 0 ? (
        <p className={empty}>No events recorded.</p>
      ) : (
        <ul className="max-h-[28rem] overflow-y-auto">
          {data.map((event, i) => (
            <li key={i} className="flex items-baseline gap-3 border-b px-4 py-1.5 text-[0.75rem] last:border-b-0">
              <span
                className={cn('size-1.5 shrink-0 translate-y-[-1px] rounded-full', LEVEL[event.level] ?? 'bg-muted-foreground/50')}
                title={event.level}
              />
              <span className="text-muted-foreground shrink-0 font-mono text-[0.68rem] tabular-nums">
                {utcDate(event.ts)} {utcTimeSeconds(event.ts)}
              </span>
              <span className="text-muted-foreground w-16 shrink-0 truncate">{event.component}</span>
              <span className="min-w-0 flex-1 leading-snug select-text">{event.message}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

// State of the data sources, the forecast domain, the model and the engine itself.
export default function SystemPage() {
  return (
    <div className="mx-auto grid max-w-6xl items-start gap-4 p-5 lg:grid-cols-[22rem_1fr]">
      <div className="bg-card overflow-hidden rounded-lg border [&>section:last-child]:border-b-0">
        <SystemPanel />
      </div>
      <div className="min-w-0 space-y-4">
        <Sources />
        <div className="grid gap-4 xl:grid-cols-2">
          <Model />
          <Processing />
        </div>
        <Events />
      </div>
    </div>
  )
}
