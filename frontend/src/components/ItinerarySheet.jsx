import React, { useState } from 'react'
import TransitBar from './TransitBar'
import AppRedirectModal from './AppRedirectModal'

export default function ItinerarySheet({ itinerary, onClose, onShare, onCallTaxi, onTransitChange }) {
  const nodes = itinerary || []
  const [redirect, setRedirect] = useState(null) // { mode, destination }

  const handleTransitAction = (mode, transitInfo, destNode) => {
    if (mode === 'taxi') {
      // Taxi: close sheet, show status in ProgressCard
      onClose()
      if (onCallTaxi) onCallTaxi(destNode, transitInfo)
    } else {
      // Other: show redirect overlay
      setRedirect({ mode, destination: destNode?.name || '目的地' })
    }
  }

  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet-panel" onClick={e => e.stopPropagation()}>
        <div className="sheet-drag-handle" />

        <div className="sheet-header">
          <span className="sheet-title">🗺️ 今日行程</span>
          <div className="sheet-header-actions">
            <button className="sheet-share-btn" onClick={onShare}>📤 分享</button>
            <button className="sheet-close-btn" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="sheet-body">
          {nodes.length === 0 ? (
            <div className="sheet-empty">
              <div className="sheet-empty-icon">🗺️</div>
              <div className="sheet-empty-title">暂无路线规划</div>
              <div className="sheet-empty-sub">请在聊天中告诉我你的出行需求 ✉️</div>
            </div>
          ) : (
            <div className="sheet-nodes">
              {nodes.map((node, idx) => {
                const isDone = node._checked || node.locked
                return (
                  <React.Fragment key={node.id}>
                    {/* Transit bar BEFORE every node (including first = from home) */}
                    {node.transit && (
                      <TransitBar
                        transit={node.transit}
                        isFirst={idx === 0}
                        onCallAction={(mode, tInfo) =>
                          handleTransitAction(mode, tInfo, node)
                        }
                        onModeChange={onTransitChange
                          ? (tData) => onTransitChange(node.id, tData)
                          : undefined}
                      />
                    )}

                    {/* Node card */}
                    <div className={`sheet-node ${isDone ? 'sheet-node--done' : ''}`}>
                      <div className="sheet-node-timeline">
                        <div className="sheet-node-dot" />
                        {idx < nodes.length - 1 && <div className="sheet-node-line" />}
                      </div>
                      <div className="sheet-node-content">
                        <div className="sheet-node-time">{node.timeStart}</div>
                        <div className="sheet-node-main">
                          <span className="sheet-node-icon">{node.icon}</span>
                          <div className="sheet-node-info">
                            <div className="sheet-node-name">{node.name}</div>
                            {node.sub && (
                              <div className="sheet-node-sub">{node.sub}</div>
                            )}
                            <div className="sheet-node-tags">
                              {node.duration && (
                                <span className="sheet-node-tag">⏱ {node.duration}</span>
                              )}
                              {node.distance && (
                                <span className="sheet-node-tag">📍 {node.distance}</span>
                              )}
                              {node.queueMin > 0 && (
                                <span className="sheet-node-tag warn">
                                  🕐 排队{node.queueMin}分钟
                                </span>
                              )}
                              {node.price && (
                                <span className="sheet-node-tag">💴 {node.price}</span>
                              )}
                            </div>
                          </div>
                          {isDone && (
                            <span className="sheet-node-done-badge">✓</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </React.Fragment>
                )
              })}
            </div>
          )}
        </div>

        {/* Redirect overlay (inside sheet/phone frame) */}
        {redirect && (
          <AppRedirectModal
            mode={redirect.mode}
            destination={redirect.destination}
            onDismiss={() => setRedirect(null)}
          />
        )}
      </div>
    </div>
  )
}
