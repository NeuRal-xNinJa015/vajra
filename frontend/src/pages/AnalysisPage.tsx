import type { ReliabilityBin, SkillRow, Verification } from '@/lib/api'
import { useVerification } from '@/lib/queries'
import { cn } from '@/lib/utils'

const TARGETS: { key: SkillRow['target']; title: string }[] = [
  { key: 'storm', title: 'Storm' },
  { key: 'lightning', title: 'Lightning' },
]

const heading = 'text-muted-foreground text-[0.68rem] font-semibold tracking-[0.12em] uppercase'

function SkillTable({ rows }: { rows: SkillRow[] }) {
  return (
    <table className="w-full text-[0.78rem] tabular-nums">
      <thead>
        <tr className="text-muted-foreground text-left text-[0.68rem]">
          <th className="py-1.5 font-normal">Lead · method</th>
          {['CSI', 'POD', 'FAR', 'Brier', 'AUC'].map((name) => (
            <th key={name} className="py-1.5 text-right font-normal">
              {name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) =>
          row.methods.length === 0 ? (
            <tr key={row.lead_min} className="border-t">
              <td className="py-2 font-mono">+{row.lead_min}</td>
              <td colSpan={5} className="text-muted-foreground py-2 text-right">
                Not verifiable with this event
              </td>
            </tr>
          ) : (
            row.methods.map((method, i) => (
              <tr key={`${row.lead_min}-${method.method}`} className={cn(i === 0 ? 'border-t font-medium' : 'text-muted-foreground')}>
                <td className="py-1.5">
                  <span className={cn('inline-block w-10 font-mono', i > 0 && 'invisible')}>+{row.lead_min}</span>
                  {method.method}
                </td>
                <td className="py-1.5 text-right">{method.csi.toFixed(2)}</td>
                <td className="py-1.5 text-right">{method.pod.toFixed(2)}</td>
                <td className="py-1.5 text-right">{method.far.toFixed(2)}</td>
                <td className="py-1.5 text-right">{method.brier.toFixed(3)}</td>
                <td className="py-1.5 text-right">{method.auc !== null ? method.auc.toFixed(2) : '—'}</td>
              </tr>
            ))
          ),
        )}
      </tbody>
    </table>
  )
}

// Critical success index of each method at each verified lead time, as bars.
function CsiBars({ rows }: { rows: SkillRow[] }) {
  const verified = rows.filter((row) => row.methods.length > 0)
  if (verified.length === 0) return null
  return (
    <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {verified.map((row) => (
        <div key={row.lead_min}>
          <div className="text-muted-foreground mb-1.5 text-[0.68rem]">
            CSI at <span className="font-mono">+{row.lead_min} min</span>
          </div>
          <ul className="space-y-1.5">
            {row.methods.map((method, i) => (
              <li key={method.method} className="flex items-center gap-2 text-[0.72rem]">
                <span className={cn('w-24 shrink-0 truncate', i > 0 && 'text-muted-foreground')} title={method.method}>
                  {method.method}
                </span>
                <span className="bg-muted h-2 flex-1 overflow-hidden rounded-full">
                  <span
                    className={cn('block h-full rounded-full', i === 0 ? 'bg-primary' : 'bg-muted-foreground/60')}
                    style={{ width: `${Math.max(0, Math.min(1, method.csi)) * 100}%` }}
                  />
                </span>
                <span className="w-8 text-right font-mono tabular-nums">{method.csi.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

// Calibration: in each probability band, what the model predicted against how often
// it happened. On the diagram a perfectly calibrated model lies on the diagonal.
function Reliability({ bins }: { bins: ReliabilityBin[] }) {
  const at = (bin: ReliabilityBin) => `${(bin.mean_predicted * 100).toFixed(1)},${(100 - bin.observed_rate * 100).toFixed(1)}`
  return (
    <div className="flex flex-wrap items-start gap-5">
      <figure className="w-40 shrink-0">
        <svg viewBox="-2 -2 104 104" className="w-full" role="img" aria-label="Reliability diagram: observed frequency against predicted probability">
          <rect x="0" y="0" width="100" height="100" fill="none" className="stroke-border" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {[25, 50, 75].map((v) => (
            <g key={v} className="stroke-border" strokeWidth="0.5">
              <line x1={v} y1="0" x2={v} y2="100" vectorEffect="non-scaling-stroke" />
              <line x1="0" y1={v} x2="100" y2={v} vectorEffect="non-scaling-stroke" />
            </g>
          ))}
          <line x1="0" y1="100" x2="100" y2="0" className="stroke-muted-foreground" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
          <polyline points={bins.map(at).join(' ')} fill="none" className="stroke-primary" strokeWidth="1.75" vectorEffect="non-scaling-stroke" />
          {bins.map((bin) => {
            const [x, y] = at(bin).split(',')
            return <circle key={bin.from} cx={x} cy={y} r="2.5" className="fill-primary" />
          })}
        </svg>
        <figcaption className="text-muted-foreground mt-1 text-[0.62rem] leading-snug">
          Across: predicted. Up: observed. Dashed line: perfect calibration.
        </figcaption>
      </figure>
      <table className="min-w-0 flex-1 text-[0.78rem] tabular-nums">
        <thead>
          <tr className="text-muted-foreground text-left text-[0.68rem]">
            <th className="py-1.5 font-normal">Predicted band</th>
            <th className="py-1.5 text-right font-normal">Predicted</th>
            <th className="py-1.5 text-right font-normal">Observed</th>
            <th className="py-1.5 text-right font-normal">Grid cells</th>
          </tr>
        </thead>
        <tbody>
          {bins.map((bin) => (
            <tr key={bin.from} className="border-t">
              <td className="py-1.5 font-mono">
                {(bin.from * 100).toFixed(0)}–{(bin.to * 100).toFixed(0)}%
              </td>
              <td className="py-1.5 text-right">{(bin.mean_predicted * 100).toFixed(0)}%</td>
              <td className="py-1.5 text-right">{(bin.observed_rate * 100).toFixed(0)}%</td>
              <td className="text-muted-foreground py-1.5 text-right">{bin.cells.toLocaleString('en')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Target({ data, target, title }: { data: Verification; target: SkillRow['target']; title: string }) {
  const rows = data.rows.filter((row) => row.target === target)
  const bins = data.reliability.find((item) => item.target === target)?.bins
  const verified = rows.find((row) => row.methods.length > 0)

  return (
    <section className="bg-card rounded-lg border">
      <div className="flex items-baseline justify-between border-b px-4 py-2.5">
        <h2 className={heading}>{title}</h2>
        {verified && (
          <span className="text-muted-foreground text-[0.68rem] tabular-nums">
            +{verified.lead_min} min: {verified.cycles} cycles, {verified.cells.toLocaleString('en')} grid cells
          </span>
        )}
      </div>
      <div className="border-b px-4 py-3">
        <CsiBars rows={rows} />
      </div>
      <div className="px-4 py-3">
        <SkillTable rows={rows} />
      </div>
      <div className="border-t px-4 py-3">
        <h3 className="mb-2 text-xs font-medium">Reliability</h3>
        {bins && bins.length > 0 ? (
          <Reliability bins={bins} />
        ) : (
          <p className="text-muted-foreground text-xs">Not available for this event.</p>
        )}
      </div>
    </section>
  )
}

// Measured skill of the ML nowcast against simpler methods, and how well its
// probabilities are calibrated. Every number comes from the backend's verification run.
export default function AnalysisPage() {
  const { data, isError, error } = useVerification()

  if (isError) return <p className="text-muted-foreground p-6 text-xs">{error.message}</p>
  if (!data) return <p className="text-muted-foreground p-6 text-xs">Loading verification</p>

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-5">
      <div>
        <p className="text-[0.8rem] leading-relaxed">{data.method}</p>
        <p className="text-muted-foreground mt-1 font-mono text-[0.68rem]">
          Model {data.model_version} · event {data.event_id}
        </p>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {TARGETS.map(({ key, title }) => (
          <Target key={key} data={data} target={key} title={title} />
        ))}
      </div>
      <p className="text-muted-foreground text-[0.7rem] leading-relaxed">
        Storm: reflectivity of {data.storm_threshold_dbz} dBZ or more. Lightning: a flash within{' '}
        {data.lightning_neighbourhood_km} km. Higher CSI, POD and AUC are better; lower FAR and Brier are better. On the
        reliability diagram, points below the dashed line mean the model was overconfident in that band.
      </p>
    </div>
  )
}
