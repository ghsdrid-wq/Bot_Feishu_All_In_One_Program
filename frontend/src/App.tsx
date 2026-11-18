import { useCallback, useEffect, useState } from 'react'
import {
  Bot, CalendarCheck, Crown, FileSpreadsheet, Loader2, Radio, Settings as SettingsIcon, ShieldCheck, Table2, WifiOff,
} from 'lucide-react'
import { get, type EngineState } from './api'
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

  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col bg-white ring-1 ring-slate-200">
        <div className="px-5 pt-6 pb-5">
          <div className="text-lg font-bold text-brand-600">Auto Report</div>
          <div className="text-xs text-slate-500">KKN_GW · v{st?.version ?? '–'}</div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3">
          {pages.map(({ key, label, icon: Icon }) => (
            <button key={key} onClick={() => go(key)}
              className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${
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
                <span className={`h-2 w-2 rounded-full ${st?.running ? 'animate-pulse bg-amber-500' : st?.scheduler_running ? 'bg-sky-500' : 'bg-emerald-500'}`} />
                {st?.status ?? '…'}
              </div>
              <div className="mt-1 text-slate-500">
                {st?.scheduler_running ? `Auto · รอบถัดไป ${st.next_run?.slice(11) ?? '–'}` : 'Auto ปิดอยู่'}
              </div>
            </>
          )}
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl space-y-6 px-8 py-7">
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
  )
}
