import React, { useState, useEffect } from 'react'
import ItineraryCards from './ItineraryCards'

// ── Agent bubble wrapper ──────────────────────────────────────────
function AgentWrap({ children, noAvatar }) {
  return (
    <div className="msg-row agent">
      {!noAvatar && <div className="msg-avatar">🤖</div>}
      <div className="msg-body">{children}</div>
    </div>
  )
}

// ── User bubble ───────────────────────────────────────────────────
function UserBubble({ content }) {
  return (
    <div className="msg-row user">
      <div className="msg-bubble user">{content}</div>
    </div>
  )
}

// ── Plain text ────────────────────────────────────────────────────
function TextBubble({ content }) {
  return (
    <AgentWrap>
      <div className="msg-bubble agent">
        {content.split('\n').map((line, i) =>
          line ? <div key={i}>{line}</div> : <br key={i} />
        )}
      </div>
    </AgentWrap>
  )
}

// ── CoT thinking ─────────────────────────────────────────────────
function ThinkingBubble({ steps, collapsed: initCollapsed }) {
  const [collapsed, setCollapsed] = useState(initCollapsed)
  const done = steps.some(s => s.startsWith('✅'))

  return (
    <AgentWrap>
      <div className="thinking-box">
        <div className="thinking-header" onClick={() => setCollapsed(c => !c)}>
          <div className="thinking-header-left">
            {done
              ? <span className="thinking-dot done" />
              : <span className="thinking-dot pulse" />}
            <span className="thinking-label">
              {done ? '查看规划思路' : '正在思考中…'}
            </span>
          </div>
          <span className="thinking-toggle">{collapsed ? '展开 ▾' : '收起 ▴'}</span>
        </div>
        {!collapsed && (
          <div className="thinking-steps">
            {steps.length === 0 ? (
              <div className="thinking-step muted">初始化规划器…</div>
            ) : (
              steps.map((s, i) => (
                <div key={i} className={`thinking-step ${s.startsWith('✅') ? 'success' : ''}`}>
                  <span className="thinking-bullet">◆</span>
                  <span>{s}</span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </AgentWrap>
  )
}

// ── Status stream ─────────────────────────────────────────────────
function StatusBubble({ items }) {
  return (
    <AgentWrap>
      <div className="status-list">
        {items.map(item => (
          <div key={item.id} className="status-item">
            <div className={`status-icon ${item.status}`}>
              {item.status === 'done' ? '✓' : item.status === 'loading' ? '⟳' : '○'}
            </div>
            <span className="status-text">{item.text}</span>
            {item.status === 'loading' && <div className="spinner" />}
            {item.status === 'done' && <span className="check">✓</span>}
          </div>
        ))}
      </div>
    </AgentWrap>
  )
}

// ── Fulfillment progress ──────────────────────────────────────────
function FulfillBubble({ items, progress }) {
  return (
    <AgentWrap>
      <div className="fulfill-box">
        <div className="fulfill-title">
          {progress >= 100 ? '🎉 安排完成！' : '⚡ 正在帮你安排…'}
        </div>
        {progress > 0 && progress < 100 && (
          <div className="prog-bar-wrap">
            <div className="prog-bar-fill" style={{ width: `${progress}%` }} />
          </div>
        )}
        <div className="fulfill-items">
          {items.map((item, i) => (
            <div key={item.id || i} className="fulfill-item">
              <span className="fulfill-icon">{item.icon}</span>
              <div className="fulfill-info">
                <div className="fulfill-name">{item.name}</div>
                <div className={`fulfill-action ${item.status === 'done' ? 'done' : ''}`}>
                  {item.status === 'done' ? `✅ ${item.action}` : item.action}
                </div>
              </div>
              {item.status === 'loading' && <div className="spinner sm" />}
              {item.status === 'done' && <span style={{ color: 'var(--success)', fontSize: 18 }}>✓</span>}
            </div>
          ))}
        </div>
      </div>
    </AgentWrap>
  )
}

// ── Exception card ────────────────────────────────────────────────
function ExceptionCard({ data, onConfirm, onDismiss }) {
  const [done, setDone] = useState(false)
  const isWeather = data.exception_type === 'weather_heavy_rain'
  const alt = data.alternative || {}

  if (done) {
    return (
      <AgentWrap>
        <div className="msg-bubble agent muted">已处理 ✓</div>
      </AgentWrap>
    )
  }

  return (
    <AgentWrap>
      <div className="exception-card">
        <div className="exception-badge">
          {isWeather ? '⛈ 天气预警' : '⚠️ 实时异常'}
        </div>
        <div className="exception-title">{data.title}</div>
        <div className="exception-msg">{data.message}</div>

        {data.original && (
          <div className="exception-original">
            ❌ 原方案「{data.original.name}」
            {data.exception_type === 'queue_spike' ? ' — 排队约90分钟' : ' — 受天气影响'}
          </div>
        )}

        {alt.name && (
          <div className="exception-alt">
            <div className="exception-alt-header">⭐ 推荐备选</div>
            <div className="exception-alt-name">{alt.icon || '🔄'} {alt.name}</div>
            <div className="exception-alt-meta">
              {alt.sub} · ⏱ {alt.queueText} · 📍 {alt.distance}
            </div>
            {alt.reason && <div className="exception-alt-reason">✅ {alt.reason}</div>}
          </div>
        )}

        <div className="exception-actions">
          <button className="btn-confirm-sm" onClick={() => { setDone(true); onConfirm(data) }}>
            同意切换
          </button>
          <button className="btn-dismiss-sm" onClick={() => { setDone(true); onDismiss?.(data) }}>
            暂不处理
          </button>
        </div>
      </div>
    </AgentWrap>
  )
}

// ── Report options ────────────────────────────────────────────────
const REPORT_OPTIONS = [
  { type: 'child_tired',    label: '😴 孩子/朋友累了',  sub: '缩短后续行程' },
  { type: 'queue_too_long', label: '😤 排队太久了',     sub: '找附近替换方案' },
  { type: 'weather',        label: '🌧 天气不好',       sub: '换室内活动' },
  { type: 'lost',           label: '🗺️ 找不到地方',    sub: '重新生成路线' },
]

function ReportBubble({ prompt, onSelect }) {
  const [done, setDone] = useState(false)
  if (done) return null
  return (
    <AgentWrap>
      <div className="report-box">
        <div className="msg-bubble agent" style={{ marginBottom: 10 }}>{prompt}</div>
        <div className="report-options">
          {REPORT_OPTIONS.map(opt => (
            <button
              key={opt.type}
              className="report-option"
              onClick={() => { setDone(true); onSelect(opt.type, opt.label) }}
            >
              <span className="report-opt-label">{opt.label}</span>
              <span className="report-opt-sub">{opt.sub}</span>
            </button>
          ))}
        </div>
      </div>
    </AgentWrap>
  )
}

// ── Booking reminder card ─────────────────────────────────────────
function BookingReminderCard({ content, name }) {
  const [dismissed, setDismissed] = useState(false)
  if (dismissed) return null
  return (
    <AgentWrap>
      <div className="booking-reminder-card">
        <div className="booking-reminder-badge">📅 预约提醒</div>
        <div className="booking-reminder-name">{name}</div>
        <div className="booking-reminder-msg">{content}</div>
        <div className="booking-reminder-actions">
          <button className="btn-confirm-sm" onClick={() => setDismissed(true)}>
            知道了，去预约
          </button>
          <button className="btn-dismiss-sm" onClick={() => setDismissed(true)}>
            稍后处理
          </button>
        </div>
      </div>
    </AgentWrap>
  )
}

// ── Soft-lock confirm card ────────────────────────────────────────
function SoftLockConfirmCard({ nodeId, action, reason, requestId, onConfirm, onDismiss }) {
  const [done, setDone] = useState(false)
  if (done) return null
  const actionLabel = action === 'delete' ? '确认取消预约' : '确认替换'
  return (
    <AgentWrap>
      <div className="soft-lock-card">
        <div className="soft-lock-badge">🔒 资源损失提醒</div>
        <div className="soft-lock-msg">{reason}</div>
        <div className="exception-actions">
          <button
            className="btn-danger-sm"
            onClick={() => { setDone(true); onConfirm(nodeId, action, requestId) }}
          >
            {actionLabel}
          </button>
          <button className="btn-dismiss-sm" onClick={() => { setDone(true); onDismiss?.(nodeId, action, requestId) }}>
            保留，我再想想
          </button>
        </div>
      </div>
    </AgentWrap>
  )
}

// ── Booking redirect toast ────────────────────────────────────────────
function BookingRedirectToast({ name, site, mockUrl }) {
  const [visible, setVisible] = useState(true)
  useEffect(() => {
    const t = setTimeout(() => setVisible(false), 3500)
    return () => clearTimeout(t)
  }, [])
  if (!visible) return null
  return (
    <AgentWrap noAvatar>
      <div className="booking-redirect-toast">
        <span className="brt-icon">🔗</span>
        <div className="brt-body">
          <div className="brt-title">正在跳转到 {site}</div>
          <div className="brt-name">{name}</div>
          <div className="brt-url">{mockUrl}</div>
        </div>
        <button className="brt-close" onClick={() => setVisible(false)}>×</button>
      </div>
    </AgentWrap>
  )
}

// ── Typing indicator ──────────────────────────────────────────────
function TypingBubble() {
  return (
    <AgentWrap>
      <div className="msg-bubble agent" style={{ padding: 0, background: 'var(--white)', border: '1px solid var(--gray-5)' }}>
        <div className="typing-dots">
          <span /><span /><span />
        </div>
      </div>
    </AgentWrap>
  )
}

// ── Monitor alert bubble ──────────────────────────────────────────
function MonitorAlertBubble({ content, severity }) {
  const colors = {
    high:   { bg: 'var(--danger-light)',  border: 'var(--danger)',  icon: '⚠️' },
    medium: { bg: 'var(--warning-light)', border: 'var(--warning)', icon: '📊' },
    low:    { bg: 'var(--info-light)',    border: 'var(--info)',    icon: 'ℹ️' },
  }
  const c = colors[severity] || colors.medium
  return (
    <AgentWrap>
      <div className="msg-bubble agent" style={{
        borderLeft: `3px solid ${c.border}`,
        background: c.bg,
        fontSize: 'var(--font-sm)',
      }}>
        {c.icon} {content}
      </div>
    </AgentWrap>
  )
}

// ── Main dispatcher ───────────────────────────────────────────────
export default function ChatMessage({ msg, onNodeAction, onTransitChange, onExceptionConfirm, onExceptionDismiss, onReportSelect, onSoftLockConfirm, onSoftLockDismiss }) {
  if (msg.role === 'user') return <UserBubble content={msg.content} />

  switch (msg.type) {
    case 'typing':    return <TypingBubble />
    case 'text':      return <TextBubble content={msg.content} />
    case 'thinking':  return <ThinkingBubble steps={msg.steps} collapsed={msg.collapsed} />
    case 'status':    return msg.items?.length > 0 ? <StatusBubble items={msg.items} /> : null
    case 'itinerary': return (
      <AgentWrap noAvatar>
        <ItineraryCards
          nodes={msg.nodes}
          summary={msg.summary}
          onNodeAction={onNodeAction}
          onTransitChange={onTransitChange}
        />
      </AgentWrap>
    )
    case 'soft_lock_confirm': return (
      <SoftLockConfirmCard
        nodeId={msg.node_id}
        action={msg.action}
        reason={msg.reason}
        requestId={msg.request_id}
        onConfirm={onSoftLockConfirm}
        onDismiss={onSoftLockDismiss}
      />
    )
    case 'fulfill':          return <FulfillBubble items={msg.items} progress={msg.progress} />
    case 'exception':        return <ExceptionCard data={msg.data} onConfirm={onExceptionConfirm} onDismiss={onExceptionDismiss} />
    case 'report':           return <ReportBubble prompt={msg.prompt} onSelect={onReportSelect} />
    case 'booking_reminder':  return <BookingReminderCard content={msg.content} name={msg.name} />
    case 'monitor_alert':     return <MonitorAlertBubble content={msg.content} severity={msg.severity} />
    default:                 return null
  }
}
