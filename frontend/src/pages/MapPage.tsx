import { useEffect } from 'react'
import { CellList, StormPanel } from '@/components/CellsPanel'
import { LeadBar } from '@/components/LeadBar'
import { MapView } from '@/components/MapView'
import { ReplayBar } from '@/components/ReplayBar'
import { useUi } from '@/store/ui'

// The map workspace. It stays mounted while another page is open, so returning to it
// is instant; `active` is false while it is hidden.
export function MapPage({ active }: { active: boolean }) {
  const setLeadAnimating = useUi((s) => s.setLeadAnimating)

  // A hidden map has no reason to keep stepping through lead times.
  useEffect(() => {
    if (!active) setLeadAnimating(false)
  }, [active, setLeadAnimating])

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <MapView>
          <CellList />
        </MapView>
        <LeadBar />
        <ReplayBar shortcuts={active} />
      </div>
      <StormPanel />
    </div>
  )
}
