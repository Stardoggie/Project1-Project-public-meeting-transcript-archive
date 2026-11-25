// API helper functions for the frontend

export async function getJson(path) {
  const response = await fetch(`/api/v1/${path}`)
  if (!response.ok) {
    // The API sends errors as {"error": ..., "detail": ...}
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json()
}