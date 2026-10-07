import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Login from './Login.tsx'
import Onboarding from './Onboarding.tsx'
import PromptWorkshop from './PromptWorkshop.tsx'
import ChatLanding from './ChatLanding.tsx'
import Demo from './Demo.tsx'
import Onboarding6 from './Onboarding6.tsx'
import Settings from './Settings.tsx'

const route = window.location.pathname.toLowerCase().replace(/\/$/, '')
const onboarding = route === '/onboarding' || /^\/onboarding\/[23]$/.test(route)
const knownRoute =
  route === '/learn'
  || route === '/demo'
  || route === '/chat'
  || route === '/prompts'
  || route === '/onboarding/6'
  || route === '/settings'
  || onboarding

if (!knownRoute) {
  window.history.replaceState(null, '', '/learn')
}

const page =
  route === '/demo' ? <Demo />
    : route === '/chat' ? <ChatLanding />
      : route === '/prompts' ? <PromptWorkshop />
        : route === '/learn' ? <Login />
          : route === '/onboarding/6' ? <Onboarding6 />
            : onboarding ? <Onboarding />
              : route === '/settings' ? <Settings />
                : <Login />

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {page}
  </StrictMode>,
)
