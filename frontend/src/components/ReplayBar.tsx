import { useEffect } from 'react'
import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { ReplayStatus } from '@/lib/api'
import { utcTime } from '@/lib/format'
import { useReplay, useReplayControls } from '@/lib/queries'
import { useCanAct } from '@/lib/session'
import { cn } from '@/lib/utils'

const STATUS: Record<ReplayStatus, { label: string; dot: string }> = {
  no_event: { label: 'No event', dot: 'bg-muted-foreground' },
  playing: { label: 'Playing', dot: 'bg-ok animate-pulse' },
  paused: { label: 'Paused', dot: 'bg-warn' },
  finished: { label: 'End of event', dot: 'bg-muted-foreground' },
}

// `shortcuts` is false while the map page is hidden, so keys typed on another page
// never drive the replay.
export function ReplayBar({ shortcuts = true }: { shortcuts?: boolean }) {
  const replay = useReplay().data
  const { play, pause, reset, seek, setSpeed } = useReplayControls()
  const canAct = useCanAct()

  const active = replay !== undefined && replay.status !== 'no_event' && shortcuts && canAct
  const playing = replay?.status === 'playing'
  const index = replay?.cycle_index ?? 0
  const last = (replay?.cycle_count ?? 1) - 1

  // Space plays or pauses, the arrow keys step one cycle, Home rewinds.
  useEffect(() => {
    if (!active) return
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.ctrlKey || event.metaKey || event.altKey) return
      if (event.code === 'Space') {
        event.preventDefault()
        if (playing) pause()
        else play()
      } else if (event.key === 'ArrowRight' && index < last) seek(index + 1)
      else if (event.key === 'ArrowLeft' && index > 0) seek(index - 1)
      else if (event.key === 'Home') reset()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, playing, index, last, play, pause, seek, reset])

  if (!replay || replay.status === 'no_event') return null
  const status = STATUS[replay.status]

  return (
    <div
      className={cn(
        'bg-background flex h-[4.25rem] shrink-0 items-center gap-3 border-t px-4 xl:gap-5',
        // A viewer account can watch the replay but not drive it.
        !canAct && 'pointer-events-none opacity-60',
      )}
      title={canAct ? undefined : 'Your account can view the replay but not control it'}
    >
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon" onClick={() => reset()} title="Back to start (Home)" aria-label="Back to start">
          <RotateCcw />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          disabled={index === 0}
          onClick={() => seek(index - 1)}
          title="Previous cycle (←)"
          aria-label="Previous cycle"
        >
          <ChevronLeft />
        </Button>
        <Button
          size="icon-lg"
          className="mx-1 rounded-full"
          onClick={() => (playing ? pause() : play())}
          title={playing ? 'Pause (Space)' : 'Play (Space)'}
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing ? <Pause className="fill-current" /> : <Play className="fill-current" />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          disabled={index === last}
          onClick={() => seek(index + 1)}
          title="Next cycle (→)"
          aria-label="Next cycle"
        >
          <ChevronRight />
        </Button>
      </div>

      <div className="flex flex-col gap-1">
        <div className="bg-muted flex rounded-md p-0.5" role="group" aria-label="Replay speed">
          {replay.speeds.map((speed) => (
            <button
              key={speed}
              type="button"
              onClick={() => setSpeed(speed)}
              aria-pressed={speed === replay.speed}
              className={cn(
                'rounded px-2 py-0.5 font-mono text-[0.7rem] tabular-nums transition-colors',
                speed === replay.speed
                  ? 'bg-background text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {speed}×
            </button>
          ))}
        </div>
        <div className="text-muted-foreground flex items-center gap-1.5 text-[0.65rem] whitespace-nowrap">
          <span className={cn('size-1.5 rounded-full', status.dot)} />
          {status.label} · {replay.seconds_per_cycle.toFixed(1)} s per cycle
        </div>
      </div>

      {/* One segment per cycle; click to jump. Dashed amber marks a cycle with no observation. */}
      <div className="min-w-0 flex-1">
        <div className="flex h-6 items-center gap-[3px]">
          {replay.cycle_times.map((time, i) => {
            const observed = replay.cycle_has_observation[i]
            return (
              <button
                key={time}
                type="button"
                onClick={() => seek(i)}
                title={`${utcTime(time)} UTC${observed ? '' : ' · no observation'}`}
                aria-label={`Go to ${utcTime(time)} UTC`}
                aria-current={i === index}
                className="group flex h-full flex-1 items-center outline-none"
              >
                <span
                  className={cn(
                    'block w-full rounded-full transition-all group-hover:h-2.5 group-focus-visible:ring-2 group-focus-visible:ring-ring',
                    i === index ? 'h-2.5' : 'h-1.5',
                    !observed
                      ? cn('border border-dashed border-warn', i === index && 'bg-warn/40')
                      : i === index
                        ? 'bg-primary'
                        : i < index
                          ? 'bg-primary/45'
                          : 'bg-muted group-hover:bg-accent',
                  )}
                />
              </button>
            )
          })}
        </div>
        <div className="text-muted-foreground mt-0.5 flex justify-between font-mono text-[0.62rem] tabular-nums">
          <span>{utcTime(replay.cycle_times[0])}</span>
          <span>{utcTime(replay.cycle_times[last])} UTC</span>
        </div>
      </div>

      <div className="w-24 shrink-0 text-right leading-tight whitespace-nowrap">
        <div className="font-mono text-xl font-semibold tabular-nums">
          {replay.cycle_time ? utcTime(replay.cycle_time) : '—'}
        </div>
        <div className="text-muted-foreground text-[0.65rem]">
          Cycle {index + 1} of {replay.cycle_count}
        </div>
      </div>
    </div>
  )
}
