import React, { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api/agentClient'

const SOURCE_LABELS_INJ = { label: '💬 测试者', color: '#FA8C16' }

const SOURCE_LABELS = {
  simulator:  { label: '🤖 模拟器Agent', color: '#722ED1' },
  main_agent: { label: '🔍 主Agent',     color: '#1890FF' },
  sandbox:    { label: '📊 Mock API',    color: '#52C41A' },
}

const TREND_ICON = { rising: '↑', falling: '↓', stable: '→' }
const TREND_COLOR = { rising: 'var(--danger)', falling: 'var(--success)', stable: 'var(--gray-3)' }

function QueueBar({ waitMin }) {
  const pct = Math.min(100, (waitMin / 90) * 100)
  const color = waitMin > 60 ? 'var(--danger)' : waitMin > 30 ? 'var(--warning)' : 'var(--success)'
  return (
    <div style={{ height: 5, background: 'var(--gray-6)', borderRadius: 3, overflow: 'hidden', marginTop: 4 }}>
      <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3, transition: 'width .5s ease' }} />
    </div>
  )
}

function MiniChart({ history }) {
  if (!history || history.length < 2) return null
  const max = Math.max(...history, 1)
  const w = 60, h = 24
  const pts = history.map((v, i) => {
    const x = (i / (history.length - 1)) * w
    const y = h - (v / max) * h
    return `${x},${y}`
  }).join(' ')
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke="var(--primary)" strokeWidth="1.5"
                strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

export default function MonitorPanel({ sessionId, monitorState, onSimulatorAdvance }) {
  const [autoSim, setAutoSim]         = useState(false)
  const [simLoading, setSimLoading]   = useState(false)
  const [lastEvent, setLastEvent]     = useState(null)
  const [injectText, setInjectText]   = useState('')
  const [injectLog, setInjectLog]     = useState([])
  const autoSimRef   = useRef(null)
  const logBottomRef = useRef(null)
  const injectInputRef = useRef(null)

  useEffect(() => {
    logBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [monitorState?.recent_events])

  // Auto-simulate
  useEffect(() => {
    if (autoSim) {
      autoSimRef.current = setInterval(() => {
        handleSimulate()
      }, 30000)
    } else {
      clearInterval(autoSimRef.current)
    }
    return () => clearInterval(autoSimRef.current)
  }, [autoSim, sessionId])

  const handleSimulate = useCallback(async () => {
    if (!sessionId || simLoading) return
    setSimLoading(true)
    const result = await api.advanceSimulator(sessionId)
    if (!result.error) {
      setLastEvent(result)
      if (onSimulatorAdvance) onSimulatorAdvance(result)
    }
    setSimLoading(false)
  }, [sessionId, simLoading, onSimulatorAdvance])

  const handleInject = useCallback(async () => {
    const text = injectText.trim()
    if (!text || !sessionId || simLoading) return
    setSimLoading(true)
    setInjectText('')
    // Log the user's input immediately
    setInjectLog(prev => [...prev, { role: 'user', text }])
    const result = await api.injectSimulatorEvent(sessionId, text)
    if (!result.error) {
      const reply = result.agent_dialogue || result.event?.message || '事件已注入'
      setInjectLog(prev => [...prev, { role: 'agent', text: reply }])
      setLastEvent(result)
      if (onSimulatorAdvance) onSimulatorAdvance(result)
    } else {
      setInjectLog(prev => [...prev, { role: 'agent', text: '注入失败，请重试' }])
    }
    setSimLoading(false)
    injectInputRef.current?.focus()
  }, [sessionId, simLoading, injectText, onSimulatorAdvance])

  const state = monitorState || {}
  const queues = state.restaurant_queues || []
  const events = state.recent_events || []
  const weather = state.weather || {}
  const phase = state.phase || 'gathering'

  const weatherIcon = {
    sunny: '☀️', cloudy: '⛅', light_rain: '🌦️',
    heavy_rain: '⛈️', overcast: '🌥️',
  }[weather.condition] || '🌤️'

  const phaseLabel = {
    gathering: '等待用户', confirming: '需求确认', planning: '规划中',
    monitoring: '监控运行中',
  }[phase] || phase

  return (
    <div className="monitor-panel">
      {/* Header */}
      <div className="monitor-header">
        <div className="monitor-title">
          <span className="monitor-title-icon">🔍</span>
          实时监控后台
        </div>
        <div className="monitor-phase">
          <span className={`monitor-phase-dot ${phase === 'monitoring' ? 'active' : ''}`} />
          {phaseLabel}
        </div>
      </div>

      {/* Weather strip */}
      <div className="monitor-weather">
        <span className="monitor-weather-icon">{weatherIcon}</span>
        <span className="monitor-weather-text">
          {weather.location || '北京朝阳区'} · {weather.temperature_c || '--'}°C
          · {weather.condition === 'heavy_rain' ? '大雨⚠️' :
             weather.condition === 'light_rain' ? '小雨' : '天气良好'}
        </span>
        {weather.risk_level === 'high' && (
          <span className="monitor-weather-alert">风险高</span>
        )}
      </div>

      {/* Queue status */}
      {queues.length > 0 && (
        <div className="monitor-section">
          <div className="monitor-section-title">🍽️ 餐厅排队监控</div>
          {queues.map(q => (
            <div key={q.poi_id} className="monitor-queue-card">
              <div className="monitor-queue-header">
                <span className="monitor-queue-name">{q.name}</span>
                <span className="monitor-queue-trend"
                      style={{ color: TREND_COLOR[q.trend] || 'var(--gray-3)' }}>
                  {TREND_ICON[q.trend] || '→'}
                </span>
              </div>
              <div className="monitor-queue-body">
                <div className="monitor-queue-wait">
                  <span className="monitor-queue-min">{q.wait_min}</span>
                  <span className="monitor-queue-unit">分钟</span>
                  <span className="monitor-queue-tables">（{q.queue_tables} 桌）</span>
                </div>
                <MiniChart history={q.history} />
              </div>
              <QueueBar waitMin={q.wait_min} />
              <div className="monitor-queue-status"
                   style={{ color: q.wait_min > 45 ? 'var(--danger)' : q.wait_min > 25 ? 'var(--warning)' : 'var(--success)' }}>
                {q.wait_min > 60 ? '⚠️ 高峰期，建议提前或错峰' :
                 q.wait_min > 30 ? '📊 排队较长，注意时间' :
                 '✅ 排队正常'}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Simulator controls + natural language inject */}
      <div className="monitor-section">
        <div className="monitor-section-title">🤖 模拟器Agent</div>
        <div className="monitor-sim-controls">
          <button
            className={`monitor-sim-btn ${simLoading ? 'loading' : ''}`}
            onClick={handleSimulate}
            disabled={simLoading || !sessionId}
          >
            {simLoading ? '⟳ 模拟中...' : '▶ 随机事件'}
          </button>
          <label className="monitor-auto-toggle">
            <input
              type="checkbox"
              checked={autoSim}
              onChange={e => setAutoSim(e.target.checked)}
            />
            <span>自动 30s</span>
          </label>
        </div>

        {/* Natural language inject dialog */}
        <div className="monitor-inject-box">
          <div className="monitor-inject-title">💬 描述模拟事件</div>
          <div className="monitor-inject-log">
            {injectLog.length === 0 ? (
              <div className="monitor-inject-hint">
                用自然语言描述事件，如：<br />
                「餐厅开始排队60分钟」<br />
                「突然下大雨了」
              </div>
            ) : (
              injectLog.map((item, i) => (
                <div
                  key={i}
                  className={`monitor-inject-msg monitor-inject-msg--${item.role}`}
                >
                  {item.text}
                </div>
              ))
            )}
          </div>
          <div className="monitor-inject-input-row">
            <input
              ref={injectInputRef}
              className="monitor-inject-input"
              type="text"
              placeholder="描述要触发的事件..."
              value={injectText}
              onChange={e => setInjectText(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleInject()}
              disabled={simLoading || !sessionId}
            />
            <button
              className="monitor-inject-send"
              onClick={handleInject}
              disabled={simLoading || !injectText.trim() || !sessionId}
            >
              注入
            </button>
          </div>
        </div>
      </div>

      {/* Agent dialogue log */}
      <div className="monitor-section monitor-log-section">
        <div className="monitor-section-title">📝 Agent 对话日志</div>
        <div className="monitor-log">
          {events.length === 0 ? (
            <div className="monitor-log-empty">等待事件...</div>
          ) : (
            events.map((e, i) => {
              const src = SOURCE_LABELS[e.source] || { label: e.source, color: 'var(--gray-3)' }
              return (
                <div key={i} className="monitor-log-item">
                  <div className="monitor-log-time">{e.time}</div>
                  <div className="monitor-log-source" style={{ color: src.color }}>
                    {src.label}
                  </div>
                  <div className="monitor-log-msg">{e.message}</div>
                </div>
              )
            })
          )}
          <div ref={logBottomRef} />
        </div>
      </div>
    </div>
  )
}
