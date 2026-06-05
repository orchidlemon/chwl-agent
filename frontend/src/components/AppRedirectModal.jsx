import React, { useEffect, useState } from 'react'

const MODE_CONFIG = {
  driving: {
    icon: '🗺️', app: '美团地图',
    action: '正在显示驾车路线…',
    color: '#1677FF',
  },
  walking: {
    icon: '🚶', app: '美团地图',
    action: '正在显示步行路线…',
    color: '#52C41A',
  },
  transit: {
    icon: '🚌', app: '美团地图',
    action: '正在显示公共交通路线…',
    color: '#722ED1',
  },
}

export default function AppRedirectModal({ mode, destination, onDismiss }) {
  const [progress, setProgress] = useState(0)
  const cfg = MODE_CONFIG[mode] || MODE_CONFIG.driving
  const DURATION = 2600 // ms

  useEffect(() => {
    const start = Date.now()
    const interval = setInterval(() => {
      const pct = Math.min(100, ((Date.now() - start) / DURATION) * 100)
      setProgress(pct)
      if (pct >= 100) { clearInterval(interval); setTimeout(onDismiss, 200) }
    }, 30)
    return () => clearInterval(interval)
  }, [onDismiss])

  return (
    <div className="redirect-overlay" onClick={onDismiss}>
      <div className="redirect-panel" onClick={e => e.stopPropagation()}>
        <div className="redirect-app-icon" style={{ background: cfg.color }}>
          {cfg.icon}
        </div>
        <div className="redirect-app-name">跳转 {cfg.app}</div>
        <div className="redirect-action">{cfg.action}</div>
        {destination && (
          <div className="redirect-dest">📍 前往 {destination}</div>
        )}
        <div className="redirect-progress-wrap">
          <div className="redirect-progress-bar"
               style={{ width: `${progress}%`, background: cfg.color }} />
        </div>
        <div className="redirect-hint">点击任意区域关闭</div>
      </div>
    </div>
  )
}
