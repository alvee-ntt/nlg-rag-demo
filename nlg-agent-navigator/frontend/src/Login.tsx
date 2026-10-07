import { useState } from 'react'
import type { FormEvent } from 'react'
import hq from '../Assets/NationalLifeGroupHQ.jpeg'
import batteryIcon from './assets/ios-battery.svg'
import signalIcon from './assets/ios-signal.svg'
import wifiIcon from './assets/ios-wifi.svg'
import { signIn, signedInUser } from './session'
import './Login.css'

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    if (!username.trim() || !password.trim()) {
      setNote('Enter your username and password.')
      return
    }
    setNote('')
    signIn()
    setBusy(true)
    try {
      const response = await fetch(`/api/profiles/${encodeURIComponent(signedInUser())}`)
      window.location.assign(response.ok ? '/chat' : '/onboarding')
    } catch {
      setBusy(false)
      setNote('Could not check your profile.')
    }
  }

  return (
    <div className="login-page">
      <div className="login-phone">
        <div className="login-hero">
          <img src={hq} alt="" />
        </div>
        <div className="login-status">
          <span>9:41</span>
          <span>
            <img src={signalIcon} alt="" />
            <img src={wifiIcon} alt="" />
            <img src={batteryIcon} alt="" />
          </span>
        </div>
        <main className="login-main">
          <form className="login-card" onSubmit={handleSubmit} noValidate>
            <h1>Agent Navigator Login</h1>
            <div className="login-stack">
              <div className="login-field">
                <label htmlFor="username">Username or Agent ID</label>
                <input
                  id="username"
                  name="username"
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  placeholder="e.g. agent.smith"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                />
              </div>
              <div className="login-field">
                <label htmlFor="password">Password</label>
                <input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  placeholder="••••••••••••"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </div>
              <button className="login-submit" type="submit" disabled={busy}>Login</button>
              <p className="login-links">
                <button type="button" onClick={() => setNote('This demo uses one shared login. Ask whoever set it up for the credentials.')}>Forgot username?</button>
                <span>  |  </span>
                <button type="button" onClick={() => setNote('This demo uses one shared login. Ask whoever set it up for the credentials.')}>Forgot password?</button>
              </p>
              <p className="login-note" role="alert">{note}</p>
            </div>
          </form>
          <p className="login-legal">For agent use only - not for use with the public</p>
        </main>
      </div>
    </div>
  )
}
