import React, { useEffect, useRef, useState } from 'react'

const SCENARIO_MAP = {
  family:  { icon: '👨‍👩‍👧', label: '家庭出行' },
  friends: { icon: '👯', label: '朋友出行' },
  couple:  { icon: '💑', label: '情侣出行' },
  solo:    { icon: '🚶', label: '独自出游' },
}

const COMPANION_MAP = {
  spouse:  '配偶', child:   '孩子', parents: '父母',
  friends: '朋友', elderly: '老人', baby:    '婴幼儿',
  partner: '另一半', family: '家人',
}

const STYLE_MAP = {
  relaxed:  { icon: '😌', label: '轻松悠闲' },
  active:   { icon: '⚡', label: '活力体验' },
  cultural: { icon: '🏛️', label: '文化探索' },
  foodie:   { icon: '🍜', label: '美食打卡' },
}

const DISTANCE_MAP = { nearby: '附近优先', any: '不限距离' }

const CHILD_PURPOSE_MAP = {
  education: { icon: '🔬', label: '科普学习' },
  fun:       { icon: '🎠', label: '轻松好玩' },
}

const FRIENDS_TYPE_MAP = {
  social:    { icon: '🎭', label: '社交互动（剧本杀/密室）' },
  exhibition:{ icon: '🏛️', label: '文化展览（博物馆/美术馆）' },
  mall:      { icon: '🛍️', label: '逛街购物（商场/步行街）' },
  photo_spot:{ icon: '📸', label: '出片打卡（网红/文艺）' },
  mixed:     { icon: '🎲', label: '综合体验' },
}

const VENUE_MAP = {
  mall:    { icon: '🏬', label: '商场/室内购物中心' },
  indoor:  { icon: '🏠', label: '室内场所优先' },
  outdoor: { icon: '🌳', label: '户外/公园' },
}

const ACTIVITY_PREF_MAP = {
  park:       { icon: '🌳', label: '逛公园/户外' },
  mall:       { icon: '🏬', label: '逛商场/购物中心' },
  exhibition: { icon: '🏛️', label: '展览/博物馆/美术馆' },
  citywalk:   { icon: '🚶', label: 'Citywalk/街区' },
  social:     { icon: '🎲', label: '剧本杀/密室/桌游' },
  playground: { icon: '🎠', label: '亲子乐园/游乐园' },
}

function InfoRow({ icon, label, value, pending, highlight }) {
  return (
    <div className={`up-row ${highlight ? 'up-row-highlight' : ''}`}>
      <span className="up-row-icon">{icon}</span>
      <span className="up-row-label">{label}</span>
      <span className={`up-row-value ${pending ? 'up-pending' : ''}`}>
        {pending ? '···' : value}
      </span>
    </div>
  )
}

function BoolRow({ icon, label, value, trueText, falseText, highlight }) {
  if (value === null || value === undefined) {
    return <InfoRow icon={icon} label={label} pending highlight={highlight} />
  }
  return (
    <InfoRow icon={icon} label={label}
      value={value ? (trueText || '是') : (falseText || '否')}
      highlight={highlight} />
  )
}

function TagList({ tags }) {
  if (!tags || tags.length === 0) return null
  return (
    <div className="up-tags">
      {tags.map((t, i) => <span key={i} className="up-tag">{t}</span>)}
    </div>
  )
}

function Pulse() {
  return <span className="up-pulse" />
}

function CompletionBar({ pct }) {
  const color = pct >= 80 ? 'var(--success)' : pct >= 50 ? 'var(--warning)' : 'var(--primary)'
  return (
    <div className="up-bar-wrap">
      <div className="up-bar-fill" style={{ width: `${pct}%`, background: color }} />
    </div>
  )
}

