import { useEffect } from 'react'
import { HashRouter } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { AppShell } from '@/app/AppShell'
import { useHealth } from '@/lib/queries'
import { useSession } from '@/lib/session'
import { LoginPage } from '@/pages/LoginPage'

const AUTH_KEY = 'vajra-auth-required'

// Whether the engine required sign-in the last time it answered. Used while it is
// still starting, so the sign-in screen can open at once. Sign-in is assumed until
// the engine says otherwise.
function rememberedAuthRequired(): boolean {
  try {
    return localStorage.getItem(AUTH_KEY) !== '0'
  } catch {
    return true
  }
}

function App() {
  const health = useHealth()
  const signedIn = useSession((s) => s.token !== null)
  const client = useQueryClient()
  const authRequired = health.data?.auth_required ?? rememberedAuthRequired()
  const answered = health.data?.auth_required

  useEffect(() => {
    if (answered === undefined) return
    try {
      localStorage.setItem(AUTH_KEY, answered ? '1' : '0')
    } catch {
      // Storage unavailable: sign-in is assumed at the next start.
    }
  }, [answered])

  // When a session ends, nothing fetched under it is kept for the next user.
  useEffect(() => {
    if (authRequired && !signedIn) {
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== 'health' })
      window.location.hash = ''
    }
  }, [authRequired, signedIn, client])

  if (authRequired && !signedIn) return <LoginPage version={health.data?.version} />

  return (
    <HashRouter>
      <AppShell />
    </HashRouter>
  )
}

export default App
