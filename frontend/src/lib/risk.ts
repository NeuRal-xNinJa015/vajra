import type { Alert, RiskLevel } from './api'

// Colours come from the theme's status tokens, so they hold in light and dark.
export const RISK: Record<RiskLevel, { label: string; badge: string; dot: string }> = {
  severe: { label: 'Severe', badge: 'border-danger/40 bg-danger/10 text-danger', dot: 'bg-danger' },
  high: { label: 'High', badge: 'border-high/40 bg-high/10 text-high', dot: 'bg-high' },
  moderate: { label: 'Moderate', badge: 'border-warn/40 bg-warn/10 text-warn', dot: 'bg-warn' },
  low: { label: 'Low', badge: 'border-border bg-muted text-muted-foreground', dot: 'bg-muted-foreground' },
}

export const ALERT_STATUS: Record<Alert['status'], string> = {
  suggested: 'Awaiting review',
  approved: 'Approved',
  edited: 'Approved with edits',
  rejected: 'Rejected',
}