function useChangedKeys(facts) {
  const prev = useRef({})
  const [changed, setChanged] = useState(new Set())

  useEffect(() => {
    if (!facts) return
    const newChanged = new Set()
    for (const k of Object.keys(facts)) {
      if (JSON.stringify(facts[k]) !== JSON.stringify(prev.current[k])) {
        newChanged.add(k)
      }
    }
    if (newChanged.size > 0) {
      setChanged(newChanged)
      const t = setTimeout(() => setChanged(new Set()), 1800)
      prev.current = { ...facts }
      return () => clearTimeout(t)
    }
    prev.current = { ...facts }
  }, [facts])

  return changed
}

export default function UserProfilePanel({ facts, preferences, phase }) {
  const changed = useChangedKeys(facts)
  const f = facts || {}
  const p = preferences || {}

  const scenario    = SCENARIO_MAP[f.scenario]
  const style       = STYLE_MAP[f.travel_style]
  const companions  = (f.companions || []).map(c => COMPANION_MAP[c] || c).filter(Boolean)
  const childPurpose   = CHILD_PURPOSE_MAP[f.child_purpose]
  const friendsType    = FRIENDS_TYPE_MAP[f.friends_activity_type]
  const foodTags       = (p.food?.length > 0 ? p.food : f.food_preferences) || []
  const avoidTags      = p.avoid  || []
  const specialNeeds   = f.special_needs || []
  const venuePref      = VENUE_MAP[p.venue || f.venue_preference] || null
  const activityPref   = ACTIVITY_PREF_MAP[f.activity_preference] || (f.activity_preference_label ? { icon: '🎯', label: f.activity_preference_label } : null)
  const hasDemands     = foodTags.length > 0 || venuePref || activityPref || specialNeeds.length > 0
  const hasFemale      = (f.female_count || 0) > 0
  const isAllMale      = f.group_gender === 'all_male'
  const isFriends      = f.scenario === 'friends'
  const hasChildren    = f.has_children || f.has_child
  const hasElderly     = f.has_elderly

  // Completeness: base fields + conditional fields
  const baseFields = ['scenario', 'start_time', 'duration_hours', 'companions', 'home_area']
  const conditionalFields = []
  if (hasChildren) {
    conditionalFields.push('child_age', 'child_purpose')
  }
  if (hasElderly) {
    conditionalFields.push('elderly_no_walking')
  }
  if (isFriends) {
    conditionalFields.push('friends_activity_type', 'group_gender')
  }
  if (hasFemale) {
    conditionalFields.push('female_prefer_low_intensity', 'female_prefer_indoor')
  }
  if (isAllMale) {
    conditionalFields.push('male_prefer_high_intensity')
  }

  const allFields = [...baseFields, ...conditionalFields]
  const filled = allFields.filter(k => {
    const v = f[k]
    if (v === null || v === undefined) return false
    if (Array.isArray(v)) return v.length > 0
    return true
  }).length
  const pct = allFields.length > 0 ? Math.round((filled / allFields.length) * 100) : 0

  const phaseLabel = {
    gathering:  '收集信息中',
    confirming: '确认需求中',
    planning:   '规划中',
    monitoring: '行程监控中',
  }[phase] || '等待输入'

  const phaseColor = {
    gathering:  'var(--warning)',
    confirming: 'var(--primary)',
    planning:   '#722ED1',
    monitoring: 'var(--success)',
  }[phase] || 'var(--gray-3)'

  const c = (key) => changed.has(key)

  return (
    <div className="up-panel">
      {/* Header */}
      <div className="up-header">
        <div className="up-title">
          <span className="up-title-icon">👤</span>
          用户画像
        </div>
        <div className="up-live">
          <Pulse />
          <span className="up-live-text">实时更新</span>
        </div>
      </div>

      {/* Phase */}
      <div className="up-phase" style={{ borderColor: phaseColor, color: phaseColor }}>
        <span className="up-phase-dot" style={{ background: phaseColor }} />
        {phaseLabel}
      </div>

      {/* Completeness */}
      <div className="up-section">
        <div className="up-section-title">信息完整度</div>
        <CompletionBar pct={pct} />
        <div className="up-bar-label">{pct}% · {filled}/{allFields.length} 字段已获取</div>
      </div>

      {/* Scenario */}
      <div className="up-section">
        <div className="up-section-title">🎭 出行场景</div>
        {scenario ? (
          <div className={`up-scenario-card ${c('scenario') ? 'up-highlight' : ''}`}>
            <span className="up-scenario-icon">{scenario.icon}</span>
            <span className="up-scenario-label">{scenario.label}</span>
          </div>
        ) : (
          <div className="up-scenario-card up-empty">
            <span className="up-scenario-icon">❓</span>
            <span className="up-scenario-label up-pending">待了解</span>
          </div>
        )}
      </div>

      {/* User explicit demands — shown prominently when present */}
      {hasDemands && (
        <div className="up-section up-demands-section">
          <div className="up-section-title">📌 用户具体需求</div>
          {venuePref && (
            <div className={`up-demand-chip ${c('venue_preference') ? 'up-highlight' : ''}`}>
              <span>{venuePref.icon}</span>
              <span>{venuePref.label}</span>
            </div>
          )}
          {activityPref && (
            <div className={`up-demand-chip ${c('activity_preference') ? 'up-highlight' : ''}`}>
              <span>{activityPref.icon}</span>
              <span>{activityPref.label}</span>
            </div>
          )}
          {foodTags.length > 0 && (
            <div className="up-demand-row">
              <span className="up-demand-icon">🍽️</span>
              <div className="up-demand-tags">
                {foodTags.map((t, i) => (
                  <span key={i} className="up-demand-tag food">{t}</span>
                ))}
              </div>
            </div>
          )}
          {specialNeeds.length > 0 && (
            <div className="up-demand-row">
              <span className="up-demand-icon">⚠️</span>
              <div className="up-demand-tags">
                {specialNeeds.map((t, i) => (
                  <span key={i} className="up-demand-tag special">{t}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Basic info */}
      <div className="up-section">
        <div className="up-section-title">📋 基础信息</div>
        <div className="up-rows">
          <InfoRow icon="⏰" label="出发时间"
            value={f.start_time} pending={!f.start_time} highlight={c('start_time')} />
          <InfoRow icon="⏱️" label="游玩时长"
            value={f.duration_hours ? `${f.duration_hours} 小时` : null}
            pending={!f.duration_hours} highlight={c('duration_hours')} />
          <InfoRow icon="📍" label="出发地点"
            value={f.home_area} pending={!f.home_area} highlight={c('home_area')} />
        </div>
      </div>

      {/* Companions */}
      <div className="up-section">
        <div className="up-section-title">👥 同行人</div>
        {companions.length > 0 ? (
          <TagList tags={companions} />
        ) : (
          <div className="up-empty-hint">待了解···</div>
        )}
        {/* Gender breakdown */}
        {(f.male_count > 0 || f.female_count > 0) && (
          <div className="up-gender-row">
            {f.male_count > 0 && <span className="up-gender-tag male">👨 男生 {f.male_count}人</span>}
            {f.female_count > 0 && <span className="up-gender-tag female">👩 女生 {f.female_count}人</span>}
          </div>
        )}
      </div>

      {/* Children section */}
      {hasChildren && (
        <div className="up-section">
          <div className="up-section-title">🧒 孩子信息</div>
          <div className="up-rows">
            <InfoRow icon="🎂" label="孩子年龄"
              value={f.child_age ? `${f.child_age} 岁` : null}
              pending={!f.child_age} highlight={c('child_age')} />
          </div>
          {childPurpose ? (
            <div className={`up-badge-card ${c('child_purpose') ? 'up-highlight' : ''}`}>
              <span>{childPurpose.icon}</span>
              <span>出行目的：{childPurpose.label}</span>
            </div>
          ) : (
            <div className="up-empty-hint">出行目的待确认···</div>
          )}
          {f.child_age && (() => {
            const ca = f.child_age
            let msg = null
            if (ca < 5)       msg = `⚠️ ${ca}岁：禁止密室/剧本杀·酒吧`
            else if (ca < 14) msg = `⚠️ ${ca}岁：仅限非恐密室/剧本杀，禁止恐怖主题·酒吧`
            else if (ca < 18) msg = `⚠️ ${ca}岁（未成年）：禁止酒吧`
            return msg ? <div className="up-notice">{msg}</div> : null
          })()}
        </div>
      )}

      {/* Elderly section */}
      {hasElderly && (
        <div className="up-section">
          <div className="up-section-title">🧓 老人出行</div>
          <div className="up-rows">
            <BoolRow icon="🦯" label="避免步行"
              value={f.elderly_no_walking}
              trueText="是，需交通接驳" falseText="步行无碍"
              highlight={c('elderly_no_walking')} />
          </div>
        </div>
      )}

      {/* Friends activity type */}
      {isFriends && (
        <div className="up-section">
          <div className="up-section-title">🎉 活动偏好</div>
          {friendsType ? (
            <div className={`up-badge-card ${c('friends_activity_type') ? 'up-highlight' : ''}`}>
              <span>{friendsType.icon}</span>
              <span>{friendsType.label}</span>
            </div>
          ) : (
            <div className="up-empty-hint">活动类型待确认···</div>
          )}
        </div>
      )}

      {/* Female preferences */}
      {hasFemale && (
        <div className="up-section">
          <div className="up-section-title">👩 女生偏好</div>
          <div className="up-rows">
            <BoolRow icon="🥗" label="需减脂/健康餐"
              value={f.female_weight_loss}
              trueText="是" falseText="无特殊"
              highlight={c('female_weight_loss')} />
            <BoolRow icon="🧘" label="低体力活动"
              value={f.female_prefer_low_intensity}
              trueText="偏好" falseText="都可以"
              highlight={c('female_prefer_low_intensity')} />
            <BoolRow icon="🏠" label="室内优先"
              value={f.female_prefer_indoor}
              trueText="倾向室内" falseText="室内外皆可"
              highlight={c('female_prefer_indoor')} />
          </div>
        </div>
      )}

      {/* Male preferences */}
      {isAllMale && (
        <div className="up-section">
          <div className="up-section-title">💪 男生偏好</div>
          <div className="up-rows">
            <BoolRow icon="⛰️" label="高强度运动"
              value={f.male_prefer_high_intensity}
              trueText="喜欢（爬山/运动）" falseText="普通活动即可"
              highlight={c('male_prefer_high_intensity')} />
          </div>
        </div>
      )}

      {/* 18+ legal check */}
      {f.all_adults_confirmed !== undefined && f.all_adults_confirmed !== null && (
        <div className="up-section">
          <div className="up-section-title">⚖️ 法律确认</div>
          <div className={`up-badge-card ${f.all_adults_confirmed ? '' : 'up-badge-warn'}`}>
            <span>{f.all_adults_confirmed ? '✅' : '❌'}</span>
            <span>{f.all_adults_confirmed ? '全员已满18岁' : '含未成年人，已排除饮酒场所'}</span>
          </div>
        </div>
      )}

      {/* Travel style & preferences */}
      <div className="up-section">
        <div className="up-section-title">🎨 出行风格</div>
        {style ? (
          <div className={`up-badge-card ${c('travel_style') ? 'up-highlight' : ''}`}>
            <span>{style.icon}</span>
            <span>{style.label}</span>
            {p.distance && <span className="up-badge-extra">{DISTANCE_MAP[p.distance] || p.distance}</span>}
          </div>
        ) : (
          <div className="up-empty-hint">待了解···</div>
        )}
        {avoidTags.length > 0 && (
          <>
            <div className="up-sub-title">避开事项</div>
            <TagList tags={avoidTags.map(t => `❌ ${t}`)} />
          </>
        )}
        {p.skip_restaurant && (
          <div className="up-notice">🍱 自行解决餐食，不安排餐厅</div>
        )}
      </div>

      <div className="up-footer">
        AI 正在通过对话实时构建用户画像
      </div>
    </div>
  )
}

