import React, { useRef, useState } from 'react'
import html2canvas from 'html2canvas'

const TRANSIT_ICONS  = { taxi: '🚕', driving: '🚗', walking: '🚶', transit: '🚌' }
const TRANSIT_LABELS = { taxi: '打车', driving: '开车', walking: '步行', transit: '公交' }

function timePeriod(hhmm) {
  if (!hhmm) return ''
  const h = parseInt(hhmm.split(':')[0], 10)
  if (isNaN(h)) return ''
  if (h < 6)  return '凌晨'
  if (h < 12) return '上午'
  if (h < 13) return '中午'
  if (h < 18) return '下午'
  if (h < 20) return '傍晚'
  return '晚上'
}

function softTime(hhmm) {
  if (!hhmm) return ''
  const [hStr, mStr] = hhmm.split(':')
  const h = parseInt(hStr, 10)
  const m = parseInt(mStr, 10)
  if (isNaN(h) || isNaN(m)) return hhmm
  const rm = Math.round(m / 5) * 5
  const rh = rm === 60 ? h + 1 : h
  const fm = rm === 60 ? 0 : rm
  return `${rh}:${fm.toString().padStart(2, '0')}`
}

const CLOSING_RE = /(\d{1,2})[：:点](\d{0,2})\s*[闭关停]馆/

function closingWarning(riskFacts) {
  if (!riskFacts?.length) return null
  for (const fact of riskFacts) {
    if (CLOSING_RE.test(fact)) return fact
  }
  return null
}

const SHARE_APPS = [
  { id: 'wechat',   icon: '💚', name: '微信好友',  color: '#07C160' },
  { id: 'moments',  icon: '🟢', name: '朋友圈',    color: '#07C160' },
  { id: 'qq',       icon: '🐧', name: 'QQ',        color: '#1B8BEC' },
  { id: 'weibo',    icon: '🔴', name: '微博',      color: '#E6162D' },
]

const OTHER_ACTIONS = [
  { id: 'save',  icon: '💾', name: '保存图片' },
  { id: 'copy',  icon: '📋', name: '复制链接' },
]

export default function ShareModal({ itinerary, onClose }) {
  const [status, setStatus] = useState(null)  // null | 'loading' | { name }
  const previewRef = useRef(null)

  const handleShare = (name) => {
    if (name === '保存图片') {
      handleSaveImage()
      return
    }
    setStatus('loading')
    setTimeout(() => setStatus({ name }), 900)
  }

  const handleSaveImage = async () => {
    setStatus('loading')
    try {
      const el = previewRef.current
      if (!el) throw new Error('no element')
      const canvas = await html2canvas(el, {
        backgroundColor: '#FFF3EC',
        scale: 2,
        useCORS: true,
        logging: false,
      })
      const link = document.createElement('a')
      link.download = `美团行程_${new Date().toLocaleDateString('zh-CN').replace(/\//g, '-')}.png`
      link.href = canvas.toDataURL('image/png')
      link.click()
      setStatus({ name: '保存图片' })
    } catch (e) {
      setStatus({ name: '保存图片' })
    }
  }

  const nodes = itinerary || []

  if (status === 'loading') {
    return (
      <div className="share-overlay">
        <div className="share-panel share-panel--result">
          <div className="share-spinner" />
          <div className="share-result-text">正在分享...</div>
        </div>
      </div>
    )
  }

  if (status?.name) {
    const isDone = status.name === '保存图片'
    return (
      <div className="share-overlay" onClick={onClose}>
        <div className="share-panel share-panel--result" onClick={e => e.stopPropagation()}>
          <div className="share-result-icon">✅</div>
          <div className="share-result-title">
            {isDone ? '图片已保存到相册' : `已分享至${status.name}`}
          </div>
          <div className="share-result-sub">分享成功，让朋友们也来打卡吧～</div>
          <button className="share-result-close" onClick={onClose}>关闭</button>
        </div>
      </div>
    )
  }

  return (
    <div className="share-overlay" onClick={onClose}>
      <div className="share-panel" onClick={e => e.stopPropagation()}>
        <div className="share-drag-handle" />
        <div className="share-title">📤 分享行程</div>

        {/* Preview card — captured by html2canvas on save */}
        <div className="share-preview" ref={previewRef}>
          <div className="share-preview-header">
            <span className="share-preview-logo">🤖</span>
            <div>
              <div className="share-preview-brand">美团本地生活助手</div>
              <div className="share-preview-sub">为你规划了今日行程</div>
            </div>
          </div>
          <div className="share-preview-nodes">
            {nodes.length === 0 ? (
              <div className="share-preview-empty">暂无行程</div>
            ) : (
              nodes.map((node, i) => (
                <React.Fragment key={node.id}>
                  {/* Transit row before every node (including first = from home) */}
                  {node.transit && (
                    <div className="share-preview-transit">
                      <span>{TRANSIT_ICONS[node.transit.mode] || '🚕'}</span>
                      <span>
                        {i === 0
                          ? `从出发地 · ${TRANSIT_LABELS[node.transit.mode] || '打车'} · 约${node.transit.duration_min}分钟`
                          : `${TRANSIT_LABELS[node.transit.mode] || '打车'} · 约${node.transit.duration_min}分钟`}
                      </span>
                    </div>
                  )}
                  <div className="share-preview-node">
                    <div className="share-preview-time-block">
                      <span className="share-preview-period">{timePeriod(node.timeStart)}</span>
                      <span className="share-preview-time">约{softTime(node.timeStart)}</span>
                    </div>
                    <span className="share-preview-icon">{node.icon}</span>
                    <div className="share-preview-name-block">
                      <span className="share-preview-name">{node.name}</span>
                      {closingWarning(node.risk_facts) && (
                        <span className="share-preview-closing">⏰ {closingWarning(node.risk_facts)}</span>
                      )}
                    </div>
                    {node._checked && <span className="share-preview-check">✓</span>}
                  </div>
                </React.Fragment>
              ))
            )}
          </div>
        </div>

        {/* Social apps */}
        <div className="share-apps">
          {SHARE_APPS.map(app => (
            <button key={app.id} className="share-app-btn" onClick={() => handleShare(app.name)}>
              <span className="share-app-icon">{app.icon}</span>
              <span className="share-app-name">{app.name}</span>
            </button>
          ))}
        </div>

        {/* Other actions */}
        <div className="share-others">
          {OTHER_ACTIONS.map(action => (
            <button key={action.id} className="share-other-btn" onClick={() => handleShare(action.name)}>
              <span>{action.icon}</span>
              <span>{action.name}</span>
            </button>
          ))}
        </div>

        <button className="share-cancel-btn" onClick={onClose}>取消</button>
      </div>
    </div>
  )
}
