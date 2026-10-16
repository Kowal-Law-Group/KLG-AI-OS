import { useEffect, useMemo, useState } from 'react'
import { fetchMatters } from '@/api/client'
import { useUIStore } from '@/store/uiStore'
import { useChatStore } from '@/store/chatStore'
import { useAuthStore } from '@/store/authStore'
import { useMatterStore } from '@/store/matterStore'
import type { Matter } from '@/types'
import styles from './DeadlinesWorkspace.module.css'

type FilterMode = 'active' | 'all' | 'overdue' | 'week' | 'month' | 'mine'

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '—'
  const d = new Date(dateStr + 'T12:00:00')
  if (isNaN(d.getTime())) return dateStr
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function daysLabel(days?: number | null): string {
  if (days == null) return '—'
  if (days === 0) return 'Today'
  if (days < 0) return `${Math.abs(days)}d overdue`
  return `${days}d`
}

function urgencyClass(days?: number | null): string {
  if (days == null) return ''
  if (days <= 0) return styles.overdue
  if (days <= 7) return styles.urgent
  if (days <= 30) return styles.soon
  return ''
}

// Statuses that mean a matter is definitively closed — mirrors the backend denylist.
const INACTIVE_STATUSES = new Set([
  'done', 'closed', 'canceled', 'cancelled', 'archived',
  'complete', 'completed', 'inactive', 'withdrawn', 'settled',
  'rejected', 'dropped', 'lost',
])

function hasDeadline(m: Matter): boolean {
  return !!(m.target_date || m.next_court_deadline)
}

// Normalize Notion status strings to canonical group keys.
// Handles capitalization variants ("On hold", "On Hold", "ON HOLD").
// Empty/null status → 'untagged' so unset matters don't silently join Active.
function statusKey(m: Matter): string {
  const s = (m.status ?? '').trim().toLowerCase()
  if (s.includes('hold')) return 'on hold'
  if (s.includes('active')) return 'active'
  if (s === '') return 'untagged'
  return s
}

function passesFilter(m: Matter, filter: FilterMode, myName: string): boolean {
  if (filter === 'all') return true
  if (filter === 'active') {
    // Allowlist: only show matters with an explicit Active or On Hold status.
    // Matters with no status or non-standard statuses are excluded — they
    // are not shown in Notion's active views either.
    const key = statusKey(m)
    return key === 'active' || key === 'on hold'
  }
  if (filter === 'mine') return (m.assignee ?? '').toLowerCase().includes(myName.toLowerCase())
  const days = m.days_until ?? null
  if (filter === 'overdue') return days != null && days < 0
  if (filter === 'week') return days != null && days >= 0 && days <= 7
  if (filter === 'month') return days != null && days >= 0 && days <= 30
  return true
}

const GROUP_ORDER = ['active', 'on hold']

// Project Status values that place a matter inside Notion's Matter Deadlines view scope.
// Idea, Backlog, Done, and Canceled are excluded — mirrors the backend _ACTIVE_PROJECT_STATUSES set.
const VIEW_PROJECT_STATUSES = new Set(['planning', 'in progress', 'paused', 'waiting'])

function groupByStatus(matters: Matter[]): Array<{ label: string; items: Matter[] }> {
  const map = new Map<string, Matter[]>()
  for (const m of matters) {
    const key = statusKey(m)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(m)
  }
  return [...map.entries()]
    .sort(([a], [b]) => {
      const ai = GROUP_ORDER.indexOf(a)
      const bi = GROUP_ORDER.indexOf(b)
      if (ai !== -1 && bi !== -1) return ai - bi
      if (ai !== -1) return -1
      if (bi !== -1) return 1
      return a.localeCompare(b)
    })
    .map(([key, items]) => ({
      label: key === 'on hold' ? 'On Hold' : key.charAt(0).toUpperCase() + key.slice(1),
      items,
    }))
}

function MatterRow({
  number, matter, onNavigate, onAI,
}: {
  number: number
  matter: Matter
  onNavigate: () => void
  onAI: () => void
}) {
  return (
    <div className={`${styles.matterRow} ${urgencyClass(matter.days_until)}`}>
      <span className={styles.colNum}>{number}</span>

      <span className={`${styles.colName} ${styles.matterName}`}>
        <button className={styles.matterBtn} onClick={onNavigate} title="Open in Matters">
          {matter.name}
        </button>
        {matter.url && (
          <a
            href={matter.url}
            target="_blank"
            rel="noreferrer"
            className={styles.notionLink}
            title="Open in Notion"
            onClick={e => e.stopPropagation()}
          >
            <span className="material-symbols-outlined">open_in_new</span>
          </a>
        )}
      </span>

      <span className={`${styles.colStage} ${styles.cell}`}>{matter.case_stage || '—'}</span>

      <span className={`${styles.colAssignee} ${styles.cell}`}>
        {matter.assignee ? (
          <span className={styles.assigneeChip}>{matter.assignee.split(/[,\s]+/)[0]}</span>
        ) : '—'}
      </span>

      <span className={`${styles.colDeadline} ${styles.cell} ${urgencyClass(matter.days_until)}`}>
        {formatDate(matter.next_court_deadline)}
      </span>

      <span className={`${styles.colTarget} ${styles.cell}`}>{formatDate(matter.target_date)}</span>

      <span className={`${styles.colDays} ${styles.cell}`}>
        {matter.days_until != null ? (
          <span className={`${styles.daysBadge} ${urgencyClass(matter.days_until)}`}>
            {daysLabel(matter.days_until)}
          </span>
        ) : '—'}
      </span>

      <span className={styles.colAi}>
        <button className={styles.sparkBtn} onClick={onAI} title="Ask Alfred about this matter">
          ✨
        </button>
      </span>
    </div>
  )
}

