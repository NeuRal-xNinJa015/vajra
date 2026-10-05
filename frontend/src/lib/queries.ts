import { useMutation, useQueries, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useUi } from '@/store/ui'
import { api, type AlertDecision, type ReplayState, type Timeline } from './api'

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: 3000,
    retry: false,
  })
}

export function useConnectionStatus(): ConnectionStatus {
  const health = useHealth()
  if (health.isSuccess) return 'connected'
  return health.isError ? 'disconnected' : 'connecting'
}

export function useStatus() {
  const connected = useConnectionStatus() === 'connected'
  return useQuery({
    queryKey: ['status'],
    queryFn: api.status,
    enabled: connected,
    refetchInterval: 10000,
  })
}

export function useDataStatus() {
  const connected = useConnectionStatus() === 'connected'
  return useQuery({
    queryKey: ['data-status'],
    queryFn: api.dataStatus,
    enabled: connected,
    refetchInterval: 10000,
  })
}

// ---- replay ---------------------------------------------------------------

// The backend owns the replay clock; the UI follows it, polling faster while it runs.
export function useReplay() {
  const connected = useConnectionStatus() === 'connected'
  return useQuery({
    queryKey: ['replay'],
    queryFn: api.replay,
    enabled: connected,
    refetchInterval: (query) => (query.state.data?.status === 'playing' ? 200 : 2000),
  })
}

// Show the result of a control immediately, then let the backend's answer replace it.
function applyNow(client: QueryClient, change: (state: ReplayState) => Partial<ReplayState>) {
  client.cancelQueries({ queryKey: ['replay'] })
  client.setQueryData<ReplayState>(['replay'], (state) => (state ? { ...state, ...change(state) } : state))
}

function atCycle(client: QueryClient, state: ReplayState, index: number): Partial<ReplayState> {
  const timeline = client.getQueryData<Timeline>(['timeline', state.event_id])
  return {
    cycle_index: index,
    cycle_time: state.cycle_times[index],
    cycle_id: timeline?.cycles[index]?.cycle_id ?? state.cycle_id,
  }
}

export function useReplayControls() {
  const client = useQueryClient()
  const settle = {
    onSuccess: (state: ReplayState) => client.setQueryData(['replay'], state),
    onError: () => client.invalidateQueries({ queryKey: ['replay'] }),
  }
  return {
    play: useMutation({
      mutationFn: api.replayPlay,
      onMutate: () => applyNow(client, () => ({ status: 'playing' })),
      ...settle,
    }).mutate,
    pause: useMutation({
      mutationFn: api.replayPause,
      onMutate: () => applyNow(client, () => ({ status: 'paused' })),
      ...settle,
    }).mutate,
    reset: useMutation({
      mutationFn: api.replayReset,
      onMutate: () => applyNow(client, (state) => ({ status: 'paused', ...atCycle(client, state, 0) })),
      ...settle,
    }).mutate,
    seek: useMutation({
      mutationFn: api.replaySeek,
      onMutate: (index: number) => applyNow(client, (state) => atCycle(client, state, index)),
      ...settle,
    }).mutate,
    setSpeed: useMutation({
      mutationFn: api.replaySpeed,
      onMutate: (speed: number) => applyNow(client, () => ({ speed })),
      ...settle,
    }).mutate,
  }
}

// ---- observations and forecasts -------------------------------------------

// Layers for every cycle of the event, fetched once. Changing cycle or lead
// time then needs no request at all.
export function useTimeline() {
  const eventId = useReplay().data?.event_id ?? null
  return useQuery({
    queryKey: ['timeline', eventId],
    queryFn: () => api.timeline(eventId!),
    enabled: eventId !== null,
    staleTime: Infinity,
  })
}

function useCurrentCycle() {
  const index = useReplay().data?.cycle_index
  const timeline = useTimeline().data
  return index === undefined ? undefined : timeline?.cycles[index]
}

// The observed field for the cycle the replay is on.
export function useObservation() {
  return { data: useCurrentCycle()?.observation }
}

// Observed lightning for the interval before the cycle the replay is on.
export function useLightning() {
  return { data: useCurrentCycle()?.lightning ?? undefined }
}

// The forecast issued at the cycle the replay is on.
export function useForecast() {
  return { data: useCurrentCycle()?.forecast }
}

