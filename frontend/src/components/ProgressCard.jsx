import React from 'react'

export default function ProgressCard({ itinerary, onCheckin, taxiStatus }) {
  const nodes = itinerary || []
  const doneCount = nodes.filter(n => n._checked).length
  const total = nodes.length

  if (total === 0) return null

  if (doneCount === total) {
    return (
      <div className="progress-card progress-card--done">
        <span className="progress-card-done-icon">🎉</span>
        <span className="progress-card-done-text">今日行程全部完成！</span>
      </div>
    )
  }

  const node = nodes.find(n => !n._checked)
  if (!node) return null

  // Taxi status for current node
  const taxi = taxiStatus?.nodeId === node.id ? taxiStatus : null

  return (
    <div className="progress-card">
      <div className="progress-card-body">
        <div className="progress-card-badge">
          ⏳ 下一站
          <span className="progress-card-count">{doneCount}/{total}</span>
        </div>

        {/* Only show location name — no transit mode */}
        <div className="progress-card-row">
          <span className="progress-card-time">{node.timeStart}</span>
          <span className="progress-card-icon">{node.icon}</span>
          <span className="progress-card-name">{node.name}</span>
        </div>

        {/* Taxi status line */}
        {taxi && (
          <div className={`progress-card-taxi ${taxi.status}`}>
            {taxi.status === 'requesting' && (
              <>
                <span className="progress-card-taxi-dot" />
                🚕 正在为您叫车...
              </>
            )}
            {taxi.status === 'success' && (
              <>
                ✅ {taxi.plateNo} · {taxi.driver}
                {taxi.eta && ` · ${taxi.eta}分钟到达`}
              </>
            )}
            {taxi.status === 'error' && (
              <>⚠️ 叫车失败，请重试</>
            )}
          </div>
        )}

        {/* Queue info (only if no taxi active) */}
        {!taxi && node.queueMin > 0 && node.queueText !== '无需排队' && (
          <div className="progress-card-meta">
            <span>🕐 {node.queueText}</span>
            {node.distance && <span>· 📍 {node.distance}</span>}
          </div>
        )}
      </div>

      <button
        className="progress-checkbox"
        onClick={() => onCheckin(node.id)}
        title="打卡完成此节点"
      >
        <span className="progress-checkbox-box" />
      </button>
    </div>
  )
}
