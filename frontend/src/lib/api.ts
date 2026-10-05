// Types mirror backend/vajra/contracts.py. All datetimes are UTC ISO strings.
import type { Geometry } from 'geojson'
import { useSession } from './session'

export type SourceType = 'radar' | 'satellite' | 'lightning' | 'model'
export type Bounds = [west: number, south: number, east: number, north: number]

export interface Health {
  status: 'ok'
  version: string
  auth_required: boolean
}

export interface UserInfo {
  username: string
  display_name: string
  role: 'viewer' | 'forecaster' | 'admin'
}

export interface LoginResponse {
  token: string
  expires_at: string
  user: UserInfo
}

export interface Status {
  app: string
  version: string
  mode: 'replay'
  region_name: string
  bounds: Bounds
  grid: { resolution_km: number; height: number; width: number }
  cycle_interval_min: number
  lead_times_min: number[]
  database_ok: boolean
  events: string[]
  server_time: string
}

export interface SourceStatus {
  source_type: SourceType
  source_id: string
  path: string
  file_count: number
  latest_obs_time: string | null
  data_age_min: number | null
}

export interface DataStatus {
  sources: SourceStatus[]
  missing: SourceType[]
}

export interface FieldLayer {
  variable: string
  units: string
  lead_time_min: number
  valid_time: string
  image_url: string
  bounds: Bounds
  obs_time: string | null
  data_age_min: number | null
  valid_fraction: number | null
  legend: { label: string; color: string }[]
  stats: Record<string, number>
}

export interface Forecast {
  cycle_id: string
  cycle_time: string
  available: boolean
  reason: string | null
  model_name: string | null
  model_version: string | null
  ensemble_members: number | null
  compute_seconds: number | null
  motion_speed_kmh: number | null
  motion_toward_deg: number | null
  layers: FieldLayer[]
}

export interface TimelineCycle {
  cycle_id: string
  cycle_time: string
  observation: FieldLayer
  lightning: FieldLayer | null
  forecast: Forecast
}

export interface Timeline {
  event_id: string
  cycles: TimelineCycle[]
}

export type Severity = 'moderate' | 'strong' | 'severe'

export interface StormCell {
  cell_id: string
  cycle_id: string
  track_id: string
  lead_time_min: number
  centroid: [lon: number, lat: number]
  polygon: Geometry
  area_km2: number
  severity: Severity | null
  probability: number | null
  motion_dir_deg: number | null
  speed_kmh: number | null
  uncertainty: number | null
  max_dbz: number | null
  mean_dbz: number | null
  ml_storm_probability: number | null
  ml_lightning_probability: number | null
}

export type RiskLevel = 'low' | 'moderate' | 'high' | 'severe'

export interface RiskAssessment {
  level: RiskLevel
  likelihood: number | null
  likelihood_source: 'ml' | 'ensemble' | 'none'
  factors: string[]
}

export interface StormCellTrack {
  track_id: string
  first_seen: string
  cell: StormCell
  projections: StormCell[]
  history: { cycle_time: string; centroid: [number, number]; max_dbz: number | null }[]
  risk: RiskAssessment
}

export interface Alert {
  alert_id: string
  cycle_id: string
  cell_id: string | null
  track_id: string | null
  risk_level: RiskLevel
  headline: string
  description: string
  polygon: Geometry
  valid_from: string
  valid_to: string
  status: 'suggested' | 'approved' | 'edited' | 'rejected'
  reviewed_by: string | null
  reviewed_at: string | null
  cap_xml: string | null
}

export interface Explanation {
  cycle_id: string
  cell_id: string | null
  lead_time_min: number
  target: 'storm' | 'lightning'
  available: boolean
  reason: string | null
  probability: number | null
  base_probability: number | null
  contributions: {
    feature: string
    description: string
    source: string
    value: number | null
    contribution: number
  }[]
}

export type FeedbackVerdict = 'correct' | 'partly_correct' | 'incorrect'

export interface Feedback {
  feedback_id: number
  cycle_id: string
  alert_id: string | null
  cell_id: string | null
  forecaster: string
  verdict: FeedbackVerdict
  comment: string | null
  created_at: string
}

export type FeedbackIn = Omit<Feedback, 'feedback_id' | 'created_at'>

export interface AlertDecision {
  forecaster: string
  risk_level?: RiskLevel
  headline?: string
  description?: string
}

export interface StormCells {
  cycle_id: string
  cells: StormCellTrack[]
}

export interface SkillMethod {
  method: string
  pod: number
  far: number
  csi: number
  brier: number
  auc: number | null
}

export interface SkillRow {
  target: 'storm' | 'lightning'
  lead_min: number
  cycles: number
  cells: number
  positive_rate: number | null
  methods: SkillMethod[]
}

export interface ReliabilityBin {
  from: number
  to: number
  cells: number
  mean_predicted: number
  observed_rate: number
}

export interface Verification {
  event_id: string
  model_version: string
  method: string
  storm_threshold_dbz: number
  lightning_neighbourhood_km: number
  rows: SkillRow[]
  reliability: { target: 'storm' | 'lightning'; bins: ReliabilityBin[] }[]
}

export interface ModelVersion {
  model_version: string
  kind: string
  trained_at: string
  train_events: string[]
  is_active: boolean
}

