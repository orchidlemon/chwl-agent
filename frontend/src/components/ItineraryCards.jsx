import React, { useEffect, useState } from 'react'

const MODE_ICONS = { taxi: '🚕', driving: '🚗', walking: '🚶', transit: '🚌' }
const MODE_LABELS = { taxi: '打车', driving: '开车', walking: '步行', transit: '公交' }

function timePeriod(hhmm) {
  if (!hhmm) return ''
  const h = parseInt(String(hhmm).split(':')[0], 10)
  if (Number.isNaN(h)) return ''
  if (h < 6) return '凌晨'
  if (h < 12) return '上午'
  if (h < 13) return '中午'
  if (h < 18) return '下午'
  if (h < 20) return '傍晚'
  return '晚上'
}

function softTime(hhmm) {
  if (!hhmm) return ''
  const [hStr, mStr] = String(hhmm).split(':')
  const h = parseInt(hStr, 10)
  const m = parseInt(mStr, 10)
  if (Number.isNaN(h) || Number.isNaN(m)) return hhmm
  const rm = Math.round(m / 5) * 5
  const rh = rm === 60 ? h + 1 : h
  const fm = rm === 60 ? 0 : rm
  return `${String(rh).padStart(2, '0')}:${String(fm).padStart(2, '0')}`
}

const CLOSING_RE = /(\d{1,2})[:点](\d{0,2}).*(闭|关|停)/

function closingWarning(riskFacts) {
  if (!riskFacts?.length) return null
  return riskFacts.find(fact => CLOSING_RE.test(fact)) || null
}

