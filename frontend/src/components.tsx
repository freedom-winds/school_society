import { useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { Modal, message } from 'antd'
import { motion } from 'framer-motion'
import { useAuthStore } from './stores/auth'
import type { Category, Club, Honor, Position, Revision } from './types'

export const statusLabel: Record<string, string> = { DRAFT: '草稿', PENDING_REVIEW: '待审核', PUBLISHED: '已发布', HIDDEN: '已隐藏', ARCHIVED: '已归档', DELETED: '已删除', MODIFICATION_PENDING: '修改审核中', PENDING: '待审核', APPROVED: '已通过', REJECTED: '已驳回', WITHDRAWN: '已撤回', SUPERSEDED: '历史版本', ACTIVE: '正常', DISABLED: '已禁用' }

export function AppHeader() {
  const { user, clear } = useAuthStore(); const nav = useNavigate()
  return <header className="site-header"><Link className="brand" to="/"><span className="brand-mark">S</span><span>上海中学学生社团</span></Link><nav><NavLink to="/clubs">全部社团</NavLink>{user && <NavLink to="/registration-status">审核查询</NavLink>}{user && <NavLink to={user.role === 'ADMIN' ? '/admin' : '/dashboard'}>管理后台</NavLink>}</nav><div className="header-account">{user ? <><span className="account-name">{user.display_name}</span><button className="button text" onClick={() => { clear(); nav('/') }}>退出</button></> : <><Link className="button text" to="/login">登录</Link><Link className="button primary small" to="/register">注册账号</Link></>}</div></header>
}

export function AppFooter() { return <footer className="site-footer"><div><b>校园社团中心</b><span>让每一份热爱被清晰看见</span></div><span>校园社团信息管理平台 · V1.0</span></footer> }

export function PageContainer({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) { return <main className={`page-container ${wide ? 'wide' : ''}`}>{children}</main> }
export function SectionHeader({ eyebrow, title, action }: { eyebrow?: string; title: string; action?: React.ReactNode }) { return <div className="section-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2>{title}</h2></div>{action}</div> }
export function StatusBadge({ status }: { status: string }) { const tone = status.includes('PENDING') ? 'warning' : status.includes('REJECT') || status === 'DISABLED' ? 'danger' : status.includes('PUBLISH') || status === 'APPROVED' || status === 'ACTIVE' ? 'success' : 'neutral'; return <span className={`status ${tone}`}>{statusLabel[status] || status}</span> }
export function EmptyState({ title = '暂时没有内容', detail = '换个条件看看，或稍后再来。' }: { title?: string; detail?: string }) { return <div className="empty"><span>—</span><strong>{title}</strong><p>{detail}</p></div> }
export function LoadingButton({ loading = false, loadingText = '处理中…', children, className = '', disabled, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean; loadingText?: ReactNode }) { return <button {...props} className={`${className} ${loading ? 'is-loading' : ''}`.trim()} disabled={disabled || loading} aria-busy={loading}>{loading && <span className="button-spinner" aria-hidden="true" />}{loading ? loadingText : children}</button> }

export function CategoryPill({ category, active, onClick }: { category: Category; active?: boolean; onClick?: () => void }) { return <button className={`category-pill ${active ? 'active' : ''}`} onClick={onClick}><i>{category.icon || '·'}</i>{category.name}</button> }
export function CategoryFilter({ categories, value, onChange }: { categories: Category[]; value?: number; onChange: (value?: number) => void }) { return <div className="category-filter"><button className={!value ? 'active' : ''} onClick={() => onChange(undefined)}>全部</button>{categories.map(c => <button key={c.id} className={value === c.id ? 'active' : ''} onClick={() => onChange(c.id)}>{c.name}</button>)}</div> }
export function ClubSearchBar({ value, onChange, placeholder = '搜索社团名称、简介或招新语' }: { value: string; onChange: (value: string) => void; placeholder?: string }) { return <label className="search-bar"><span>⌕</span><input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} /><kbd>↵</kbd></label> }

export function ClubCard({ club }: { club: Club }) { const revision = club.current_revision; return <motion.article className="club-card" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .18 }}><div className="club-card-top">{club.icon_url ? <img src={club.icon_url} alt="" /> : <span className="club-initial">{(club.name || revision?.name || '社').slice(0, 1)}</span>}<span className="club-category">{club.category?.name || revision?.category?.name || '未分类'}</span></div><div className="club-card-content"><h3>{club.name || revision?.name}</h3><p className="intro">{club.short_intro || revision?.short_intro}</p><p className="slogan">“{club.recruitment_slogan || revision?.recruitment_slogan}”</p><Link to={`/clubs/${club.slug}`} className="inline-link">查看社团档案 <b>→</b></Link></div></motion.article> }
export function ClubGrid({ clubs }: { clubs: Club[] }) { return <div className="club-grid">{clubs.map(club => <ClubCard key={club.id} club={club} />)}</div> }

