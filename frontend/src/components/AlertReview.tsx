import { useState } from 'react'
import { Button } from '@/components/ui/button'
import type { Alert, FeedbackVerdict, RiskLevel } from '@/lib/api'
import { utcTime } from '@/lib/format'
import { useAddFeedback, useAlertActions, useFeedback } from '@/lib/queries'
import { ALERT_STATUS, RISK } from '@/lib/risk'
import { useCanAct, useSession } from '@/lib/session'
import { cn } from '@/lib/utils'
import { useUi } from '@/store/ui'

const field =
  'bg-background w-full rounded-md border border-input px-2.5 py-1.5 text-[0.8rem] outline-none focus:border-primary'

const VERDICTS: { value: FeedbackVerdict; label: string }[] = [
  { value: 'correct', label: 'Correct' },
  { value: 'partly_correct', label: 'Partly correct' },
  { value: 'incorrect', label: 'Incorrect' },
]

// Who a decision is recorded under: the signed-in user, or, where sign-in is not
// in use, a name typed here.
function useDecisionName() {
  const user = useSession((s) => s.user)
  const typed = useUi((s) => s.forecaster)
  return { name: (user?.display_name ?? typed).trim(), signedIn: user !== null }
}

function NameField() {
  const typed = useUi((s) => s.forecaster)
  const setTyped = useUi((s) => s.setForecaster)
  return (
    <input
      className={field}
      placeholder="Your name"
      value={typed}
      onChange={(e) => setTyped(e.target.value)}
      aria-label="Forecaster name"
    />
  )
}

// Forecaster's verdict on an alert. Stored for later verification; it does not change the model.
function FeedbackForm({ alert }: { alert: Alert }) {
  const { name } = useDecisionName()
  const canAct = useCanAct()
  const recorded = useFeedback(alert.cycle_id).data?.feedback.filter((item) => item.alert_id === alert.alert_id) ?? []
  const add = useAddFeedback()
  const [verdict, setVerdict] = useState<FeedbackVerdict>('correct')
  const [comment, setComment] = useState('')

  return (
    <section className="space-y-2">
      <h3 className="text-muted-foreground text-xs font-medium">Feedback</h3>
      {canAct && (
        <>
          <div className="flex gap-2">
            <select className={field} value={verdict} onChange={(e) => setVerdict(e.target.value as FeedbackVerdict)} aria-label="Verdict">
              {VERDICTS.map(({ value, label }) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              variant="outline"
              disabled={!name || add.isPending}
              onClick={() =>
                add.mutate(
                  {
                    cycle_id: alert.cycle_id,
                    alert_id: alert.alert_id,
                    cell_id: alert.cell_id,
                    forecaster: name,
                    verdict,
                    comment: comment.trim() || null,
                  },
                  { onSuccess: () => setComment('') },
                )
              }
            >
              Submit
            </Button>
          </div>
          <input
            className={field}
            placeholder="Comment (optional)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            aria-label="Feedback comment"
          />
        </>
      )}
      {add.error && <p className="text-danger text-xs">{add.error.message}</p>}
      {recorded.length === 0 && !canAct && <p className="text-muted-foreground text-xs">No feedback recorded.</p>}
      {recorded.map((item) => (
        <p key={item.feedback_id} className="text-muted-foreground text-xs leading-snug">
          {item.forecaster}: {VERDICTS.find((v) => v.value === item.verdict)?.label}
          {item.comment && ` — ${item.comment}`}
        </p>
      ))}
    </section>
  )
}

// Review of one alert: editable with approve / reject while it awaits a decision,
// read-only afterwards. Includes the CAP preview and feedback.
export function AlertReview({ alert }: { alert: Alert }) {
  const { name, signedIn } = useDecisionName()
  const canAct = useCanAct()
  const { approve, reject } = useAlertActions()
  const [headline, setHeadline] = useState(alert.headline)
  const [description, setDescription] = useState(alert.description)
  const [risk, setRisk] = useState<RiskLevel>(alert.risk_level)
  const pending = alert.status === 'suggested'
  const busy = approve.isPending || reject.isPending
  const error = approve.error ?? reject.error

  return (
    <div className="space-y-4">
      {pending && canAct ? (
        <section className="space-y-2">
          <input className={field} value={headline} onChange={(e) => setHeadline(e.target.value)} aria-label="Headline" />
          <textarea
            className={cn(field, 'h-32 resize-none leading-snug')}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            aria-label="Description"
          />
          <div className="flex gap-2">
            <select className={field} value={risk} onChange={(e) => setRisk(e.target.value as RiskLevel)} aria-label="Risk level">
              {(Object.keys(RISK) as RiskLevel[]).map((level) => (
                <option key={level} value={level}>
                  {RISK[level].label} risk
                </option>
              ))}
            </select>
            {!signedIn && <NameField />}
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              className="flex-1"
              disabled={!name || busy}
              onClick={() =>
                approve.mutate({
                  alertId: alert.alert_id,
                  decision: { forecaster: name, headline, description, risk_level: risk },
                })
              }
            >
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="flex-1"
              disabled={!name || busy}
              onClick={() => reject.mutate({ alertId: alert.alert_id, decision: { forecaster: name } })}
            >
              Reject
            </Button>
          </div>
          <p className="text-muted-foreground text-xs">
            {name ? `The decision will be recorded under ${name}.` : 'Enter your name to record a decision.'}
          </p>
          {error && <p className="text-danger text-xs">{error.message}</p>}
        </section>
      ) : (
        <section className="space-y-2">
          <p className="text-[0.82rem] leading-relaxed">{alert.description}</p>
          <p className="text-muted-foreground text-xs">
            {pending
              ? 'Awaiting review. Your account can view alerts but not decide them.'
              : `${ALERT_STATUS[alert.status]} by ${alert.reviewed_by}${alert.reviewed_at ? ` at ${utcTime(alert.reviewed_at)} UTC` : ''}`}
          </p>
        </section>
      )}

      <FeedbackForm alert={alert} />

      <details>
        <summary className="text-muted-foreground cursor-pointer text-xs">CAP preview (not disseminated)</summary>
        <pre className="bg-muted mt-2 max-h-64 overflow-auto rounded-md p-2.5 font-mono text-[0.65rem] leading-snug select-text">
          {alert.cap_xml}
        </pre>
      </details>
    </div>
  )
}
