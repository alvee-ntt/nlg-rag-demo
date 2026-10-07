import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import batteryIcon from './assets/ios-battery.svg'
import signalIcon from './assets/ios-signal.svg'
import wifiIcon from './assets/ios-wifi.svg'
import orbSmall from './assets/navigator-orb-sm.png'
import stepper from './assets/settings-stepper.svg'
import chevron from './assets/chevron.svg'
import { saveDefaultProfile } from './profile'
import { isSignedIn, signedInUser } from './session'
import './Onboarding.css'

const CAN = [
  'Answer from approved NLG material, with sources',
  'Give you wording you can say to a client',
  'Put a person one tap away',
]

function Chips({ label, options, selected, onSelect }: {
  label: string
  options: string[]
  selected: string
  onSelect: (option: string) => void
}) {
  return (
    <div className="pref">
      <p className="pref-label">{label}</p>
      <div className="chips" role="group" aria-label={label}>
        {options.map((option) => (
          <button
            className={option === selected ? 'chip is-selected' : 'chip'}
            type="button"
            aria-pressed={option === selected}
            key={option}
            onClick={() => onSelect(option)}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  )
}

export default function Settings() {
  const signedIn = isSignedIn()
  const [choices, setChoices] = useState({
    length: 'Brief',
    format: 'Prose',
    tone: 'Warm',
    ask: 'Type',
    reach: 'Type',
    wording: 'Plain Language',
  })

  const threadRef = useRef<HTMLDivElement>(null)
  const [saving, setSaving] = useState(false)
  const [saveNote, setSaveNote] = useState('')
  const [saveFailed, setSaveFailed] = useState(false)

  function choose(key: keyof typeof choices, option: string) {
    setChoices((current) => ({ ...current, [key]: option }))
  }

  useLayoutEffect(() => {
    const thread = threadRef.current
    const form = thread?.querySelector('.settings-form')
    if (!thread || !(form instanceof HTMLElement)) return
    thread.scrollTop = form.offsetTop
  }, [])

  async function saveProfile() {
    if (saving) return
    setSaving(true)
    setSaveNote('')
    setSaveFailed(false)
    try {
      const response = await fetch('/api/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: signedInUser(),
          language: 'English',
          length: choices.length,
          format: choices.format,
          tone: choices.tone,
          how_you_ask: choices.ask,
          how_answers_reach_you: choices.reach,
          wording: choices.wording,
        }),
      })
      if (!response.ok) {
        setSaveFailed(true)
        setSaveNote('Could not save your profile.')
        return
      }
      window.location.assign('/onboarding/6')
    } catch {
      setSaveFailed(true)
      setSaveNote('Could not save your profile.')
    } finally {
      setSaving(false)
    }
  }

  async function skipProfile() {
    if (saving) return
    setSaving(true)
    setSaveNote('')
    setSaveFailed(false)
    try {
      await saveDefaultProfile()
      window.location.assign('/chat')
    } catch {
      setSaveFailed(true)
      setSaveNote('Could not save your profile.')
      setSaving(false)
    }
  }

  useEffect(() => {
    if (!signedIn) {
      window.location.replace('/learn')
      return
    }
    const userId = signedInUser()
    let cancelled = false
    fetch(`/api/profiles/${encodeURIComponent(userId)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((profile) => {
        if (!profile || cancelled) return
        setChoices({
          length: profile.length,
          format: profile.format,
          tone: profile.tone,
          ask: profile.how_you_ask,
          reach: profile.how_answers_reach_you,
          wording: profile.wording,
        })
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [signedIn])

  if (!signedIn) return null

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
        <main className="onboarding-sheet settings-sheet">
          <div className="onboarding-nav is-pills">
            <button className="ob-pill" type="button" onClick={() => window.location.assign('/onboarding/3')}>Go Back</button>
            <button className="ob-pill" type="button" disabled={saving} onClick={() => void skipProfile()}>Skip for now</button>
          </div>
          <div className="ob-thread settings-thread" ref={threadRef}>
            <div className="ob-message">
              <img className="ob-orb-sm" src={orbSmall} alt="" />
              <div className="ob-bubble">
                <h2>Navigator can</h2>
                <ul>
                  {CAN.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
            </div>
            <div className="ob-message">
              <span className="ob-orb-slot" />
              <div className="ob-bubble">
                <h2>Navigator does not</h2>
                <p>Say that a case will be approved, or replace underwriting. Learn is the same: it does not approve cases.</p>
              </div>
            </div>
            <div className="settings-form">
              <div className="settings-lead">
                <h1>How should Navigator answer?</h1>
                <p>Pick what feels right.<br />You can change it anytime in Settings.</p>
              </div>
              <div className="pref is-language">
                <p className="pref-label">Language</p>
                <div className="lang-row">
                  <span className="lang-name"><span className="flag" />English</span>
                  <img src={chevron} alt="" />
                </div>
              </div>
              <Chips label="Length:" options={['Brief', 'Balanced', 'Detailed']} selected={choices.length} onSelect={(option) => choose('length', option)} />
              <Chips label="Format:" options={['Auto', 'Bullets', 'Prose']} selected={choices.format} onSelect={(option) => choose('format', option)} />
              <Chips label="Tone:" options={['Warm', 'Neutral', 'Formal']} selected={choices.tone} onSelect={(option) => choose('tone', option)} />
              <Chips label="How you ask" options={['Type', 'Voice']} selected={choices.ask} onSelect={(option) => choose('ask', option)} />
              <Chips label="How answers reach you" options={['Type', 'Voice']} selected={choices.reach} onSelect={(option) => choose('reach', option)} />
              <Chips label="Wording" options={['Plain Language', 'Industry terms']} selected={choices.wording} onSelect={(option) => choose('wording', option)} />
              <div className="preview">
                <h2>Preview</h2>
                <p>Cap Focus leaves more room in a strong year. Participation Focus credits more of an average year but caps sooner.</p>
              </div>
            </div>
          </div>
          <div className="onboarding-foot">
            <div className="onboarding-stepper">
              <img src={stepper} alt="" />
            </div>
            <button className="onboarding-next" type="button" disabled={saving} onClick={() => void saveProfile()}>Next</button>
            <p className={saveFailed ? 'settings-save-note is-error' : 'settings-save-note'} role="status">{saveNote}</p>
            <button className="settings-skip" type="button" disabled={saving} onClick={() => void skipProfile()}>Skip for now</button>
          </div>
        </main>
      </div>
    </div>
  )
}
