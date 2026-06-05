/**
 * Agent backend client.
 * All requests go to /agent/* (proxied to :8001 by Vite).
 */

const BASE = '/agent'

// ── Session ─────────────────────────────────────────────────────────

const SESSION_KEY = 'meituan_agent_session_id_v3'

export async function getOrCreateSession() {
  let sid = localStorage.getItem(SESSION_KEY)
  if (sid) {
    try {
      const r = await fetch(`${BASE}/${sid}/memory`)
      if (r.ok) return sid
    } catch (_) {}
  }
  const r = await fetch(`${BASE}/session`, { method: 'POST' })
  const data = await r.json()
  sid = data.session_id
  localStorage.setItem(SESSION_KEY, sid)
  return sid
}

export function clearSession() {
  localStorage.removeItem(SESSION_KEY)
}

// ── SSE streaming helper ─────────────────────────────────────────────

export async function streamPost(url, payload, onEvent, timeoutMs = 120000) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload != null ? JSON.stringify(payload) : undefined,
      signal: controller.signal,
    })

    if (!resp.ok) {
      onEvent({ type: 'error', message: `HTTP ${resp.status}` })
      return
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const parts = buf.split('\n\n')
      buf = parts.pop()
      for (const chunk of parts) {
        const line = chunk.trim()
        if (!line.startsWith('data: ')) continue
        try {
          const evt = JSON.parse(line.slice(6))
          onEvent(evt)
          if (evt.type === 'done' || evt.type === 'stream_end') return
        } catch (_) {}
      }
    }
  } catch (err) {
    const isAbort = err?.name === 'AbortError'
    onEvent({
      type: 'error',
      message: isAbort ? '请求超时，已停止等待。你可以重新规划。' : err.message,
    })
  } finally {
    clearTimeout(timeout)
  }
}

// ── Chat (phase-aware, primary entry point) ──────────────────────────

export function streamChat(sessionId, message, onEvent, opts = {}) {
  const payload = { message }
  if (opts.phase_hint)       payload.phase_hint       = opts.phase_hint
  if (opts.original_request) payload.original_request = opts.original_request
  if (opts.client_itinerary) payload.client_itinerary = opts.client_itinerary
  return streamPost(`${BASE}/${sessionId}/chat`, payload, onEvent)
}

// ── Planning (legacy) ────────────────────────────────────────────────

export function streamPlan(sessionId, payload, onEvent) {
  return streamPost(`${BASE}/${sessionId}/plan`, payload, onEvent)
}

// ── Fulfillment ───────────────────────────────────────────────────────

export function streamFulfill(sessionId, onEvent) {
  return streamPost(`${BASE}/${sessionId}/fulfill`, null, onEvent)
}

// ── Exception confirm ─────────────────────────────────────────────────

export function streamExceptionConfirm(sessionId, payload, onEvent) {
  return streamPost(`${BASE}/${sessionId}/exception/confirm`, payload, onEvent)
}

export async function resolveConfirmation(sessionId, requestId, approved = false, modifications = {}, reason = '') {
  const r = await fetch(`${BASE}/${sessionId}/confirmation/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: requestId, approved, modifications, reason }),
  })
  return r.json()
}

// ── Node actions ───────────────────────────────────────────────────────

export async function nodeAction(sessionId, nodeId, action, force = false, requestId = null) {
  const payload = { node_id: nodeId, action, force }
  if (requestId) payload.request_id = requestId
  const r = await fetch(`${BASE}/${sessionId}/node/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return r.json()
}

// ── User report ────────────────────────────────────────────────────────

export async function reportIssue(sessionId, type, note = '') {
  const r = await fetch(`${BASE}/${sessionId}/report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, note }),
  })
  return r.json()
}

// ── Memory ──────────────────────────────────────────────────────────────

export async function getMemory(sessionId) {
  const r = await fetch(`${BASE}/${sessionId}/memory`)
  return r.json()
}

// ── Monitor state (poll for real-time updates) ────────────────────────

export async function getMonitorState(sessionId) {
  try {
    const r = await fetch(`${BASE}/${sessionId}/monitor/state`)
    if (!r.ok) return null
    return r.json()
  } catch (_) {
    return null
  }
}

// ── Simulator advance ─────────────────────────────────────────────────

export async function advanceSimulator(sessionId) {
  try {
    const r = await fetch(`${BASE}/${sessionId}/simulator/advance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
    return r.json()
  } catch (e) {
    return { error: e.message }
  }
}

// ── Taxi dispatch ─────────────────────────────────────────────────────

export async function dispatchTaxi(sessionId) {
  try {
    const r = await fetch(`${BASE}/${sessionId}/taxi/dispatch`, { method: 'POST' })
    if (!r.ok) return { error: `HTTP ${r.status}` }
    return r.json()
  } catch (e) {
    return { error: e.message }
  }
}

// ── Route estimate (direct to Mock API via backend proxy) ─────────────

export async function getRouteEstimate(sessionId, fromPoiId, toPoiId, mode) {
  try {
    const r = await fetch(
      `${BASE}/${sessionId}/route/estimate?from=${encodeURIComponent(fromPoiId)}&to=${encodeURIComponent(toPoiId)}&mode=${mode}`
    )
    if (!r.ok) return null
    return r.json()
  } catch (_) { return null }
}

// ── Node checkin (user marks node as done) ────────────────────────────

export async function checkinNode(sessionId, nodeId) {
  try {
    const r = await fetch(`${BASE}/${sessionId}/node/checkin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node_id: nodeId }),
    })
    return r.json()
  } catch (e) {
    return { error: e.message }
  }
}

// ── Simulator inject (natural language → event) ───────────────────────

export async function injectSimulatorEvent(sessionId, text) {
  try {
    const r = await fetch(`${BASE}/${sessionId}/simulator/inject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    return r.json()
  } catch (e) {
    return { error: e.message }
  }
}

// ── Queue advice ──────────────────────────────────────────────────────

export async function getQueueAdvice(sessionId) {
  try {
    const r = await fetch(`${BASE}/${sessionId}/queue-advice`)
    if (!r.ok) return { advices: [] }
    return r.json()
  } catch (_) {
    return { advices: [] }
  }
}

// ── Location (Mock API direct) ────────────────────────────────────────

export async function getUserLocation() {
  try {
    const r = await fetch('/api/location/current')
    if (!r.ok) return null
    return r.json()
  } catch (_) { return null }
}

// ── Reset ────────────────────────────────────────────────────────────────

export async function resetSession(sessionId) {
  await fetch(`${BASE}/${sessionId}/reset`, { method: 'DELETE' })
  clearSession()
}
