import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import batteryIcon from './assets/ios-battery.svg'
import signalIcon from './assets/ios-signal.svg'
import wifiIcon from './assets/ios-wifi.svg'
import orb from './assets/onboarding-orb.png'
import orbSmall from './assets/navigator-orb-sm.png'
import stepper1 from './assets/onboarding-stepper.svg'
import stepper2 from './assets/onboarding-stepper-2.svg'
import stepper3 from './assets/onboarding-stepper-3.svg'
import { saveDefaultProfile } from './profile'
import { isSignedIn } from './session'
import './Onboarding.css'

const PATHS = ['/onboarding', '/onboarding/2', '/onboarding/3']
const STEPPERS = [stepper1, stepper2, stepper3]
const REVEAL_MS = 1600

const CAN = [
  'Answer from approved NLG material, with sources',
  'Give you wording you can say to a client',
  'Put a person one tap away',
]

function go(path: string) {
  window.location.assign(path)
}

function onboardingStep(pathname = window.location.pathname) {
  const path = pathname.toLowerCase().replace(/\/$/, '')
  const index = PATHS.indexOf(path)
  return index >= 0 ? index + 1 : 0
}

function StatusBar() {
  return (
    <div className="onboarding-status">
      <span>9:41</span>
      <span>
        <img src={signalIcon} alt="" />
        <img src={wifiIcon} alt="" />
        <img src={batteryIcon} alt="" />
      </span>
    </div>
  )
}

function CanBubble() {
  return (
    <div className="ob-message">
      <img className="ob-orb-sm" src={orbSmall} alt="" />
      <div className="ob-bubble">
        <h2>Navigator can</h2>
        <ul>
          {CAN.map((item) => <li key={item}>{item}</li>)}
        </ul>
      </div>
    </div>
  )
}

function CannotBubble() {
  return (
    <div className="ob-message">
      <span className="ob-orb-slot" />
      <div className="ob-bubble">
        <h2>Navigator does not</h2>
        <p>Say that a case will be approved, or replace underwriting. Learn is the same: it does not approve cases.</p>
      </div>
    </div>
  )
}

function Title({ children }: { children: ReactNode }) {
  return <h2 className="ob-title">{children}</h2>
}

function ProfileReveal() {
  const [reveal, setReveal] = useState(0)

  useEffect(() => {
    const profile = window.setTimeout(() => setReveal(1), REVEAL_MS)
    const answer = window.setTimeout(() => setReveal(2), REVEAL_MS * 2)
    return () => {
      window.clearTimeout(profile)
      window.clearTimeout(answer)
    }
  }, [])

  return (
    <>
      {reveal >= 1 && <Title>Now, let’s configure your profile...</Title>}
      {reveal >= 2 && <Title>How should Navigator answer?</Title>}
    </>
  )
}

export default function Onboarding() {
  const signedIn = isSignedIn()
  const step = onboardingStep()
  const [skipping, setSkipping] = useState(false)
  const [skipError, setSkipError] = useState('')

  useEffect(() => {
    if (!signedIn) window.location.replace('/learn')
  }, [signedIn])

  async function skip() {
    if (skipping) return
    setSkipping(true)
    setSkipError('')
    try {
      await saveDefaultProfile()
      window.location.assign('/chat')
    } catch {
      setSkipping(false)
      setSkipError('Could not save your profile.')
    }
  }

  if (!signedIn || step === 0) return null

  const next = step === 3 ? '/settings' : PATHS[step]

  return (
    <div className="onboarding-page">
      <div className="onboarding-phone">
        <StatusBar />
        <main className="onboarding-sheet">
          {step === 1 ? (
            <div className="onboarding-nav">
              <span className="onboarding-back" aria-hidden="true">Go Back</span>
              <button className="onboarding-skip" type="button" disabled={skipping} onClick={() => void skip()}>Skip for now</button>
            </div>
          ) : (
            <div className="onboarding-nav is-pills">
              <button className="ob-pill" type="button" onClick={() => go(PATHS[step - 2])}>Go Back</button>
              <button className="ob-pill" type="button" disabled={skipping} onClick={() => void skip()}>Skip for now</button>
            </div>
          )}
          {step === 1 ? (
            <div className="onboarding-messages">
              <div className="onboarding-intro">
                <img className="onboarding-orb" src={orb} alt="" />
                <div className="onboarding-copy">
                  <h1>Hi, this is Navigator.<br />I’ll walk you through the app...</h1>
                  <p>Lorem ipsum dolor sit amet</p>
                </div>
              </div>
            </div>
          ) : (
            <div className="ob-thread">
              <Title>Check facts fast,<br />in your own words</Title>
              <CanBubble />
              {step === 3 && <CannotBubble />}
              {step === 3 && <ProfileReveal />}
            </div>
          )}
          <div className="onboarding-foot">
            <div className="onboarding-stepper">
              <img src={STEPPERS[step - 1]} alt="" />
            </div>
            <button className="onboarding-next" type="button" onClick={() => go(next)}>Next</button>
            {skipError ? <p className="settings-save-note is-error" role="alert">{skipError}</p> : null}
          </div>
        </main>
      </div>
    </div>
  )
}
