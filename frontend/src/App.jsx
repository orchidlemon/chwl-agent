import React, { useCallback, useEffect, useState } from 'react'
import ChatPage from './ChatPage'
import MonitorPanel from './components/MonitorPanel'
import UserProfilePanel from './components/UserProfilePanel'
import ItinerarySheet from './components/ItinerarySheet'
import ShareModal from './components/ShareModal'
import { getOrCreateSession, checkinNode, dispatchTaxi, getUserLocation } from './api/agentClient'
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

  const handleTransitConfirm = useCallback(() => {
    setItinerary(prev => prev.map(node =>
      transitPending[node.id] ? { ...node, transit: transitPending[node.id] } : node
    ))
    setTransitPending({})
  }, [transitPending])

  const handleTransitDiscard = useCallback(() => {
    setTransitPending({})
  }, [])

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
          itinerary={itinerary}
          onCheckin={handleCheckin}
          taxiStatus={taxiStatus}
          onTransitChange={handleTransitChange}
        />

        {/* Floating transit confirm bar */}
        {pendingCount > 0 && (
          <div className="transit-confirm-bar">
            <span className="transit-confirm-text">已调整 {pendingCount} 段交通</span>
            <button className="transit-confirm-discard" onClick={handleTransitDiscard}>忽略</button>
            <button className="transit-confirm-ok" onClick={handleTransitConfirm}>确认调整</button>
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