export function DeadlinesWorkspace() {
  const { user } = useAuthStore()
  const { setWorkspace } = useUIStore()
  const { setDraftInput } = useChatStore()
  const { setSelectedMatter } = useMatterStore()

  const [matters, setMatters] = useState<Matter[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<FilterMode>('active')
  const [search, setSearch] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    fetchMatters(true)
      .then(data => {
        if (cancelled) return
        const list = Array.isArray(data) ? data : (data.matters ?? [])
        // Sort by days_until: overdue first, then soonest, then no-date at end
        list.sort((a, b) => {
          const da = a.days_until ?? 9999
          const db = b.days_until ?? 9999
          return da - db
        })
        setMatters(list)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Failed to load matters')
      })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [])

  const myName = user ?? ''

  // Scope to the same 123-row Project Status range as Notion's Matter Deadlines view
  const viewMatters = useMemo(() =>
    matters.filter(m => VIEW_PROJECT_STATUSES.has((m.project_status ?? '').trim().toLowerCase()))
  , [matters])

  const filtered = useMemo(() => {
    let list = viewMatters.filter(m => passesFilter(m, filter, myName))
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      list = list.filter(m => (m.name ?? '').toLowerCase().includes(q))
    }
    return list
  }, [viewMatters, filter, search, myName])

  function handleAI(matter: Matter) {
    setDraftInput(`Alfred, give me a status update on the ${matter.name} matter — what are the key deadlines and what should the team focus on?`)
    setWorkspace('chat')
  }

  if (loading) {
    return (
      <div className={styles.container}>
        <div className={styles.toolbar}>
          <div className={styles.toolbarTitle}>
            <span className="material-symbols-outlined">event_upcoming</span>
            Deadlines
          </div>
        </div>
        <div className={styles.skeletonWrap}>
          {[0, 1, 2, 3, 4, 5, 6].map(i => (
            <div key={i} className={styles.skeletonRow} style={{ width: `${70 + (i % 3) * 10}%` }} />
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className={styles.container}>
        <div className={styles.errorCard}>
          <span className="material-symbols-outlined">error_outline</span>
          <p>{error}</p>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.container}>
      {/* ── Toolbar ─────────────────────────────────────── */}
      <div className={styles.toolbar}>
        <div className={styles.toolbarTitle}>
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>event_upcoming</span>
          Deadlines
          <span className={styles.taskBadge}>{filtered.length}</span>
        </div>

        <div className={styles.toolbarControls}>
          <select
            className={styles.select}
            value={filter}
            onChange={e => setFilter(e.target.value as FilterMode)}
          >
            <option value="active">Active matters</option>
            <option value="all">All matters</option>
            <option value="overdue">Overdue</option>
            <option value="week">Due this week</option>
            <option value="month">Due this month</option>
            <option value="mine">Assigned to me</option>
          </select>

          <div className={styles.searchWrap}>
            <span className="material-symbols-outlined" style={{ fontSize: 14, color: 'var(--text-muted)' }}>search</span>
            <input
              className={styles.searchInput}
              placeholder="Search matters…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
        </div>
      </div>

      {/* ── Column headers ───────────────────────────────── */}
      <div className={styles.colHeaders}>
        <span className={styles.colNum}>#</span>
        <span className={styles.colName}>MATTER</span>
        <span className={styles.colStage}>STAGE</span>
        <span className={styles.colAssignee}>ASSIGNEE</span>
        <span className={styles.colDeadline}>COURT DEADLINE</span>
        <span className={styles.colTarget}>TARGET DATE</span>
        <span className={styles.colDays}>DAYS</span>
        <span className={styles.colAi} />
      </div>

      {/* ── Table body ───────────────────────────────────── */}
      <div className={styles.tableBody}>
        {filtered.length === 0 ? (
          <div className={styles.emptyState}>
            <span className="material-symbols-outlined">event_available</span>
            <p>No matters match the current filters.</p>
          </div>
        ) : filter === 'active' ? (
          groupByStatus(filtered).map(({ label, items }) => (
            <div key={label}>
              <div className={styles.groupHeader}>
                <span className={styles.groupChevron}>▼</span>
                <span className={styles.groupLabel}>{label}</span>
                <span className={styles.groupCount}>{items.length}</span>
              </div>
              {items.map((matter, i) => (
                <MatterRow key={matter.id} number={i + 1} matter={matter}
                  onNavigate={() => { setSelectedMatter(matter); setWorkspace('matters') }}
                  onAI={() => handleAI(matter)} />
              ))}
            </div>
          ))
        ) : (
          // Flat numbered list for all other filters
          filtered.map((matter, i) => (
            <MatterRow key={matter.id} number={i + 1} matter={matter}
              onNavigate={() => { setSelectedMatter(matter); setWorkspace('matters') }}
              onAI={() => handleAI(matter)} />
          ))
        )}
      </div>
    </div>
  )
}