// What the map shows: the observation at lead 0, otherwise the chosen forecast
// product for that lead. If the ML model could not predict that lead at this cycle,
// the reflectivity forecast is shown instead; with no forecast at all, the observation.
export function useDisplayedLayer() {
  const leadTime = useUi((s) => s.leadTime)
  const product = useUi((s) => s.product)
  const observation = useObservation().data
  const forecast = useForecast().data
  const at = (variable: string) =>
    leadTime > 0
      ? forecast?.layers.find((layer) => layer.variable === variable && layer.lead_time_min === leadTime)
      : undefined
  const wanted = at(product)
  const forecastLayer = wanted ?? at('forecast_reflectivity')
  return {
    layer: forecastLayer ?? observation,
    isForecast: forecastLayer !== undefined,
    forecastMissing: leadTime > 0 && forecast !== undefined && forecastLayer === undefined,
    productMissing: forecastLayer !== undefined && wanted === undefined,
  }
}

// Measured skill of the ML nowcast for the event on screen.
export function useVerification() {
  const eventId = useReplay().data?.event_id ?? null
  return useQuery({
    queryKey: ['verification', eventId],
    queryFn: () => api.verification(eventId!),
    enabled: eventId !== null,
    staleTime: Infinity,
    retry: false,
  })
}

// ---- alerts ---------------------------------------------------------------

// Alerts at the cycle the replay is on. The backend suggests; the forecaster decides.
export function useAlerts() {
  const cycleId = useReplay().data?.cycle_id ?? null
  return useQuery({
    queryKey: ['alerts', cycleId],
    queryFn: () => api.alerts(cycleId!),
    enabled: cycleId !== null,
  })
}

// Every alert from the start of the event up to the cycle the replay is on, newest
// cycle first. Later cycles are not asked for: asking makes the backend suggest
// alerts for a cycle, and that must not happen before the replay reaches it.
export function useAlertsSoFar() {
  const replay = useReplay().data
  const timeline = useTimeline().data
  const cycles = timeline && replay ? timeline.cycles.slice(0, replay.cycle_index + 1) : []
  return useQueries({
    queries: cycles.map((cycle) => ({
      queryKey: ['alerts', cycle.cycle_id],
      queryFn: () => api.alerts(cycle.cycle_id),
    })),
    combine: (results) => ({
      alerts: results.flatMap((result) => result.data?.alerts ?? []).reverse(),
      loading: results.some((result) => result.isPending),
    }),
  })
}

export function useAlertActions() {
  const client = useQueryClient()
  const refresh = { onSuccess: () => client.invalidateQueries({ queryKey: ['alerts'] }) }
  return {
    preview: useMutation({
      mutationFn: ({ cycleId, cellId }: { cycleId: string; cellId: string }) => api.alertPreview(cycleId, cellId),
      ...refresh,
    }),
    approve: useMutation({
      mutationFn: ({ alertId, decision }: { alertId: string; decision: AlertDecision }) =>
        api.alertApprove(alertId, decision),
      ...refresh,
    }),
    reject: useMutation({
      mutationFn: ({ alertId, decision }: { alertId: string; decision: AlertDecision }) =>
        api.alertReject(alertId, decision),
      ...refresh,
    }),
  }
}

// ---- feedback and explanation ---------------------------------------------

// Forecaster feedback recorded for a cycle (the one the replay is on by default).
export function useFeedback(forCycleId?: string) {
  const currentCycleId = useReplay().data?.cycle_id ?? null
  const cycleId = forCycleId ?? currentCycleId
  return useQuery({
    queryKey: ['feedback', cycleId],
    queryFn: () => api.feedback(cycleId!),
    enabled: cycleId !== null,
  })
}

export function useAddFeedback() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.feedbackAdd,
    onSuccess: () => client.invalidateQueries({ queryKey: ['feedback'] }),
  })
}

// Why the ML model gave a cell its lightning probability at a lead time.
export function useExplanation(cycleId: string, cellId: string, leadMin: number | null) {
  return useQuery({
    queryKey: ['explanation', cycleId, cellId, leadMin],
    queryFn: () => api.explanation(cycleId, cellId, leadMin!),
    enabled: leadMin !== null,
    staleTime: Infinity,
  })
}

// ---- storm cells ----------------------------------------------------------

// Storm cells at the cycle the replay is on.
export function useStormCells() {
  const cycleId = useReplay().data?.cycle_id ?? null
  return useQuery({
    queryKey: ['storm-cells', cycleId],
    queryFn: () => api.stormCells(cycleId!),
    enabled: cycleId !== null,
    staleTime: Infinity, // a historical cycle never changes
  })
}
