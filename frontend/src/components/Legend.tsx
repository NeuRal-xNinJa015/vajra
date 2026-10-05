import type { FieldLayer } from '@/lib/api'

export function Legend({ layer, title }: { layer: FieldLayer; title: string }) {
  const scale = layer.legend.filter((entry) => /^\d+$/.test(entry.label))
  const flags = layer.legend.filter((entry) => !/^\d+$/.test(entry.label))

  return (
    <div className="bg-card/90 absolute bottom-3 left-3 w-72 rounded-lg border px-3 py-2.5 shadow-md backdrop-blur">
      <div className="mb-1.5 flex items-baseline justify-between text-[0.7rem]">
        <span className="font-medium">{title}</span>
        <span className="text-muted-foreground">{layer.units}</span>
      </div>
      <div className="flex h-2.5 overflow-hidden rounded-sm">
        {scale.map((entry) => (
          <span key={entry.label} className="flex-1" style={{ background: entry.color }} />
        ))}
      </div>
      {/* Each label sits at the left edge of its colour: the lower bound of that band. */}
      <div className="text-muted-foreground mt-1 flex font-mono text-[0.6rem] tabular-nums">
        {scale.map((entry, i) => (
          <span key={entry.label} className="flex-1">
            {i % 2 === 0 ? entry.label : ''}
          </span>
        ))}
      </div>
      <div className="text-muted-foreground mt-2 flex gap-4 border-t pt-2 text-[0.65rem]">
        {flags.map((entry) => (
          <span key={entry.label} className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm ring-1 ring-border" style={{ background: entry.color }} />
            {entry.label}
          </span>
        ))}
      </div>
    </div>
  )
}
