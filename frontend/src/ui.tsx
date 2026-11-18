// ชิ้นส่วนหน้าจอที่ใช้ซ้ำ
import { useState, type ReactNode } from 'react'

export function Card({ title, action, children, className = '' }: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-2xl bg-white shadow-sm ring-1 ring-slate-200 ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
          <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}

type ButtonProps = {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  disabled?: boolean
  icon?: ReactNode
  type?: 'button' | 'submit'
}

export function Button({ children, onClick, variant = 'secondary', disabled, icon, type = 'button' }: ButtonProps) {
  const styles = {
    primary: 'bg-brand-600 text-white hover:bg-brand-700 shadow-sm',
    secondary: 'bg-white text-ink ring-1 ring-slate-300 hover:bg-slate-50',
    ghost: 'text-slate-600 hover:bg-slate-100',
    danger: 'bg-white text-brand-700 ring-1 ring-brand-100 hover:bg-brand-50',
  }[variant]
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition
        disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}
    >
      {icon}
      {children}
    </button>
  )
}

export function Toggle({ checked, onChange, label, hint, disabled }: {
  checked: boolean
  onChange: (v: boolean) => void
  label: ReactNode
  hint?: ReactNode
  disabled?: boolean
}) {
  return (
    <label className={`flex items-start gap-3 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition ${checked ? 'bg-brand-600' : 'bg-slate-300'}`}
      >
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${checked ? 'left-[22px]' : 'left-0.5'}`} />
      </button>
      <span>
        <span className="block text-sm font-medium text-ink">{label}</span>
        {hint && <span className="block text-xs text-slate-500">{hint}</span>}
      </span>
    </label>
  )
}

export function Field({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full rounded-xl border-0 bg-slate-50 px-3.5 py-2.5 text-sm text-ink ring-1 ring-slate-200 placeholder:text-slate-400 ' +
  'focus:bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none'

export function Stat({ label, value, tone = 'default', sub }: {
  label: string
  value: ReactNode
  tone?: 'default' | 'good' | 'bad' | 'warn'
  sub?: ReactNode
}) {
  const color = { default: 'text-ink', good: 'text-emerald-600', bad: 'text-brand-600', warn: 'text-amber-600' }[tone]
  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200">
      <div className="text-sm text-slate-500">{label}</div>
      <div className={`mt-1 text-3xl font-semibold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export function Badge({ tone, children }: { tone: 'green' | 'red' | 'amber' | 'slate' | 'blue'; children: ReactNode }) {
  const styles = {
    green: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
    red: 'bg-brand-50 text-brand-700 ring-brand-100',
    amber: 'bg-amber-50 text-amber-700 ring-amber-200',
    slate: 'bg-slate-100 text-slate-600 ring-slate-200',
    blue: 'bg-sky-50 text-sky-700 ring-sky-200',
  }[tone]
  return <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${styles}`}>{children}</span>
}

export const fmt = (n: number) => n.toLocaleString('th-TH')

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold">{title}</h1>
        {sub && <p className="text-sm text-slate-500">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

export function Notice({ tone = 'info', children }: { tone?: 'info' | 'ok' | 'warn' | 'err'; children: ReactNode }) {
  const styles = {
    info: 'bg-sky-50 text-sky-800 ring-sky-200',
    ok: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
    warn: 'bg-amber-50 text-amber-800 ring-amber-200',
    err: 'bg-brand-50 text-brand-700 ring-brand-100',
  }[tone]
  return <div className={`rounded-2xl px-5 py-3 text-sm ring-1 ${styles}`}>{children}</div>
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-6" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
          <h2 className="font-semibold">{title}</h2>
          <button className="rounded-lg px-2 text-slate-400 hover:bg-slate-100" onClick={onClose}>✕</button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  )
}

/** ข้อความแจ้งผลชั่วคราว (หายเองใน 4 วินาที) */
export function useFlash() {
  const [flash, setFlash] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const show = (tone: 'ok' | 'err', text: string) => {
    setFlash({ tone, text })
    setTimeout(() => setFlash(null), 4000)
  }
  const node = flash ? <Notice tone={flash.tone}>{flash.text}</Notice> : null
  return { show, node }
}

export const selectClass = inputClass
