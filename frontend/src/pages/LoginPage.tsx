import { useState, type FormEvent } from 'react'
import { Eye, EyeOff, LoaderCircle, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { useSession } from '@/lib/session'
import { useUi } from '@/store/ui'

const field =
  'bg-background focus:border-primary focus:ring-ring h-10 w-full rounded-md border px-3 text-sm outline-none select-text focus:ring-2 disabled:opacity-60'

export function LoginPage({ version }: { version: string }) {
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
      const { token, user } = await api.login(username.trim(), password)
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
    <div className="bg-sidebar flex h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="bg-primary/15 ring-primary/40 mb-3 flex size-11 items-center justify-center rounded-lg ring-1">
            <Zap className="text-primary size-6 fill-current" />
          </div>
          <div className="text-lg font-semibold tracking-[0.22em]">VAJRA</div>
          <div className="text-muted-foreground mt-1 text-xs">Thunderstorm &amp; Lightning Nowcasting</div>
        </div>

        <form onSubmit={submit} noValidate className="bg-card rounded-xl border p-6 shadow-sm">
          <h1 className="text-sm font-semibold">Sign in</h1>

          <label className="mt-4 block text-xs font-medium" htmlFor="login-user">
            User ID
          </label>
          <input
            id="login-user"
            className={`${field} mt-1.5`}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
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
            {busy ? 'Signing in' : 'Sign in'}
          </Button>
        </form>

        <p className="text-muted-foreground mt-4 text-center text-[0.7rem] leading-relaxed">
          Accounts are issued by your administrator.
          <br />
          <span className="font-mono">Engine {version}</span>
        </p>
      </div>
    </div>
  )
}
