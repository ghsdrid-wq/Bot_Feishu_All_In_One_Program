import { useCallback, useEffect, useState } from 'react'
import {
  Bot, CalendarCheck, Crown, FileSpreadsheet, Loader2, Menu, Monitor, Moon, Radio, Settings as SettingsIcon,
  ShieldCheck, Sun, Table2, WifiOff, X,
} from 'lucide-react'
import { get, type EngineState } from './api'
import { useTheme } from './theme'
import { Report } from './pages/Report'
import { Workbooks } from './pages/Workbooks'
import { DwsPlan } from './pages/DwsPlan'
import { JmsUser } from './pages/JmsUser'
import { Policy } from './pages/Policy'
import { Daily } from './pages/Daily'
import { Vip } from './pages/Vip'
import { Settings } from './pages/Settings'

const pages = [
  { key: 'report', label: 'รายงานรายชั่วโมง', icon: Table2 },
  { key: 'workbooks', label: 'ไฟล์ Excel', icon: FileSpreadsheet },
  { key: 'daily', label: 'สรุปรายวัน', icon: CalendarCheck },
  { key: 'vip', label: 'VIP AWB', icon: Crown },
  { key: 'dws', label: 'DWS PLAN', icon: Radio },
  { key: 'jms', label: 'JMS USER', icon: Bot },
  { key: 'policy', label: 'สิทธิ์รหัส', icon: ShieldCheck },
  { key: 'settings', label: 'ตั้งค่า', icon: SettingsIcon },
] as const
export type PageKey = (typeof pages)[number]['key']
export type Go = (key: PageKey) => void

const themeInfo = {
  auto: { icon: Monitor, label: 'ธีม: ตามเครื่อง' },
  light: { icon: Sun, label: 'ธีม: สว่าง' },
  dark: { icon: Moon, label: 'ธีม: มืด' },
}

function statusDot(st: EngineState | null) {
  return st?.running ? 'animate-pulse bg-amber-500' : st?.scheduler_running ? 'bg-sky-500' : 'bg-emerald-500'
}

