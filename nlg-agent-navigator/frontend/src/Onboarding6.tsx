import { useEffect } from 'react'
import batteryIcon from './assets/ios-battery.svg'
import signalIcon from './assets/ios-signal.svg'
import wifiIcon from './assets/ios-wifi.svg'
import orb from './assets/onboarding-orb.png'
import orbSmall from './assets/navigator-orb-sm.png'
import book from './assets/icon-learn-book.svg'
import bookSelected from './assets/icon-learn-book-selected.svg'
import stepper from './assets/onboarding-stepper-6.svg'
import { isSignedIn } from './session'
import './Onboarding.css'
import './Onboarding6.css'

export default function Onboarding6() {
  const signedIn = isSignedIn()

  useEffect(() => {
    if (!signedIn) window.location.replace('/learn')
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
        <main className="onboarding-sheet ob6-sheet">
          <div className="ob6-body">
            <img className="onboarding-orb" src={orb} alt="" />
            <div className="ob6-copy">
              <h1>We can chat anytime of you can navigate through the format toggle to change experience.</h1>
              <div className="mode-switch" aria-hidden="true">
                <div className="mode-pill is-on">
                  <img className="mode-orb" src={orbSmall} alt="" />
                  <span>Chat</span>
                </div>
                <div className="mode-book">
                  <img src={book} alt="" />
                </div>
              </div>
              <p className="ob6-note">Lorem Ipsum dolor sit amet</p>
              <div className="mode-switch is-learn" aria-hidden="true">
                <div className="mode-book">
                  <img className="mode-orb" src={orbSmall} alt="" />
                </div>
                <div className="mode-pill is-on">
                  <img src={bookSelected} alt="" />
                  <span>Learn</span>
                </div>
              </div>
              <p className="ob6-note">Lorem Ipsum dolor sit amet</p>
            </div>
          </div>
          <div className="onboarding-foot">
            <div className="onboarding-stepper">
              <img src={stepper} alt="" />
            </div>
            <button className="onboarding-next" type="button" onClick={() => window.location.assign('/chat')}>Next</button>
          </div>
        </main>
      </div>
    </div>
  )
}
