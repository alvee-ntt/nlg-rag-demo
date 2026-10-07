import { signedInUser } from './session'

export const DEFAULT_PROFILE = {
  language: 'English',
  length: 'Balanced',
  format: 'Auto',
  tone: 'Formal',
  how_you_ask: 'Type',
  how_answers_reach_you: 'Type',
  wording: 'Plain Language',
}

export async function saveDefaultProfile() {
  const response = await fetch('/api/profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: signedInUser(),
      ...DEFAULT_PROFILE,
    }),
  })
  if (!response.ok) throw new Error('Could not save your profile.')
}