export default function App() {
  const [page, setPage] = useState<PageKey>(() => {
    try {
      const saved = localStorage.getItem('page') as PageKey | null
      return saved && pages.some((p) => p.key === saved) ? saved : 'report'
    } catch {
      return 'report'
    }
  })
  const [st, setSt] = useState<EngineState | null>(null)
  const [offline, setOffline] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const theme = useTheme()
  const ThemeIcon = themeInfo[theme.mode].icon

  const refresh = useCallback(async () => {
    try {
      setSt(await get<EngineState>('/api/engine'))
      setOffline(false)
    } catch {
      setOffline(true)
    }
  }, [])
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 2000)
    return () => clearInterval(t)
  }, [refresh])

  const go: Go = (key) => {
    setPage(key)
    setMenuOpen(false)
    document.getElementById('main')?.scrollTo({ top: 0 })
    try {
      localStorage.setItem('page', key)
    } catch {
      /* ไม่มี storage ก็ใช้ได้ */
    }
  }

  const busy: Partial<Record<PageKey, boolean>> = {
    report: st?.running, daily: st?.ds_busy, vip: st?.vip_busy,
  }
  const online: Partial<Record<PageKey, boolean | undefined>> = { dws: st?.bot_running, jms: st?.jms_running }
  const current = pages.find((p) => p.key === page)!

  const themeButton = (
    <button onClick={theme.next} title={themeInfo[theme.mode].label} aria-label={themeInfo[theme.mode].label}
      className="flex items-center gap-2 rounded-xl p-2 text-slate-500 transition hover:bg-slate-100 hover:text-ink">
      <ThemeIcon size={18} />
    </button>
  )

  return (
    <div className="flex h-full">
      {/* มือถือ: พื้นมืดหลังเมนู */}
      {menuOpen && <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setMenuOpen(false)} />}

      {/* เมนูซ้าย: จอใหญ่แสดงตลอด, มือถือเลื่อนออกมาเมื่อกด ☰ */}
      <aside className={`fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] shrink-0 flex-col bg-surface ring-1 ring-slate-200
        transition-transform duration-200 md:static md:z-auto md:w-60 md:translate-x-0
        ${menuOpen ? 'translate-x-0 shadow-xl' : '-translate-x-full'}`}>
        <div className="flex items-start justify-between px-5 pt-6 pb-5">
          <div>
            <div className="text-lg font-bold text-brand-600">Auto Report</div>
            <div className="text-xs text-slate-500">KKN_GW · v{st?.version ?? '–'}</div>
          </div>
          <div className="flex items-center gap-1">
            <span className="hidden md:block">{themeButton}</span>
            <button className="rounded-xl p-2 text-slate-500 hover:bg-slate-100 md:hidden" aria-label="ปิดเมนู"
              onClick={() => setMenuOpen(false)}><X size={18} /></button>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3">
          {pages.map(({ key, label, icon: Icon }) => (
            <button key={key} onClick={() => go(key)}
              className={`flex w-full items-center gap-3 rounded-xl px-3 py-3 text-sm font-medium transition md:py-2.5 ${
                page === key ? 'bg-brand-50 text-brand-700' : 'text-slate-600 hover:bg-slate-100'}`}>
              <Icon size={18} />
              <span className="flex-1 text-left">{label}</span>
              {busy[key] && <Loader2 size={15} className="animate-spin text-brand-600" />}
              {online[key] !== undefined && (
                <span title={online[key] ? 'ONLINE' : 'OFFLINE'} className={`h-2 w-2 rounded-full ${online[key] ? 'bg-emerald-500' : 'bg-slate-300'}`} />
              )}
            </button>
          ))}
        </nav>
        <div className="m-3 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 ring-1 ring-slate-200">
          {offline ? (
            <span className="flex items-center gap-2 text-brand-700"><WifiOff size={14} /> ติดต่อโปรแกรมไม่ได้</span>
          ) : (
            <>
              <div className="flex items-center gap-2 font-medium">
                <span className={`h-2 w-2 rounded-full ${statusDot(st)}`} />
                {st?.status ?? '…'}
              </div>
              <div className="mt-1 text-slate-500">
                {st?.scheduler_running ? `Auto · รอบถัดไป ${st.next_run?.slice(11) ?? '–'}` : 'Auto ปิดอยู่'}
              </div>
            </>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* แถบบนของมือถือ */}
        <header className="sticky top-0 z-30 flex items-center gap-2 bg-surface px-2 py-2 ring-1 ring-slate-200 md:hidden">
          <button className="rounded-xl p-2 text-slate-600 hover:bg-slate-100" aria-label="เมนู" onClick={() => setMenuOpen(true)}>
            <Menu size={22} />
          </button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{current.label}</div>
            <div className="flex items-center gap-1.5 truncate text-[11px] text-slate-500">
              {offline ? (
                <span className="text-brand-700">ติดต่อโปรแกรมไม่ได้</span>
              ) : (
                <>
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDot(st)}`} />
                  {st?.status ?? '…'}
                  {st?.scheduler_running && ` · ถัดไป ${st.next_run?.slice(11) ?? '–'}`}
                </>
              )}
            </div>
          </div>
          {themeButton}
        </header>

        <main id="main" className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl space-y-4 px-3 py-4 sm:space-y-6 sm:px-6 sm:py-6 lg:px-8 lg:py-7">
            {page === 'report' && <Report st={st} refresh={refresh} go={go} />}
            {page === 'workbooks' && <Workbooks />}
            {page === 'daily' && <Daily st={st} />}
            {page === 'vip' && <Vip st={st} go={go} />}
            {page === 'dws' && <DwsPlan st={st} refresh={refresh} />}
            {page === 'jms' && <JmsUser st={st} refresh={refresh} go={go} />}
            {page === 'policy' && <Policy />}
            {page === 'settings' && <Settings />}
          </div>
        </main>
      </div>
    </div>
  )
}
