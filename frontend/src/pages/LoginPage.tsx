import { useState, type FormEvent } from 'react'
import { Eye, EyeOff, LoaderCircle, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { useSession } from '@/lib/session'
import { useUi } from '@/store/ui'

const field =
  'bg-background focus:border-primary focus:ring-ring h-10 w-full rounded-md border px-3 text-sm outline-none select-text focus:ring-2 disabled:opacity-60'

// The engine takes a few seconds to start with the app. A sign-in sent before it is
// up waits for it this long before reporting that it cannot be reached.
const ENGINE_WAIT_MS = 45000

async function signIn(username: string, password: string) {
  const deadline = Date.now() + ENGINE_WAIT_MS
  for (;;) {
    try {
      return await api.login(username, password)
    } catch (err) {
      // A TypeError means no answer at all; anything else is the engine's own refusal.
      if (!(err instanceof TypeError) || Date.now() > deadline) throw err
      await new Promise((resolve) => setTimeout(resolve, 700))
    }
  }
}

export function LoginPage({ version }: { version?: string }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    if (!username.trim() || !password) {
      setError('Enter your user ID and password.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const { token, user } = await signIn(username.trim(), password)
      // Decisions are recorded under the signed-in name.
      useUi.getState().setForecaster(user.display_name)
      useSession.getState().start(token, user)
    } catch (err) {
      // A refusal carries the backend's own message; anything else means it was unreachable.
      setError(err instanceof TypeError ? 'The forecast engine cannot be reached. Try again.' : (err as Error).message)
      setPassword('')
      setBusy(false)
    }
  }

  return (
    <div className="bg-background flex h-screen items-center justify-center p-6">
      <div className="bg-card grid w-full max-w-3xl overflow-hidden rounded-xl border shadow-lg md:grid-cols-[5fr_6fr]">
        {/* Brand panel: always dark, in both themes. */}
        <div className="relative hidden min-h-[26rem] flex-col justify-end overflow-hidden bg-[#0b132b] p-8 text-white md:flex">
          <svg className="absolute inset-0 size-full" viewBox="0 0 400 520" preserveAspectRatio="xMidYMid slice" aria-hidden>
            <defs>
              <radialGradient id="login-glow" cx="30%" cy="105%" r="85%">
                <stop offset="0%" stopColor="#2563eb" stopOpacity="0.55" />
                <stop offset="55%" stopColor="#1e3a8a" stopOpacity="0.25" />
                <stop offset="100%" stopColor="#0b132b" stopOpacity="0" />
              </radialGradient>
            </defs>
            <rect width="400" height="520" fill="url(#login-glow)" />
            {[150, 230, 310, 390, 470].map((r) => (
              <circle key={r} cx="120" cy="560" r={r} fill="none" stroke="#60a5fa" strokeOpacity="0.14" />
            ))}
            {[60, 140, 220, 300].map((x) => (
              <line key={x} x1={x} y1="0" x2={x + 80} y2="520" stroke="#60a5fa" strokeOpacity="0.06" />
            ))}
          </svg>
          <div className="relative flex items-center gap-3">
            <Zap className="size-11 fill-current text-[#60a5fa]" />
            <div>
              <div className="text-3xl leading-none font-bold tracking-[0.06em]">VAJRA</div>
              <div className="mt-2 text-sm leading-snug text-white/75">
                Nowcasting for
                <br />
                Thunderstorm &amp; Lightning
              </div>
            </div>
          </div>
        </div>

        <div className="flex flex-col justify-center p-8">
        <form onSubmit={submit} noValidate>
          <h1 className="text-xl font-semibold">Sign In</h1>
          <p className="text-muted-foreground mt-0.5 text-xs">Access VAJRA</p>

          <label className="mt-6 block text-xs font-medium" htmlFor="login-user">
            User ID
          </label>
          <input
            id="login-user"
            className={`${field} mt-1.5`}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Enter user ID"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            disabled={busy}
          />

          <label className="mt-3.5 block text-xs font-medium" htmlFor="login-password">
            Password
          </label>
          <div className="relative mt-1.5">
            <input
              id="login-password"
              className={`${field} pr-10`}
              type={showPassword ? 'text' : 'password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password"
              autoComplete="current-password"
              disabled={busy}
            />
            <button
              type="button"
              onClick={() => setShowPassword(!showPassword)}
              title={showPassword ? 'Hide password' : 'Show password'}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              aria-pressed={showPassword}
              className="text-muted-foreground hover:text-foreground focus-visible:ring-ring absolute top-1 right-1 flex size-8 items-center justify-center rounded outline-none focus-visible:ring-2"
            >
              {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </button>
          </div>

          <div role="alert" className="text-destructive mt-3 min-h-4 text-xs leading-snug">
            {error}
          </div>

          <Button type="submit" className="mt-2 h-10 w-full" disabled={busy}>
            {busy && <LoaderCircle className="animate-spin" />}
            {busy ? 'Signing in' : 'Sign In'}
          </Button>
        </form>

          <p className="text-muted-foreground mt-5 text-[0.7rem] leading-relaxed">
            Accounts are issued by your administrator.
            {version && <span className="ml-2 font-mono">Engine {version}</span>}
          </p>
        </div>
      </div>
    </div>
  )
}