export interface ModelInfo {
  active: ModelVersion | null
  versions: ModelVersion[]
}

export interface SystemEvent {
  ts: string
  level: string
  component: string
  message: string
}

export interface Performance {
  event_id: string
  measured_at: string
  cycles: number
  seconds_by_group: Record<string, number>
  total_seconds: number
  mean_seconds_per_cycle: number
}

export type ReplayStatus = 'no_event' | 'paused' | 'playing' | 'finished'

export interface ReplayState {
  mode: 'replay'
  status: ReplayStatus
  event_id: string | null
  cycle_id: string | null
  cycle_index: number
  cycle_count: number
  cycle_time: string | null
  cycle_times: string[]
  cycle_has_observation: boolean[]
  speed: number
  speeds: number[]
  seconds_per_cycle: number
}

const FALLBACK_URL = 'http://127.0.0.1:8756'

let backendUrl: Promise<string> | undefined
let resolvedUrl: string | undefined

// The desktop shell owns the backend process and tells us where it is.
// In a plain browser (vite dev without Tauri) we fall back to the default port.
function getBackendUrl(): Promise<string> {
  backendUrl ??= (
    '__TAURI_INTERNALS__' in window
      ? import('@tauri-apps/api/core').then(({ invoke }) => invoke<string>('backend_url'))
      : Promise.resolve(FALLBACK_URL)
  ).then((url) => (resolvedUrl = url))
  return backendUrl
}

function authHeader(): Record<string, string> {
  const token = useSession.getState().token
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// For requests the map makes itself (images): the sign-in header, for backend URLs only.
export function backendRequestHeaders(url: string): Record<string, string> {
  return resolvedUrl !== undefined && url.startsWith(resolvedUrl) ? authHeader() : {}
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const base = await getBackendUrl()
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { ...init.headers, ...authHeader() },
  })
  if (response.ok) {
    return (response.status === 204 ? undefined : await response.json()) as T
  }
  // The session ended on the backend (expired, or the backend restarted): back to sign-in.
  if (response.status === 401 && path !== '/auth/login') useSession.getState().end()
  // The backend explains refusals in `detail`; show that rather than a status code.
  const detail = await response
    .json()
    .then((body: { detail?: unknown }) => (typeof body.detail === 'string' ? body.detail : null))
    .catch(() => null)
  throw new Error(detail ?? `${path} failed: ${response.status} ${response.statusText}`)
}

function get<T>(path: string): Promise<T> {
  return request<T>(path, {})
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export const api = {
  health: () => get<Health>('/health'),
  login: (username: string, password: string) => post<LoginResponse>('/auth/login', { username, password }),
  logout: () => post<void>('/auth/logout'),
  status: () => get<Status>('/status'),
  dataStatus: () => get<DataStatus>('/data-status'),
  modelInfo: () => get<ModelInfo>('/model-info'),
  systemEvents: () => get<SystemEvent[]>('/system-events?limit=50'),
  performance: (eventId: string) => get<Performance>(`/performance/${eventId}`),
  replay: () => get<ReplayState>('/replay'),
  replayPlay: () => post<ReplayState>('/replay/play'),
  replayPause: () => post<ReplayState>('/replay/pause'),
  replayReset: () => post<ReplayState>('/replay/reset'),
  replaySeek: (index: number) => post<ReplayState>('/replay/seek', { index }),
  replaySpeed: (speed: number) => post<ReplayState>('/replay/speed', { speed }),
  // Every cycle's layers in one request. Image URLs are made absolute so the map can load them.
  timeline: async (eventId: string): Promise<Timeline> => {
    const timeline = await get<Timeline>(`/events/${eventId}/timeline`)
    const base = await getBackendUrl()
    const absolute = (layer: FieldLayer) => ({ ...layer, image_url: `${base}${layer.image_url}` })
    return {
      ...timeline,
      cycles: timeline.cycles.map((cycle) => ({
        ...cycle,
        observation: absolute(cycle.observation),
        lightning: cycle.lightning && absolute(cycle.lightning),
        forecast: { ...cycle.forecast, layers: cycle.forecast.layers.map(absolute) },
      })),
    }
  },
  stormCells: (cycleId: string) => get<StormCells>(`/storm-cells?cycle_id=${cycleId}`),
  verification: (eventId: string) => get<Verification>(`/verification/${eventId}`),
  alerts: (cycleId: string) => get<{ cycle_id: string; alerts: Alert[] }>(`/alerts?cycle_id=${cycleId}`),
  alertPreview: (cycleId: string, cellId: string) =>
    post<Alert>('/alerts/preview', { cycle_id: cycleId, cell_id: cellId }),
  alertApprove: (alertId: string, decision: AlertDecision) => post<Alert>(`/alerts/${alertId}/approve`, decision),
  alertReject: (alertId: string, decision: AlertDecision) => post<Alert>(`/alerts/${alertId}/reject`, decision),
  explanation: (cycleId: string, cellId: string, leadMin: number) =>
    get<Explanation>(`/explanation/${cycleId}?cell_id=${cellId}&lead_min=${leadMin}&target=lightning`),
  feedback: (cycleId: string) => get<{ cycle_id: string; feedback: Feedback[] }>(`/feedback?cycle_id=${cycleId}`),
  feedbackAdd: (item: FeedbackIn) => post<Feedback>('/feedback', item),
}
