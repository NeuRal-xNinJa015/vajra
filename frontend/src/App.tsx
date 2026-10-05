import { useEffect } from 'react'
import { HashRouter } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { LoaderCircle, Zap } from 'lucide-react'
import { AppShell } from '@/app/AppShell'
import { useHealth } from '@/lib/queries'
import { useSession } from '@/lib/session'
import { LoginPage } from '@/pages/LoginPage'

// Shown until the forecast engine has answered once; the health check keeps retrying.
function EngineWait({ failed }: { failed: boolean }) {
  return (
    <div className="bg-sidebar flex h-screen flex-col items-center justify-center gap-3 text-center">
      <div className="bg-primary/15 ring-primary/40 flex size-11 items-center justify-center rounded-lg ring-1">
        <Zap className="text-primary size-6 fill-current" />
      </div>
      <div className="text-lg font-semibold tracking-[0.22em]">VAJRA</div>
      <div className="text-muted-foreground flex items-center gap-2 text-xs" role="status">
        <LoaderCircle className="size-3.5 animate-spin" />
        {failed ? 'The forecast engine is not responding. Retrying.' : 'Starting the forecast engine'}
      </div>
    </div>
  )
}

function App() {
  const health = useHealth()
  const signedIn = useSession((s) => s.token !== null)
  const client = useQueryClient()
  const authRequired = health.data?.auth_required

  // When a session ends, nothing fetched under it is kept for the next user.
  useEffect(() => {
    if (authRequired && !signedIn) {
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== 'health' })
      window.location.hash = ''
    }
  }, [authRequired, signedIn, client])

  if (!health.data) return <EngineWait failed={health.isError} />
  if (authRequired && !signedIn) return <LoginPage version={health.data.version} />

  return (
    <HashRouter>
      <AppShell />
    </HashRouter>
  )
}

export default App