export function ClubHero({ club }: { club: Club }) { const r = club.current_revision!; return <section className="club-hero"><div className="club-symbol">{r.icon_url ? <img src={r.icon_url} alt="" /> : r.name.slice(0, 1)}</div><div><span className="category-tag">{r.category?.name}</span><h1>{r.name}</h1><p className="hero-slogan">{r.recruitment_slogan}</p><p>{r.short_intro}</p></div></section> }
export function ClubMetaPanel({ revision, positions }: { revision: Revision; positions?: Position[] }) { return <aside className="club-meta"><h3>社团信息</h3>{revision.advisor && <Meta label="指导老师" value={revision.advisor} />}{revision.activity_time && <Meta label="活动时间" value={revision.activity_time} />}{revision.activity_location && <Meta label="活动地点" value={revision.activity_location} />}{positions && positions.length > 0 && <div className="meta-row"><span>负责人</span><div>{positions.map(p => <p key={p.id}>{p.user.display_name}<em>{p.position === 'PRESIDENT' ? '社长' : '副社长'}</em></p>)}</div></div>}</aside> }
function Meta({ label, value }: { label: string; value: string }) { return <div className="meta-row"><span>{label}</span><b>{value}</b></div> }
export function HonorTimeline({ honors }: { honors: Honor[] }) { if (!honors.length) return null; return <section className="honor-timeline"><h2>社团荣誉</h2>{honors.map((h, i) => <div className="honor" key={`${h.title}-${i}`}><i></i><div><b>{h.title}</b><span>{[h.year, h.level].filter(Boolean).join(' · ')}</span>{h.description && <p>{h.description}</p>}</div></div>)}</section> }
export function MarkdownContent({ value }: { value: string }) {
  const inline = (text: string) => text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]*\))/g).filter(Boolean).map((piece, index) => {
    if (piece.startsWith('**')) return <strong key={index}>{piece.slice(2, -2)}</strong>
    if (piece.startsWith('*')) return <em key={index}>{piece.slice(1, -1)}</em>
    const match = piece.match(/^\[([^\]]+)\]\(([^)]*)\)$/)
    if (match && /^https?:\/\//.test(match[2])) return <a key={index} href={match[2]} target="_blank" rel="noreferrer">{match[1]}</a>
    return piece
  })
  return <div className="markdown-content">{value.replace(/<[^>]*>/g, '').split('\n').map((line, index) => {
    if (line.startsWith('### ')) return <h4 key={index}>{inline(line.slice(4))}</h4>
    if (line.startsWith('## ')) return <h3 key={index}>{inline(line.slice(3))}</h3>
    if (line.startsWith('# ')) return <h2 key={index}>{inline(line.slice(2))}</h2>
    if (line.startsWith('> ')) return <blockquote key={index}>{inline(line.slice(2))}</blockquote>
    if (/^[-*] /.test(line)) return <li key={index}>{inline(line.slice(2))}</li>
    return line.trim() ? <p key={index}>{inline(line)}</p> : <br key={index} />
  })}</div>
}

