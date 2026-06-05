import React, { useCallback, useEffect, useRef, useState } from 'react'
import ChatMessage from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import ProgressCard from './components/ProgressCard'
import * as api from './api/agentClient'

let _id = 0
const genId = () => `msg_${++_id}`

const WELCOME = {
  id: 'welcome',
  role: 'agent',
  type: 'text',
  content: '你好！我是美团本地生活助手 🤖\n\n告诉我你今天想怎么安排，比如：\n「今天下午带老婆孩子出去玩，别太远」\n「和朋友下午一起出去转转」\n\n我会先帮你确认一下出行信息，再给你规划最合适的方案。',
}

export default function ChatPage({ sessionId, onMonitorUpdate, onItineraryUpdate, onProfileUpdate, itinerary, onCheckin, taxiStatus, onTransitChange }) {
  const [messages, setMessages]             = useState([WELCOME])
  const [isStreaming, setIsStreaming]        = useState(false)
  const [quickReplies, setQuickReplies]     = useState([])
  const [itineraryMsgId, setItineraryMsgId] = useState(null)
  const [monitoringActive, setMonitoringActive] = useState(false)
  const [chatPhase, setChatPhase]           = useState('gathering')
  const [originalRequest, setOriginalRequest] = useState('')  // user's first message
  const [bookingPopup, setBookingPopup]     = useState(null)  // { name, site, mockUrl }
  const monitorPollRef = useRef(null)
  const bottomRef = useRef(null)

  // ── helpers ──────────────────────────────────────────────────────

  const append = useCallback((msg) => {
    const m = { id: genId(), ...msg }
    setMessages(prev => [...prev, m])
    return m.id
  }, [])

  const update = useCallback((id, fn) => {
    setMessages(prev => prev.map(m => m.id === id ? fn(m) : m))
  }, [])

  const removeMsg = useCallback((id) => {
    setMessages(prev => prev.filter(m => m.id !== id))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (itinerary?.length && itinerary.every(n => n._checked || n.completed_lock)) {
      setChatPhase('completed')
      setQuickReplies([])
    }
  }, [itinerary])

  // ── Monitor polling ───────────────────────────────────────────────

  const startMonitorPolling = useCallback(() => {
    if (monitorPollRef.current) return
    setMonitoringActive(true)

    monitorPollRef.current = setInterval(async () => {
      if (!sessionId) return
      const state = await api.getMonitorState(sessionId)
      if (!state) return

      // Notify parent for monitor panel update
      if (onMonitorUpdate) onMonitorUpdate(state)

      // Handle pending chat event (from simulator)
      if (state.pending_chat_event) {
        const evt = state.pending_chat_event
        const etype = evt.type || ''

        if (etype === 'node_checkin') {
          append({ role: 'agent', type: 'text', content: evt.message })
        } else if (etype === 'queue_spike') {
          append({
            role: 'agent', type: 'exception',
            data: {
              request_id: evt.request_id,
              exception_type: 'queue_spike',
              title: '餐厅排队突发拥堵',
              message: evt.message,
              original: { id: evt.node_id || 'node_002', name: evt.poi_id || '餐厅' },
              alternative: null,
            },
          })
        } else if (etype === 'weather_heavy_rain') {
          append({
            role: 'agent', type: 'exception',
            data: {
              request_id: evt.request_id,
              exception_type: 'weather_heavy_rain',
              title: '天气预警',
              message: evt.message,
              original: null,
              alternative: null,
            },
          })
        } else {
          append({
            role: 'agent', type: 'monitor_alert',
            severity: evt.severity || 'medium',
            content: evt.message,
          })
        }
      }
    }, 5000)

    return () => {
      clearInterval(monitorPollRef.current)
      monitorPollRef.current = null
    }
  }, [sessionId, append, onMonitorUpdate])

  useEffect(() => {
    return () => {
      if (monitorPollRef.current) {
        clearInterval(monitorPollRef.current)
      }
    }
  }, [])

  // ── Chat flow (phase-aware) ───────────────────────────────────────

  const runChat = useCallback(async (text, opts = {}) => {
    if (!sessionId || isStreaming) return
    setIsStreaming(true)
    setQuickReplies([])

    // Show typing indicator immediately as a chat bubble
    const typingId = append({ role: 'agent', type: 'typing' })
    let typingGone = false
    const clearTyping = () => { if (!typingGone) { typingGone = true; removeMsg(typingId) } }

    // Lazy IDs — status/cot cards only created after confirmed event
    let statusId = null
    let cotId = null

    const ensurePlanningCards = () => {
      clearTyping()
      setChatPhase('planning')
      if (!statusId) {
        statusId = append({
          role: 'agent', type: 'status',
          items: [
            { id: 1, text: '需求已确认', status: 'done' },
            { id: 2, text: '搜索附近活动', status: 'loading' },
            { id: 3, text: '查询排队情况', status: 'pending' },
            { id: 4, text: 'AI 规划中', status: 'pending' },
          ],
        })
      }
      if (!cotId) {
        cotId = append({ role: 'agent', type: 'thinking', steps: [], collapsed: false })
      }
    }

    await api.streamChat(sessionId, text, (evt) => {
      switch (evt.type) {
        // ── Phase: gathering / confirming → clarify ─────────────────
        case 'clarify':
          clearTyping()
          setChatPhase('confirming')
          append({ role: 'agent', type: 'text', content: evt.message })
          // Always show the button throughout the confirming phase
          setQuickReplies([{ label: '🗓️ 开始规划', action: 'start_plan' }])
          // Update user profile panel with latest facts
          if (onProfileUpdate && (evt.facts || evt.preferences)) onProfileUpdate({ facts: evt.facts, preferences: evt.preferences, phase: evt.phase || 'confirming' })
          break

        // ── Phase: confirming → planning ────────────────────────────
        case 'confirmed':
          clearTyping()
          setChatPhase('planning')
          if (onProfileUpdate && (evt.facts || evt.preferences)) onProfileUpdate({ facts: evt.facts, preferences: evt.preferences, phase: evt.phase || 'planning' })
          if (evt.message) {
            append({ role: 'agent', type: 'text', content: evt.message })
          }
          statusId = append({
            role: 'agent', type: 'status',
            items: [
              { id: 1, text: '需求已确认', status: 'done' },
              { id: 2, text: '搜索附近活动', status: 'loading' },
              { id: 3, text: '查询排队情况', status: 'loading' },
              { id: 4, text: 'AI 规划中', status: 'pending' },
            ],
          })
          cotId = append({ role: 'agent', type: 'thinking', steps: [], collapsed: false })
          break

        // ── Status updates ──────────────────────────────────────────
        case 'status':
          ensurePlanningCards()
          if (statusId) {
            update(statusId, m => ({
              ...m,
              items: m.items.map(i =>
                i.id === evt.id
                  ? { ...i, status: evt.status, ...(evt.text ? { text: evt.text } : {}) }
                  : i
              ),
            }))
          }
          break

        // ── CoT steps ───────────────────────────────────────────────
        case 'cot_step':
          ensurePlanningCards()
          if (cotId) {
            update(cotId, m => ({ ...m, steps: [...m.steps, evt.text] }))
          }
          break

        // ── Itinerary ready ─────────────────────────────────────────
        case 'itinerary_ready': {
          if (cotId) update(cotId, m => ({ ...m, collapsed: true }))
          const iid = append({
            role: 'agent', type: 'itinerary',
            nodes: evt.nodes || [], summary: evt.summary || '',
          })
          setItineraryMsgId(iid)
          if (onItineraryUpdate) onItineraryUpdate(evt.nodes || [])
          if (onProfileUpdate && (evt.facts || evt.preferences)) onProfileUpdate({ facts: evt.facts, preferences: evt.preferences, phase: evt.phase || 'monitoring' })
          append({
            role: 'agent', type: 'text',
            content: `行程规划好了 👆\n${evt.summary || ''}\n\n想改什么直接告诉我，比如：\n「把餐厅换成川菜」「出发时间改成下午3点」\n\n或者点「一键安排」直接预约。`,
          })
          setQuickReplies([
            { label: '✅ 一键安排', action: 'fulfill' },
            { label: '🔄 重新规划', action: 'replan' },
            { label: '😴 出问题了', action: 'report' },
          ])
          startMonitorPolling()
          break
        }

        // ── Profile updates ────────────────────────────────────────
        case 'profile_updated':
          if (onProfileUpdate) onProfileUpdate({ facts: evt.facts, preferences: evt.preferences, phase: evt.phase || chatPhase })
          break

        // ── Booking reminder ────────────────────────────────────────
        case 'booking_reminder':
          append({
            role: 'agent', type: 'booking_reminder',
            poi_id: evt.poi_id,
            name: evt.name,
            content: evt.message,
          })
          break

        // ── Monitor started ──────────────────────────────────────────
        case 'monitor_started':
          append({ role: 'agent', type: 'text', content: evt.message })
          break

        // ── General text ─────────────────────────────────────────────
        case 'text':
          clearTyping()
          append({ role: 'agent', type: 'text', content: evt.content || evt.message || '' })
          break

        case 'itinerary_updated':
          if (itineraryMsgId && evt.nodes) {
            update(itineraryMsgId, m => ({ ...m, nodes: evt.nodes }))
          }
          if (onItineraryUpdate) onItineraryUpdate(evt.nodes || [])
          if (onProfileUpdate && (evt.facts || evt.preferences)) {
            onProfileUpdate({ facts: evt.facts, preferences: evt.preferences, phase: evt.phase || 'monitoring' })
          }
          break

        // ── Error ─────────────────────────────────────────────────────
        case 'error':
          clearTyping()
          append({ role: 'agent', type: 'text', content: `出了点问题：${evt.message}，请重试。` })
          break
      }
    }, opts).finally(() => {
      clearTyping()
      setIsStreaming(false)
    })
  }, [sessionId, isStreaming, append, update, removeMsg, startMonitorPolling, onProfileUpdate, onItineraryUpdate, chatPhase])

  // ── Fulfillment flow ──────────────────────────────────────────────

  const runFulfill = useCallback(async () => {
    if (!sessionId || isStreaming) return
    setIsStreaming(true)
    setQuickReplies([])
    append({ role: 'user', type: 'text', content: '好，一键帮我安排！' })

    const fulfillId = append({
      role: 'agent', type: 'fulfill',
      items: [], progress: 0,
    })

    await api.streamFulfill(sessionId, (evt) => {
      switch (evt.type) {
        case 'fulfill_init':
          update(fulfillId, m => ({ ...m, items: evt.items }))
          break
        case 'fulfill_item':
          update(fulfillId, m => ({
            ...m,
            items: m.items.map(i => i.id === evt.id ? { ...i, status: evt.status, action: evt.action } : i),
          }))
          break
        case 'fulfill_progress':
          update(fulfillId, m => ({ ...m, progress: evt.value }))
          break
        case 'booking_redirect':
          setBookingPopup({ name: evt.name, site: evt.site, mockUrl: evt.mock_url })
          setTimeout(() => setBookingPopup(null), 3500)
          break
        case 'booking_reminder':
          append({
            role: 'agent', type: 'booking_reminder',
            poi_id: evt.poi_id,
            name: evt.name,
            content: evt.message,
          })
          break
        case 'monitor_started':
          append({ role: 'agent', type: 'text', content: evt.message })
          startMonitorPolling()
          break
        case 'error':
          append({ role: 'agent', type: 'text', content: `履约出错：${evt.message}` })
          break
      }
    }).finally(() => setIsStreaming(false))
  }, [sessionId, isStreaming, append, update, startMonitorPolling])

  // ── Exception confirm ─────────────────────────────────────────────

  const confirmException = useCallback(async (exceptionData) => {
    if (!sessionId || isStreaming) return
    setIsStreaming(true)
    append({ role: 'user', type: 'text', content: '同意，帮我切换方案。' })

    const replannedId = append({
      role: 'agent', type: 'status',
      items: [{ id: 1, text: '正在切换方案...', status: 'loading' }],
    })

    await api.streamExceptionConfirm(sessionId, {
      confirmed: true,
      request_id: exceptionData.request_id,
      exception_type: exceptionData.exception_type,
      original_node_id: exceptionData.original?.id,
      recommended: exceptionData.alternative,
    }, (evt) => {
      switch (evt.type) {
        case 'itinerary_updated':
          // Update existing itinerary card in-place
          if (itineraryMsgId && evt.nodes) {
            update(itineraryMsgId, m => ({ ...m, nodes: evt.nodes }))
          }
          update(replannedId, m => ({
            ...m, items: [{ id: 1, text: '方案已切换', status: 'done' }],
          }))
          break

        case 'replan_done': {
          // Always clear the spinner
          update(replannedId, m => ({
            ...m, items: [{ id: 1, text: '方案已切换，预约完成', status: 'done' }],
          }))
          const newNodes = evt.nodes
          // Update existing itinerary card
          if (itineraryMsgId && newNodes) {
            update(itineraryMsgId, m => ({ ...m, nodes: newNodes }))
          }
          // Show updated itinerary as new card so user can see it
          if (newNodes?.length > 0) {
            const newIid = append({
              role: 'agent', type: 'itinerary',
              nodes: newNodes, summary: '',
            })
            setItineraryMsgId(newIid)
            if (onItineraryUpdate) onItineraryUpdate(newNodes)
          }
          append({ role: 'agent', type: 'text', content: '✅ 切换成功！这是更新后的行程 👆' })
          break
        }
      }
    }).finally(() => setIsStreaming(false))
  }, [sessionId, isStreaming, itineraryMsgId, append, update])

  const dismissException = useCallback(async (exceptionData) => {
    if (!sessionId) return
    append({ role: 'user', type: 'text', content: '暂不处理，先保留原方案。' })
    await api.streamExceptionConfirm(sessionId, {
      confirmed: false,
      request_id: exceptionData.request_id,
      exception_type: exceptionData.exception_type,
      original_node_id: exceptionData.original?.id,
    }, (evt) => {
      if (evt.type === 'text') {
        append({ role: 'agent', type: 'text', content: evt.content || evt.message || '' })
      }
    })
  }, [sessionId, append])

  // ── User report ───────────────────────────────────────────────────

  const submitReport = useCallback(async (type, label) => {
    if (!sessionId) return
    append({ role: 'user', type: 'text', content: label })
    const result = await api.reportIssue(sessionId, type)
    if (result.nodes && itineraryMsgId) {
      update(itineraryMsgId, m => ({ ...m, nodes: result.nodes }))
    }
    append({ role: 'agent', type: 'text', content: `已处理：${result.message || '行程已调整'}。` })
    setQuickReplies([{ label: '✅ 查看最终方案', action: 'dashboard' }])
  }, [sessionId, itineraryMsgId, append, update])

  // ── Node actions ──────────────────────────────────────────────────

  const handleNodeAction = useCallback(async (nodeId, action) => {
    if (!sessionId) return
    if (action === 'replace') {
      if (itineraryMsgId) {
        update(itineraryMsgId, m => ({
          ...m,
          nodes: m.nodes.map(n =>
            n.id === nodeId ? { ...n, _showAlts: !n._showAlts } : n
          ),
        }))
      }
      return
    }
    const result = await api.nodeAction(sessionId, nodeId, action)
    if (result.blocked) {
      append({ role: 'agent', type: 'text', content: `🔒 ${result.reason}` })
      return
    }
    if (result.soft_lock_warning) {
      append({
        role: 'agent', type: 'soft_lock_confirm',
        node_id: result.node_id,
        action:  result.action,
        reason:  result.reason,
        request_id: result.request_id,
      })
      return
    }
    if (result.nodes && itineraryMsgId) {
      update(itineraryMsgId, m => ({ ...m, nodes: result.nodes }))
    }
  }, [sessionId, itineraryMsgId, update, append])

  // User confirmed soft-lock warning → force execute
  const handleSoftLockConfirm = useCallback(async (nodeId, action, requestId) => {
    if (!sessionId) return
    const result = await api.nodeAction(sessionId, nodeId, action, true, requestId)
    if (result.nodes && itineraryMsgId) {
      update(itineraryMsgId, m => ({ ...m, nodes: result.nodes }))
      if (onItineraryUpdate) onItineraryUpdate(result.nodes)
    }
    append({ role: 'agent', type: 'text', content: '已取消预约，该节点已从行程中移除。' })
  }, [sessionId, itineraryMsgId, update, append, onItineraryUpdate])

  const handleSoftLockDismiss = useCallback(async (_nodeId, _action, requestId) => {
    if (!sessionId || !requestId) return
    await api.resolveConfirmation(sessionId, requestId, false, {}, 'user_rejected')
    append({ role: 'agent', type: 'text', content: '已保留原方案，预约和排队资格不会释放。' })
  }, [sessionId, append])

  // ── Send message ──────────────────────────────────────────────────

  const handleSend = useCallback((text) => {
    if (!text.trim() || !sessionId) return
    const shouldStartNextRound = chatPhase === 'completed'
    if (chatPhase === 'completed') {
      setOriginalRequest(text)
      setItineraryMsgId(null)
      if (onItineraryUpdate) onItineraryUpdate([])
      setChatPhase('gathering')
    } else if (!originalRequest) {
      // Save first user message as the original request context
      setOriginalRequest(text)
    }
    append({ role: 'user', type: 'text', content: text })
    runChat(text, {
      ...(shouldStartNextRound ? { phase_hint: 'new_round' } : {}),
      ...(itinerary?.length ? { client_itinerary: itinerary } : {}),
    })
  }, [sessionId, append, runChat, originalRequest, chatPhase, onItineraryUpdate, itinerary])

  // ── Quick reply handler ───────────────────────────────────────────

  const handleQuickReply = useCallback((action) => {
    switch (action) {
      case 'start_plan':
        append({ role: 'user', type: 'text', content: '好，就按这个来规划！' })
        // Pass phase_hint + original_request so backend routes correctly
        // even if session was reset (e.g. dev reload)
        runChat('好，就按这个来规划！', {
          phase_hint: 'start_plan',
          original_request: originalRequest,
        })
        break
      case 'fulfill':
        runFulfill()
        break
      case 'replan':
        append({ role: 'user', type: 'text', content: '重新规划一个方案' })
        runChat('重新规划')
        break
      case 'report':
        append({
          role: 'agent', type: 'report',
          prompt: '遇到什么问题了？选一个：',
        })
        setQuickReplies([])
        break
      case 'dashboard':
        append({ role: 'agent', type: 'text', content: '🎉 今天的行程全部安排好了！祝出行顺利～' })
        setChatPhase('completed')
        setQuickReplies([])
        break
    }
  }, [runFulfill, runChat, append, originalRequest])

  // ── render ────────────────────────────────────────────────────────

  return (
    <div className="chat-page">
      {/* Booking redirect popup overlay */}
      {bookingPopup && (
        <div className="booking-popup-overlay" onClick={() => setBookingPopup(null)}>
          <div className="booking-popup" onClick={e => e.stopPropagation()}>
            <div className="booking-popup-icon">🔗</div>
            <div className="booking-popup-title">正在跳转到 {bookingPopup.site}</div>
            <div className="booking-popup-name">{bookingPopup.name}</div>
            <div className="booking-popup-url">{bookingPopup.mockUrl}</div>
            <div className="booking-popup-tip">此为演示模拟，3秒后自动关闭</div>
            <button className="booking-popup-close" onClick={() => setBookingPopup(null)}>关闭</button>
          </div>
        </div>
      )}
      {/* Header */}
      <div className="chat-header">
        <div className="chat-header-avatar">🤖</div>
        <div className="chat-header-info">
          <div className="chat-header-name">美团本地生活助手</div>
          <div className="chat-header-sub">
            {isStreaming ? (
              <span className="chat-header-typing">正在思考中<span className="dot-anim">...</span></span>
            ) : monitoringActive ? (
              <span className="monitor-active-label">● 监控中</span>
            ) : '在线'}
          </div>
        </div>
        <div className="chat-header-badge">DeepSeek R1</div>
      </div>

      {/* Progress card — below header, only when itinerary exists */}
      {itinerary?.length > 0 && (
        <ProgressCard
          itinerary={itinerary}
          onCheckin={onCheckin}
          taxiStatus={taxiStatus}
        />
      )}

      {/* Messages */}
      <div className="chat-messages">
        {messages.map(msg => (
          <ChatMessage
            key={msg.id}
            msg={msg}
            onNodeAction={handleNodeAction}
            onTransitChange={onTransitChange}
            onExceptionConfirm={confirmException}
            onExceptionDismiss={dismissException}
            onReportSelect={(type, label) => submitReport(type, label)}
            onSoftLockConfirm={handleSoftLockConfirm}
            onSoftLockDismiss={handleSoftLockDismiss}
          />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <ChatInput
        quickReplies={quickReplies}
        onSend={handleSend}
        onQuickReply={handleQuickReply}
        disabled={isStreaming || !sessionId}
      />
    </div>
  )
}