function InlineTransitBar({ transit, isFirst, onModeChange }) {
  const [mode, setMode] = useState(transit.mode || 'taxi')
  const [dur, setDur] = useState(transit.duration_min || 12)

  useEffect(() => {
    setMode(transit.mode || 'taxi')
    setDur(transit.duration_min || 12)
  }, [transit])

  const handleChange = (newMode) => {
    const base = transit.duration_min || 12
    const dist = transit.distance_km || 2.5
    const nextDur = {
      taxi: base,
      driving: Math.max(5, Math.round(base * 0.75)),
      transit: Math.max(8, Math.round(base * 1.3)),
      walking: Math.max(10, Math.round(dist * 12)),
    }[newMode]
    setMode(newMode)
    setDur(nextDur)
    onModeChange?.({ ...transit, mode: newMode, duration_min: nextDur })
  }

  return (
    <div className="nc-transit-bar">
      <div className="nc-transit-line" />
      <div className="nc-transit-content">
        <span className="nc-transit-icon">{MODE_ICONS[mode]}</span>
        <span className="nc-transit-info">
          {isFirst
            ? `从出发地 · ${MODE_LABELS[mode]} · 约${dur}分钟`
            : `${MODE_LABELS[mode]} · 约${dur}分钟 · ${(transit.distance_km || 2.5).toFixed(1)}km`}
        </span>
        <div className="nc-transit-modes">
          {Object.entries(MODE_ICONS).map(([m, icon]) => (
            <button key={m} className={`nc-transit-chip ${mode === m ? 'active' : ''}`} onClick={() => handleChange(m)} title={MODE_LABELS[m]}>
              {icon}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

const ALT_POOL = {
  family: [
    { icon: '📚', name: '彩虹树绘本馆', sub: '望京 · 1.6km', queue: '需预约', rating: 4.6 },
    { icon: '🔬', name: '儿童科学体验馆', sub: '朝阳 · 3.1km', queue: '约10分钟', rating: 4.5 },
    { icon: '🎨', name: '亲子手作乐园', sub: '易事达 · 2.8km', queue: '无需排队', rating: 4.7 },
  ],
  restaurant: [
    { icon: '🥗', name: '亲子轻食餐厅', sub: '同商场 · 0.8km', queue: '约15分钟', rating: 4.5, poi_id: 'rest_family_002' },
    { icon: '🍽️', name: '花园简餐 Bistro', sub: '望京 · 1.2km', queue: '约12分钟', rating: 4.6, poi_id: 'rest_family_001' },
    { icon: '🥘', name: '粥小厨家庭餐', sub: '望京 · 1.5km', queue: '约10分钟', rating: 4.4, poi_id: 'rest_family_003' },
  ],
}

function NodeCard({ node, idx, onAction, onTimeChange, onReplacementSelect }) {
  const [altPage, setAltPage] = useState('primary')
  const primaryAlts = node.alternatives || []
  const reserveAlts = node.alternative_reserve || []
  const alts = altPage === 'reserve' ? reserveAlts : primaryAlts
  const isPinned = node.user_pinned || node.pinned
  const isCompleted = node.completed_lock
  const [editingTime, setEditingTime] = useState(false)
  const [timeStart, setTimeStart] = useState(node.timeStart || '')
  const [timeEnd, setTimeEnd] = useState(node.timeEnd || '')
  const period = timePeriod(node.timeStart)
  const closing = closingWarning(node.risk_facts)

  const saveTime = () => {
    setEditingTime(false)
    if (timeStart !== node.timeStart || timeEnd !== node.timeEnd) {
      onTimeChange?.(node.id, { timeStart, timeEnd })
    }
  }

  return (
    <div className={`node-card ${isPinned ? 'pinned' : ''} ${node.locked ? 'locked' : ''} ${isCompleted ? 'completed' : ''}`}>
      <div className="nc-dot-wrap">
        <div className={`nc-dot ${node.status === 'optional' ? 'opt' : isPinned ? 'pin' : isCompleted ? 'done' : ''}`}>{idx + 1}</div>
      </div>
      <div className="nc-body">
        <div className="nc-header">
          <span className="nc-icon">{node.icon}</span>
          <div className="nc-title-block">
            <div className="nc-time">
              {period && <span className="nc-period">{period}</span>}
              {editingTime ? (
                <span className="nc-time-editor">
                  <input type="time" value={timeStart} onChange={e => setTimeStart(e.target.value)} />
                  <span>-</span>
                  <input type="time" value={timeEnd} onChange={e => setTimeEnd(e.target.value)} />
                  <button className="nc-time-save" onClick={saveTime}>保存</button>
                  <button className="nc-time-cancel" onClick={() => { setEditingTime(false); setTimeStart(node.timeStart || ''); setTimeEnd(node.timeEnd || '') }}>取消</button>
                </span>
              ) : (
                <button className="nc-time-edit" onClick={() => setEditingTime(true)} title="修改时间">
                  约 {softTime(node.timeStart)}-{softTime(node.timeEnd)}
                </button>
              )}
              {node.status === 'optional' && <span className="nc-badge opt">可选</span>}
              {isCompleted && <span className="nc-badge done">已完成</span>}
              {isPinned && !isCompleted && <span className="nc-badge pin">已锁定</span>}
              {node.locked && !isCompleted && <span className="nc-badge lock">已预约</span>}
            </div>
            <div className="nc-name">{node.name}</div>
            <div className="nc-sub">{node.sub}</div>
          </div>
        </div>

        {closing && <div className="nc-closing-warn">⏰ {closing}，请注意时间安排</div>}

        <div className="nc-chips">
          {node.distance && <span className="nc-chip">📍 {node.distance}</span>}
          {node.queueText != null && node.queueText !== '' && <span className={`nc-chip ${(node.queueMin || 0) > 30 ? 'warn' : 'ok'}`}>⏱ {node.queueText}</span>}
          {node.price && node.price !== '0' && <span className="nc-chip">💰 {node.price}</span>}
          {!!node.rating && <span className="nc-chip star">★ {node.rating}</span>}
        </div>

        {node.tags?.length > 0 && <div className="nc-tags">{node.tags.slice(0, 3).map(t => <span key={t} className="nc-tag">{t}</span>)}</div>}
        {node.reason && <div className="nc-reason">{node.reason}</div>}

        {!isCompleted && (
          <div className="nc-actions">
            <button className="nc-btn replace" onClick={() => onAction(node.id, 'replace')}>换一个</button>
            <button className={`nc-btn pin ${isPinned ? 'active' : ''}`} onClick={() => onAction(node.id, 'pin')}>{isPinned ? '取消锁定' : '锁定'}</button>
          </div>
        )}

        {node._showAlts && (
          <div className="alt-panel">
            <div className="alt-panel-title">附近备选方案</div>
            {alts.map((a, i) => (
              <div key={i} className="alt-item" onClick={() => onReplacementSelect?.(node, a)}>
                <span className="alt-icon">{a.icon}</span>
                <div className="alt-info"><div className="alt-name">{a.name}</div><div className="alt-meta">{a.sub} · {a.queue}</div></div>
                <div className="alt-rating">★ {a.rating}</div>
              </div>
            ))}
            {alts.length === 0 && (
              <div className="alt-item">
                <div className="alt-info"><div className="alt-name">暂无更多可用备选</div><div className="alt-meta">本轮规划候选已用尽</div></div>
              </div>
            )}
            {altPage === 'primary' && reserveAlts.length > 0 && (
              <button className="alt-more-btn" onClick={() => setAltPage('reserve')}>查看下一批备选</button>
            )}
            {altPage === 'reserve' && primaryAlts.length > 0 && (
              <button className="alt-more-btn" onClick={() => setAltPage('primary')}>返回上一批备选</button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default function ItineraryCards({ nodes, summary, onNodeAction, onTransitChange, onNodeTimeChange, onReplacementSelect }) {
  if (!nodes?.length) return null
  return (
    <div className="itinerary-wrap">
      {summary && <div className="itin-summary"><span className="itin-summary-icon">💡</span><span>{summary}</span></div>}
      <div className="itin-nodes">
        {nodes.map((node, i) => (
          <React.Fragment key={node.id}>
            {node.transit && <InlineTransitBar transit={node.transit} isFirst={i === 0} onModeChange={onTransitChange ? (tData) => onTransitChange(node.id, tData) : undefined} />}
            <NodeCard node={node} idx={i} onAction={onNodeAction} onTimeChange={onNodeTimeChange} onReplacementSelect={onReplacementSelect} />
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}
