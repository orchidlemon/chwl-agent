import React, { useState } from 'react'

const MODES = [
  { id: 'walking', icon: '🚶', label: '步行' },
  { id: 'transit', icon: '🚌', label: '公交' },
  { id: 'taxi',    icon: '🚕', label: '打车' },
  { id: 'driving', icon: '🚗', label: '开车' },
]

const ACTION_LABELS = {
  taxi:    '一键打车',
  driving: '导航',
  walking: '步行导航',
  transit: '查看路线',
}

export default function TransitBar({ transit, isFirst, onCallAction, onModeChange }) {
  const [mode, setMode] = useState(transit?.mode || 'taxi')
  const [dur, setDur]   = useState(transit?.duration_min || 12)
  const [dist, setDist] = useState(transit?.distance_km || 2.5)
  const [loading, setLoading] = useState(false)

  const handleModeChange = async (newMode) => {
    if (newMode === mode) return
    setLoading(true)
    setMode(newMode)
    try {
      const modeMultipliers = { taxi: 1.0, driving: 0.75, transit: 1.3, walking: null }
      let newDur
      if (newMode === 'walking') {
        newDur = Math.max(10, Math.round(dist * 12))
      } else {
        const baseDur = transit?.duration_min || 12
        newDur = Math.max(5, Math.round(baseDur * (modeMultipliers[newMode] || 1.0)))
      }
      setDur(newDur)
      if (onModeChange) onModeChange({ ...transit, mode: newMode, duration_min: newDur })
    } finally {
      setLoading(false)
    }
  }

  if (!transit) return null

  const modeIcon = MODES.find(m => m.id === mode)?.icon || '🚕'

  return (
    <div className="transit-bar">
      <div className="transit-bar-info">
        <span className="transit-bar-icon">{modeIcon}</span>
        {isFirst
          ? <span className="transit-bar-dur">从出发地 · {loading ? '...' : `${dur}分钟`}</span>
          : <>
              <span className="transit-bar-dur">{loading ? '...' : `${dur}分钟`}</span>
              <span className="transit-bar-dist">{dist.toFixed(1)}km</span>
            </>
        }
      </div>

      <div className="transit-bar-modes">
        {MODES.map(m => (
          <button
            key={m.id}
            className={`transit-mode-chip ${mode === m.id ? 'active' : ''}`}
            onClick={() => handleModeChange(m.id)}
            title={m.label}
          >
            {m.icon}
          </button>
        ))}
      </div>

      <button
        className={`transit-action-btn transit-action-btn--${mode}`}
        onClick={() => onCallAction(mode, { ...transit, mode, duration_min: dur })}
      >
        {ACTION_LABELS[mode]}
      </button>
    </div>
  )
}
