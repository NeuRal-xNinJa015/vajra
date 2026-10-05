import { Check, Layers } from 'lucide-react'
import { useShallow } from 'zustand/react/shallow'
import { useLightning } from '@/lib/queries'
import { cn } from '@/lib/utils'
import { useUi } from '@/store/ui'

function Switch({ checked, onChange, label }: { checked: boolean; onChange: (on: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        'focus-visible:ring-ring flex size-4 shrink-0 items-center justify-center rounded-[4px] border transition-colors outline-none focus-visible:ring-2',
        checked ? 'border-primary bg-primary text-primary-foreground' : 'border-input bg-background',
      )}
    >
      {checked && <Check className="size-3" strokeWidth={3} />}
    </button>
  )
}

export function LayerPanel() {
  // Subscribes to these fields only, so other UI state changes do not re-render the panel.
  const {
    radarVisible, radarOpacity, cellsVisible, lightningVisible,
    setRadarVisible, setRadarOpacity, setCellsVisible, setLightningVisible,
  } = useUi(
    useShallow((s) => ({
      radarVisible: s.radarVisible,
      radarOpacity: s.radarOpacity,
      cellsVisible: s.cellsVisible,
      lightningVisible: s.lightningVisible,
      setRadarVisible: s.setRadarVisible,
      setRadarOpacity: s.setRadarOpacity,
      setCellsVisible: s.setCellsVisible,
      setLightningVisible: s.setLightningVisible,
    })),
  )
  const lightning = useLightning().data

  return (
    <div className="bg-card/90 absolute top-3 left-3 w-56 rounded-lg border shadow-md backdrop-blur">
      <div className="text-muted-foreground flex items-center gap-2 border-b px-3 py-2 text-[0.68rem] font-semibold tracking-[0.12em] uppercase">
        <Layers className="size-3.5" />
        Layers
      </div>
      <div className="space-y-2.5 px-3 py-2.5">
        <div className="flex items-center gap-2.5 text-xs">
          <Switch checked={radarVisible} onChange={setRadarVisible} label="Show reflectivity" />
          <span className={cn(!radarVisible && 'text-muted-foreground')}>Reflectivity</span>
        </div>
        <div className="flex items-center gap-2.5">
          <span className="text-muted-foreground text-[0.65rem]">Opacity</span>
          <input
            type="range"
            min={0.2}
            max={1}
            step={0.05}
            value={radarOpacity}
            disabled={!radarVisible}
            onChange={(e) => setRadarOpacity(Number(e.target.value))}
            className="slider flex-1 disabled:opacity-40"
            aria-label="Reflectivity layer opacity"
          />
          <span className="text-muted-foreground w-8 text-right font-mono text-[0.65rem] tabular-nums">
            {Math.round(radarOpacity * 100)}%
          </span>
        </div>
        {lightning && (
          <div className="border-t pt-2.5">
            <div className="flex items-center gap-2.5 text-xs">
          <Switch checked={lightningVisible} onChange={setLightningVisible} label="Show lightning" />
          <span className={cn(!lightningVisible && 'text-muted-foreground')}>Lightning</span>
        </div>
            {/* Compact colour scale: the lower bound of each band. */}
            <div className="text-muted-foreground mt-1.5 flex items-center gap-1.5 text-[0.62rem]">
              {lightning.legend.map((entry) => (
                <span key={entry.label} className="flex items-center gap-1">
                  <span className="size-2 rounded-sm ring-1 ring-border" style={{ background: entry.color }} />
                  {entry.label}
                </span>
              ))}
            </div>
            <div className="text-muted-foreground mt-0.5 text-[0.62rem]">{lightning.units}, last cycle</div>
          </div>
        )}
        <div className="flex items-center gap-2.5 border-t pt-2.5 text-xs">
          <Switch checked={cellsVisible} onChange={setCellsVisible} label="Show storm cells" />
          <span className={cn(!cellsVisible && 'text-muted-foreground')}>Storm cells</span>
        </div>
      </div>
    </div>
  )
}
