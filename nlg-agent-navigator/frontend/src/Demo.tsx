import { useEffect, useState } from 'react'
import batteryIcon from './assets/ios-battery.svg'
import signalIcon from './assets/ios-signal.svg'
import wifiIcon from './assets/ios-wifi.svg'
import { isSignedIn, signedInUser } from './session'
import './Onboarding.css'
import './Demo.css'

export default function Demo() {
  const [note, setNote] = useState('')
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!isSignedIn()) window.location.replace('/learn')
  }, [])

  async function resetProfile() {
    if (busy) return
    setBusy(true)
    setNote('')
    setFailed(false)
    try {
      const response = await fetch(`/api/profiles/${encodeURIComponent(signedInUser())}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        setFailed(true)
        setNote('Could not reset the profile.')
        return
      }
      const body = await response.json() as { deleted?: boolean }
      setNote(body.deleted ? 'Profile deleted.' : 'No profile was saved for this user.')
    } catch {
      setFailed(true)
      setNote('Could not reset the profile.')
    } finally {
      setBusy(false)
    }
  }

  if (!isSignedIn()) return null

  return (
    <div className="onboarding-page">
      <div className="onboarding-phone">
        <div className="onboarding-status">
          <span>9:41</span>
          <span>
            <img src={signalIcon} alt="" />
            <img src={wifiIcon} alt="" />
            <img src={batteryIcon} alt="" />
          </span>
        </div>
        <main className="onboarding-sheet demo-sheet">
          <section className="demo-card">
            <h1>Demo</h1>
            <p>Remove the saved profile for this sign-in.</p>
            <button className="onboarding-next" type="button" disabled={busy} onClick={() => void resetProfile()}>
              Reset profiles
            </button>
            <p className={failed ? 'settings-save-note is-error' : 'settings-save-note'} role="status">{note}</p>
          </section>
        </main>
      </div>
    </div>
  )
}
