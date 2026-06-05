import React, { useRef, useState } from 'react'

export default function ChatInput({ quickReplies, onSend, onQuickReply, disabled }) {
  const [value, setValue] = useState('')
  const ref = useRef(null)

  const submit = () => {
    const t = value.trim()
    if (!t || disabled) return
    onSend(t)
    setValue('')
    ref.current?.focus()
  }

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }

  return (
    <div className="chat-input-area">
      {/* Quick reply chips */}
      {quickReplies?.length > 0 && (
        <div className="quick-replies">
          {quickReplies.map(r => (
            <button
              key={r.action}
              className={`qr-chip ${r.action === 'fulfill' ? 'primary' : ''}`}
              onClick={() => onQuickReply(r.action)}
              disabled={disabled}
            >
              {r.label}
            </button>
          ))}
        </div>
      )}

      {/* Input row */}
      <div className="chat-input-row">
        <textarea
          ref={ref}
          className="chat-input-box"
          rows={1}
          placeholder={disabled ? '等待中…' : '告诉我你想怎么安排…'}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={onKey}
          disabled={disabled}
        />
        <button
          className={`send-btn ${value.trim() && !disabled ? 'active' : ''}`}
          onClick={submit}
          disabled={disabled || !value.trim()}
        >
          ↑
        </button>
      </div>
    </div>
  )
}
