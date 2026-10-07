const SESSION_KEY = 'navigatorUser'

export const SIGNED_IN_USER = 'nlg-user'

export function signIn() {
  sessionStorage.setItem(SESSION_KEY, SIGNED_IN_USER)
}

export function isSignedIn() {
  return sessionStorage.getItem(SESSION_KEY) === SIGNED_IN_USER
}

export function signedInUser() {
  return sessionStorage.getItem(SESSION_KEY) ?? ''
}
