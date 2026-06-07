import React, { useCallback, useEffect, useState } from 'react'
import ChatPage from './ChatPage'
import MonitorPanel from './components/MonitorPanel'
import UserProfilePanel from './components/UserProfilePanel'
import ItinerarySheet from './components/ItinerarySheet'
import ShareModal from './components/ShareModal'
import { getOrCreateSession, checkinNode, dispatchTaxi, getUserLocation, updateNodeTime, replaceNode } from './api/agentClient'
import './styles.css'

export default function App() {
  const [sessionId, setSessionId]           = useState(null)
  const [monitorState, setMonitorState]     = useState(null)
  const [showMonitor, setShowMonitor]       = useState(true)
  const [itinerary, setItinerary]           = useState([])
  const [showSheet, setShowSheet]           = useState(false)
  const [showShare, setShowShare]           = useState(false)
  const [taxiStatus, setTaxiStatus]         = useState(null)
  const [userProfile, setUserProfile]       = useState({ facts: null, preferences: null, phase: 'gathering' })
  const [detectedLocation, setDetectedLocation] = useState(null)
  const [transitPending, setTransitPending] = useState({})
  const [taxiPrompt, setTaxiPrompt] = useState(null)
  const [replacementPending, setReplacementPending] = useState(null)
  const [replacementResult, setReplacementResult] = useState(null)
  const [replacementBusy, setReplacementBusy] = useState(false)

  useEffect(() => {
    getOrCreateSession().then(setSessionId)
    getUserLocation().then(loc => {
      if (loc?.address) setDetectedLocation(loc)
    })
  }, [])

  const updateItinerary = useCallback((newNodes) => {
    setItinerary(prev => {
      const checkedIds = new Set(prev.filter(n => n._checked).map(n => n.id))
      return (newNodes || []).map(n => ({
        ...n,
        _checked: checkedIds.has(n.id) ? true : Boolean(n._checked),
      }))
    })
  }, [])

  const handleCheckin = useCallback(async (nodeId) => {
    if (!sessionId) return
    const result = await checkinNode(sessionId, nodeId)
    if (result.nodes) updateItinerary(result.nodes)
    if (result.next_requires_taxi && result.next_node) {
      setTaxiPrompt(result.next_node)
    }
  }, [sessionId, updateItinerary])

  const handleShare = useCallback(() => {
    setShowSheet(false)
    setShowShare(true)
  }, [])

  const handleCallTaxi = useCallback(async (node) => {
    if (!sessionId) return
    const nodeId = node.id
    setTaxiStatus({ nodeId, status: 'requesting' })
    const result = await dispatchTaxi(sessionId)
    if (result.error) {
      setTaxiStatus({ nodeId, status: 'error' })
    } else {
      setTaxiStatus({
        nodeId,
        status: 'success',
        plateNo: result.plate_no,
        driver: result.driver,
        car: result.car_model,
        eta: result.eta_min,
      })
    }
  }, [sessionId])

  const handleTaxiPromptConfirm = useCallback(async () => {
    if (!taxiPrompt) return
    const node = taxiPrompt
    setTaxiPrompt(null)
    await handleCallTaxi(node)
  }, [taxiPrompt, handleCallTaxi])

  const handleTaxiPromptDiscard = useCallback(() => {
    setTaxiPrompt(null)
  }, [])

  const mergeProfilePart = useCallback((prevPart, nextPart) => {
    if (!nextPart) return prevPart
    const merged = { ...(prevPart || {}) }
    Object.entries(nextPart).forEach(([key, value]) => {
      if (Array.isArray(value)) {
        const oldValues = Array.isArray(merged[key]) ? merged[key] : []
        merged[key] = [...new Set([...oldValues, ...value])]
      } else if (value !== null && value !== undefined && value !== '') {
        merged[key] = value
      } else if (!(key in merged)) {
        merged[key] = value
      }
    })
    return merged
  }, [])

  const handleProfileUpdate = useCallback((data) => {
    setUserProfile(prev => ({
      facts: mergeProfilePart(prev.facts, data.facts),
      preferences: mergeProfilePart(prev.preferences, data.preferences),
      phase: data.phase || prev.phase,
    }))
  }, [mergeProfilePart])

  // ── Transit pending / confirm ─────────────────────────────────────

  const handleTransitChange = useCallback((nodeId, newTransit) => {
    setTransitPending(prev => ({ ...prev, [nodeId]: newTransit }))
  }, [])

  const handleTransitConfirm = useCallback(async () => {
    if (!sessionId || Object.keys(transitPending).length === 0) return
    let latestNodes = null
    for (const [nodeId, transit] of Object.entries(transitPending)) {
      const result = await updateNodeTime(sessionId, nodeId, { transit })
      if (result.nodes) latestNodes = result.nodes
    }
    if (latestNodes) {
      updateItinerary(latestNodes)
    }
    setTransitPending({})
  }, [sessionId, transitPending, updateItinerary])

  const handleTransitDiscard = useCallback(() => {
    setTransitPending({})
  }, [])

  const handleReplacementSelect = useCallback((node, alternative) => {
    setReplacementPending({ node, alternative })
  }, [])

  const handleReplacementConfirm = useCallback(async () => {
    if (!sessionId || !replacementPending || replacementBusy) return
    setReplacementBusy(true)
    const { node, alternative } = replacementPending
    try {
      const result = await replaceNode(sessionId, node.id, alternative)
      if (result.blocked || result.error) {
        setReplacementResult({
          id: Date.now(),
          error: true,
          message: result.reason || result.error || '替换失败，请稍后重试。',
        })
        return
      }
      if (result.nodes) {
        updateItinerary(result.nodes)
        setReplacementResult({
          id: Date.now(),
          nodes: result.nodes,
          replacedNode: result.replaced_node,
          message: result.message || `已替换为 ${alternative.name}`,
          shouldShowRedirect: Boolean(result.should_show_redirect),
        })
      }
      setReplacementPending(null)
    } finally {
      setReplacementBusy(false)
    }
  }, [sessionId, replacementPending, replacementBusy, updateItinerary])

  const handleReplacementDiscard = useCallback(() => {
    setReplacementPending(null)
  }, [])

  const handleNewRoundStart = useCallback(() => {
    setMonitorState(null)
    setTaxiStatus(null)
    setTaxiPrompt(null)
    setTransitPending({})
    setReplacementPending(null)
    setReplacementResult(null)
    setUserProfile({ facts: null, preferences: null, phase: 'gathering' })
  }, [])

  const handleNodeTimeChange = useCallback(async (nodeId, updates) => {
    if (!sessionId) return
    setItinerary(prev => prev.map(node => node.id === nodeId ? { ...node, ...updates } : node))
    const result = await updateNodeTime(sessionId, nodeId, updates)
    if (result.nodes) updateItinerary(result.nodes)
  }, [sessionId, updateItinerary])

  const hasItinerary    = itinerary.length > 0
  const pendingCount    = Object.keys(transitPending).length

  return (
    <div className="app-wrapper">
      <UserProfilePanel
        facts={userProfile.facts}
        preferences={userProfile.preferences}
        phase={userProfile.phase}
      />

      {/* Phone frame */}
      <div className="phone-frame">
        <div className="phone-notch" />
        <div className="status-bar">
          <span className="status-time">9:41</span>
          <div className="status-icons"><span>▪▪▪</span><span>🔋</span></div>
        </div>

        {/* Location chip */}
        {detectedLocation && (
          <div className="location-chip">
            📍 {detectedLocation.district} · {detectedLocation.address}
          </div>
        )}

        <ChatPage
          sessionId={sessionId}
          onMonitorUpdate={setMonitorState}
          onItineraryUpdate={updateItinerary}
          onProfileUpdate={handleProfileUpdate}
          onNewRoundStart={handleNewRoundStart}
          itinerary={itinerary}
          onCheckin={handleCheckin}
          taxiStatus={taxiStatus}
          onTransitChange={handleTransitChange}
          onNodeTimeChange={handleNodeTimeChange}
          onReplacementSelect={handleReplacementSelect}
          replacementResult={replacementResult}
        />

        {/* Floating transit confirm bar */}
        {pendingCount > 0 && (
          <div className="transit-confirm-bar">
            <span className="transit-confirm-text">已调整 {pendingCount} 段交通</span>
            <button className="transit-confirm-discard" onClick={handleTransitDiscard}>忽略</button>
            <button className="transit-confirm-ok" onClick={handleTransitConfirm}>确认调整</button>
          </div>
        )}

        {taxiPrompt && (
          <div className="transit-confirm-bar" style={{ bottom: pendingCount > 0 ? 122 : 70 }}>
            <span className="transit-confirm-text">下一站需要打车到 {taxiPrompt.name}，现在叫车吗？</span>
            <button className="transit-confirm-discard" onClick={handleTaxiPromptDiscard}>稍后</button>
            <button className="transit-confirm-ok" onClick={handleTaxiPromptConfirm}>确认打车</button>
          </div>
        )}

        {replacementPending && (
          <div className="transit-confirm-bar" style={{ bottom: pendingCount > 0 ? (taxiPrompt ? 174 : 122) : (taxiPrompt ? 122 : 70) }}>
            <span className="transit-confirm-text">替换为 {replacementPending.alternative.name}</span>
            <button className="transit-confirm-discard" onClick={handleReplacementDiscard} disabled={replacementBusy}>忽略</button>
            <button className="transit-confirm-ok" onClick={handleReplacementConfirm} disabled={replacementBusy}>
              {replacementBusy ? '替换中...' : '确认替换'}
            </button>
          </div>
        )}

        {hasItinerary && (
          <button
            className="itin-fab"
            onClick={() => setShowSheet(true)}
            title="查看完整行程"
          >
            🗺️
            <span className="itin-fab-count">{itinerary.length}</span>
          </button>
        )}

        {showSheet && (
          <ItinerarySheet
            itinerary={itinerary}
            onClose={() => setShowSheet(false)}
            onShare={handleShare}
            onCallTaxi={handleCallTaxi}
            onTransitChange={handleTransitChange}
          />
        )}

        {showShare && (
          <ShareModal
            itinerary={itinerary}
            onClose={() => setShowShare(false)}
          />
        )}
      </div>

      <button
        className="monitor-toggle-btn"
        onClick={() => setShowMonitor(v => !v)}
        title={showMonitor ? '隐藏监控面板' : '显示监控面板'}
      >
        {showMonitor ? '◀ 隐藏' : '▶ 监控'}
      </button>

      {showMonitor && (
        <MonitorPanel
          sessionId={sessionId}
          monitorState={monitorState}
        />
      )}
    </div>
  )
}
