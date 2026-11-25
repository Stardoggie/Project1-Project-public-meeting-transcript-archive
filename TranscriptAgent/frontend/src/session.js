// Who's signed in, shared by every page. Django keeps the real sign-in (a session cookie); this mirrors it.
import { reactive } from 'vue'

// reactive() makes an object whose changes re-render anything that reads it, like ref() for a whole object
export const session = reactive({
  loaded: false,
  authenticated: false,
  username: null,
  isStaff: false,
})

function remember(who) {
  session.loaded = true
  session.authenticated = who.authenticated
  session.username = who.username
  session.isStaff = who.is_staff
}

// Django's CSRF token, from the csrftoken cookie that GET /api/session sets
function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : ''
}

async function postJson(path, body = {}) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
    body: JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status})`)
  }
  return data
}

// Asks Django who's signed in. Also gets the CSRF cookie, so call it before signing in or out.
export async function loadSession() {
  try {
    const response = await fetch('/api/session')
    if (response.ok) {
      remember(await response.json())
      return
    }
  } catch {
    // Django isn't running: show the page as if nobody is signed in
  }
  session.loaded = true
}

export async function signIn(username, password) {
  if (!csrfToken()) {
    await loadSession()
  }
  remember(await postJson('/api/session/login', { username, password }))
}

export async function signOut() {
  remember(await postJson('/api/session/logout'))
}