export function FormSection({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) { return <section className="form-section"><div className="form-section-title"><h3>{title}</h3>{description && <p>{description}</p>}</div>{children}</section> }
export function StickyActionBar({ children }: { children: React.ReactNode }) { return <div className="sticky-actions">{children}</div> }
export function ConfirmDialog({ title, content, onConfirm, danger = false, children }: { title: string; content: string; onConfirm: () => Promise<void> | void; danger?: boolean; children: React.ReactNode }) { const [open, setOpen] = useState(false); const [submitting, setSubmitting] = useState(false); return <><span onClick={() => !submitting && setOpen(true)}>{children}</span><Modal title={title} open={open} onCancel={() => !submitting && setOpen(false)} onOk={async () => { setSubmitting(true); try { await onConfirm(); setOpen(false); message.success('操作已完成') } finally { setSubmitting(false) } }} okButtonProps={{ danger, loading: submitting }} cancelButtonProps={{ disabled: submitting }}>{content}</Modal></> }

export function RevisionDiffViewer({ pending, current }: { pending: Revision; current?: Revision | null }) { const fields: Array<[keyof Revision, string]> = [['name', '社团名称'], ['short_intro', '短简介'], ['recruitment_slogan', '招新语'], ['full_intro', '完整介绍'], ['advisor', '指导老师'], ['activity_time', '活动时间'], ['activity_location', '活动地点']]; const comparing = !!current; return <div className={`revision-diff ${comparing ? '' : 'read-only'}`}>{fields.map(([key, label]) => <div className={comparing && pending[key] !== current?.[key] ? 'changed' : ''} key={key}><span>{label}</span><div>{comparing && <p>{String(current?.[key] || '—')}</p>}<b>{String(pending[key] || '—')}</b></div></div>)}</div> }

export function UserSearchSelect({ onSelect, label = '搜索用户' }: { onSelect: (id: number) => void; label?: string }) { const [keyword, setKeyword] = useState(''); const [users, setUsers] = useState<Array<{ id: number; display_name: string; username: string; role: string }>>([]); const [searching, setSearching] = useState(false); const search = async () => { setSearching(true); try { const { get } = await import('./api/client'); setUsers(await get(`/users/search?keyword=${encodeURIComponent(keyword)}`)) } catch { message.error('搜索失败，请稍后重试') } finally { setSearching(false) } }; return <div className="user-search"><label>{label}</label><div><input value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="输入姓名或用户名" /><LoadingButton className="button secondary small" loading={searching} loadingText="搜索中" onClick={search}>搜索</LoadingButton></div>{users.length > 0 && <ul>{users.map(u => <li key={u.id}><span><b>{u.display_name}</b><small>@{u.username} · {u.role}</small></span><button className="button text small" onClick={() => { onSelect(u.id); setUsers([]); setKeyword(u.display_name) }}>选择</button></li>)}</ul>}</div> }

export function DashboardShell({ children }: { children: React.ReactNode }) { const { user } = useAuthStore(); const items = user?.role === 'ADMIN' ? [['/admin', '概览'], ['/admin/users', '用户管理'], ['/admin/club-reviews', '社团审核'], ['/admin/clubs', '社团管理'], ['/admin/categories', '类别管理'], ['/admin/audit-logs', '操作日志']] : [['/dashboard', '工作台'], ['/dashboard/clubs', '我的社团'], ['/dashboard/clubs/new', '创建社团'], ['/dashboard/invitations', '职务邀请'], ['/dashboard/transfers', '社长交接'], ['/dashboard/account', '账号设置']]; return <div className="dashboard-shell"><aside className="side-nav"><Link className="side-brand" to="/">校园社团中心</Link><nav>{items.map(([to, text]) => <NavLink key={to} to={to} end={to === '/dashboard' || to === '/admin'}>{text}</NavLink>)}</nav><div className="side-user"><b>{user?.display_name}</b><span>{user?.role === 'ADMIN' ? '管理员' : '账号中心'}</span></div></aside><section className="dashboard-main"><div className="dashboard-topbar"><span>管理后台</span><Link to="/">查看公开网站 ↗</Link></div>{children}</section></div> }
