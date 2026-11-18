import { useCallback, useEffect, useState } from 'react'
import { ExternalLink, Play, Plus, RefreshCw, RotateCcw, Save, Search, Send, Trash2 } from 'lucide-react'
import { del, get, post, put } from '../api'
import { Badge, Button, Card, Field, PageHeader, Stat, Toggle, fmt, inputClass, useFlash } from '../ui'

type MachineRow = {
  id: string; name: string; total: number; shift_a: number; shift_b: number
  avg_hr: number; peak_hr: number; cur_hour: number; recent_5m: number; is_live: boolean
}
type RoomItem = { chat_id: string; name: string; enabled: boolean }
type ChatSearchItem = { chat_id: string; name: string; description?: string; user_count?: number | string; chat_mode?: string }
type AppLog = { ts: string; cat: string; level: string; msg: string }
type CogyState = {
  busy: boolean
  server_running: boolean
  port: number
  lan_url: string
  dashboard_url: string
  default_start: string
  default_end: string
  job_status: {
    state: string; idle_minutes: number; idle_finish_minutes: number
    active_since: string; sent_hours_count: number; api_ok: boolean; api_down_minutes: number
  }
  app_logs: AppLog[]
  live: {
    grand_total?: number; wcs_total?: number; dws_total?: number
    wcs_live_count?: number; dws_live_count?: number
    cur_slot?: string; hour_labels?: string[]; tot_hourly?: number[]
    wcs_machines?: MachineRow[]; dws_machines?: MachineRow[]; updated_at?: string
  }
  settings: {
    cogy_base_url: string; branch_name: string; dashboard_passcode: string
    shift_a_start: number; shift_b_start: number
    start_min_total_delta: number; start_min_per_machine: number; idle_finish_minutes: number
    eod_summary_hour: number; auto_start_server: boolean; auto_recall_previous_hour: boolean
    enable_public_tunnel: boolean; feishu_test_mode: boolean; feishu_test_chat_id: string
    feishu_chat_id: string; feishu_rooms: RoomItem[]
    enable_feishu_live_card: boolean; enable_feishu_hourly_report: boolean
  }
}

export function CogyLive() {
  const [st, setSt] = useState<CogyState | null>(null)
  const [logCat, setLogCat] = useState('ALL')
  const [mTab, setMTab] = useState<'all' | 'live' | 'wcs' | 'dws'>('all')
  const [subTab, setSubTab] = useState<'live' | 'send' | 'settings'>('live')
  const [manualStart, setManualStart] = useState('')
  const [manualEnd, setManualEnd] = useState('')
  const [lookupId, setLookupId] = useState('')
  const [chatResults, setChatResults] = useState<ChatSearchItem[]>([])
  const [newRoomName, setNewRoomName] = useState('')
  const [newRoomId, setNewRoomId] = useState('')
  const [form, setForm] = useState<CogyState['settings'] | null>(null)
  const flash = useFlash()

  const load = useCallback(async () => {
    try {
      const data = await get<CogyState>(`/api/cogy/state?cat=${encodeURIComponent(logCat)}`)
      setSt(data)
      if (!manualStart && data.default_start) setManualStart(data.default_start)
      if (!manualEnd && data.default_end) setManualEnd(data.default_end)
      setForm((prev) => prev ?? data.settings)
    } catch {
      /* ignore polling error */
    }
  }, [logCat, manualStart, manualEnd])

  useEffect(() => {
    load()
    const t = setInterval(load, 2500)
    return () => clearInterval(t)
  }, [load])

  const startServer = async () => {
    try {
      await post('/api/cogy/server/start')
      flash.show('ok', 'กำลังเปิด CoGY Live Server + Auto Job Engine')
      setTimeout(load, 1200)
    } catch (e) { flash.show('err', (e as Error).message) }
  }

  const sendReport = async (mode: 'auto' | 'manual') => {
    try {
      await post('/api/cogy/send', { mode, start: manualStart, end: manualEnd })
      flash.show('ok', 'เริ่มดึงข้อมูลและส่งรายงาน CoGY เข้า Feishu แล้ว')
      load()
    } catch (e) { flash.show('err', (e as Error).message) }
  }

  const recall = async (scope: 'last' | 'all') => {
    try {
      await post('/api/cogy/recall', { scope })
      flash.show('ok', 'กำลังเรียกคืนข้อความ Feishu...')
      load()
    } catch (e) { flash.show('err', (e as Error).message) }
  }

  const updateModes = async (patch: Record<string, boolean>) => {
    try {
      await put('/api/cogy/modes', patch)
      load()
    } catch (e) { flash.show('err', (e as Error).message) }
  }

  const saveSettings = async () => {
    if (!form) return
    try {
      await put('/api/cogy/settings', form)
      flash.show('ok', 'บันทึกการตั้งค่า CoGY เรียบร้อยแล้ว')
      setForm(null)
      load()
    } catch (e) { flash.show('err', (e as Error).message) }
  }

  const live = st?.live || {}
  const js = st?.job_status
  const cfg = st?.settings
  const curIdx = live.hour_labels && live.cur_slot ? live.hour_labels.indexOf(live.cur_slot) : -1
  const curHourTotal = curIdx >= 0 && live.tot_hourly ? live.tot_hourly[curIdx] : 0

  const allMachines: (MachineRow & { kind: 'WCS' | 'DWS' })[] = [
    ...(live.wcs_machines || []).map((m) => ({ ...m, kind: 'WCS' as const })),
    ...(live.dws_machines || []).map((m) => ({ ...m, kind: 'DWS' as const })),
  ].filter((m) => {
    if (mTab === 'live') return m.is_live
    if (mTab === 'wcs') return m.kind === 'WCS'
    if (mTab === 'dws') return m.kind === 'DWS'
    return true
  })

  return (
    <div className="space-y-4">
      <PageHeader
        title="CoGY Live (WCS + DWS)"
        sub="ติดตามยอดคัดแยก CoGY แบบ Real-Time + Auto Job Engine + Feishu Live Card"
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={startServer}><Play size={15} /> เปิด/เช็ค Live Server (8505)</Button>
            {st?.dashboard_url && (
              <Button variant="ghost" onClick={() => window.open(st.dashboard_url, '_blank')}>
                <ExternalLink size={15} /> เปิด Live Dashboard
              </Button>
            )}
          </div>
        }
      />

      {flash.node}

      {/* แถบสถานะ + โหมด 2 แบบ */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={js?.state === 'ACTIVE' ? 'green' : js?.api_ok === false ? 'red' : 'blue'}>
              {js?.state === 'ACTIVE'
                ? `● ACTIVE (เริ่ม ${js.active_since || '-'} · นิ่ง ${js.idle_minutes}/${js.idle_finish_minutes}น.)`
                : js?.api_ok === false
                  ? `● STANDBY (CoGY หลุด ${js.api_down_minutes}น.)`
                  : '● STANDBY (รอตรวจจับงาน)'}
            </Badge>
            <Badge tone={st?.server_running ? 'green' : 'amber'}>
              {st?.server_running ? `Server :${st.port} ออนไลน์` : `Server :${st?.port || 8505} ปิดอยู่`}
            </Badge>
            {cfg?.feishu_test_mode && <Badge tone="amber">🧪 TEST MODE</Badge>}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Toggle
              checked={cfg?.enable_feishu_live_card ?? true}
              onChange={(v) => updateModes({ enable_feishu_live_card: v })}
              label="โหมดเรียลไทม์ (Live Card)"
            />
            <Toggle
              checked={cfg?.enable_feishu_hourly_report ?? false}
              onChange={(v) => updateModes({ enable_feishu_hourly_report: v })}
              label="โหมดรายชั่วโมง + Excel"
            />
            <Toggle
              checked={cfg?.feishu_test_mode ?? false}
              onChange={(v) => updateModes({ feishu_test_mode: v })}
              label="โหมดทดสอบ"
            />
            <Button variant="ghost" onClick={() => post('/api/cogy/job/reset').then(() => { flash.show('ok', 'รีเซ็ตเป็น STANDBY แล้ว'); load() })}>
              <RotateCcw size={14} /> รีเซ็ต STANDBY
            </Button>
          </div>
        </div>
      </Card>

      {/* Sub-navigation */}
      <div className="flex gap-2">
        {([
          ['live', '📊 ยอดสด & รายเครื่อง'],
          ['send', '🚀 ส่ง Feishu & เลือกห้องแชท'],
          ['settings', '⚙️ ตั้งค่า CoGY'],
        ] as const).map(([k, lbl]) => (
          <Button key={k} variant={subTab === k ? 'primary' : 'ghost'} onClick={() => setSubTab(k)}>
            {lbl}
          </Button>
        ))}
      </div>

      {subTab === 'live' && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="WCS (AutoPacking)" value={fmt(live.wcs_total || 0)} sub={`${live.wcs_live_count || 0}/${live.wcs_machines?.length || 0} เครื่องทำงาน`} />
            <Stat label="DWS (เครื่องสแกน)" value={fmt(live.dws_total || 0)} sub={`${live.dws_live_count || 0}/${live.dws_machines?.length || 0} เครื่องทำงาน`} />
            <Stat label={`ชั่วโมงปัจจุบัน (${live.cur_slot || '-'})`} value={fmt(curHourTotal)} sub="ยอดรวมชั่วโมงล่าสุด" />
            <Stat label="ยอดรวมทั้งหมด" value={fmt(live.grand_total || 0)} sub={`อัปเดต ${live.updated_at || '-'}`} />
          </div>

          <Card
            title={`สถานะเครื่องแบบเรียลไทม์ (${allMachines.length} เครื่อง)`}
            action={
              <div className="flex gap-1">
                {(['all', 'live', 'wcs', 'dws'] as const).map((k) => (
                  <button key={k} onClick={() => setMTab(k)}
                    className={`rounded-lg px-2.5 py-1 text-xs font-semibold ${mTab === k ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'}`}>
                    {k === 'all' ? 'ทั้งหมด' : k === 'live' ? 'กำลังทำงาน' : k.toUpperCase()}
                  </button>
                ))}
              </div>
            }
          >
            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
              {allMachines.map((m) => (
                <div key={`${m.kind}-${m.id}`}
                  className={`rounded-xl border p-3 transition ${m.is_live ? 'border-emerald-400/70 bg-emerald-50/40 dark:bg-emerald-950/20' : 'border-slate-200 bg-surface'}`}>
                  <div className="flex items-center justify-between text-xs font-bold">
                    <span>{m.name}</span>
                    <Badge tone={m.is_live ? 'green' : 'slate'}>{m.is_live ? `● +${fmt(m.recent_5m)}` : '○ พัก'}</Badge>
                  </div>
                  <div className="mt-1 text-lg font-extrabold">{fmt(m.total)}</div>
                  <div className="mt-1 flex justify-between text-[11px] text-slate-500">
                    <span>ชม.นี้: {fmt(m.cur_hour)}</span>
                    <span>เฉลี่ย: {fmt(m.avg_hr)}/ชม.</span>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}

      {subTab === 'send' && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="ส่งรายงาน & เลือกห้องแชท Feishu">
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" disabled={st?.busy} onClick={() => sendReport('auto')}><Send size={14} /> ส่งรายงานรอบล่าสุด</Button>
                <Button variant="danger" disabled={st?.busy} onClick={() => recall('last')}>เรียกคืนรอบล่าสุด</Button>
                <Button variant="ghost" disabled={st?.busy} onClick={() => recall('all')}>ล้างประวัติที่ส่ง</Button>
              </div>

              <div className="border-t pt-3">
                <div className="mb-2 text-xs font-bold text-slate-500">ส่งแบบกำหนดช่วงเวลาเอง (Manual)</div>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="เริ่มต้น"><input className={inputClass} value={manualStart} onChange={(e) => setManualStart(e.target.value)} /></Field>
                  <Field label="สิ้นสุด"><input className={inputClass} value={manualEnd} onChange={(e) => setManualEnd(e.target.value)} /></Field>
                </div>
                <div className="mt-2"><Button variant="secondary" disabled={st?.busy} onClick={() => sendReport('manual')}>ดึงข้อมูล & ส่งช่วงเวลาที่เลือก</Button></div>
              </div>

              <div className="border-t pt-3">
                <div className="mb-2 text-xs font-bold text-slate-500">ติ๊กเลือกห้องแชทปลายทาง (ส่งหลายห้องพร้อมกันได้)</div>
                <div className="space-y-1.5">
                  {(cfg?.feishu_rooms || []).map((r) => (
                    <div key={r.chat_id} className="flex items-center justify-between rounded-lg border px-3 py-1.5 text-xs">
                      <Toggle
                        checked={r.enabled}
                        onChange={(v) => put('/api/cogy/rooms/toggle', { chat_id: r.chat_id, enabled: v }).then(load)}
                        label={`${r.name} (${r.chat_id})`}
                      />
                      <button onClick={() => del(`/api/cogy/rooms/${encodeURIComponent(r.chat_id)}`).then(load)} className="text-rose-600 hover:underline">
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Card>

          <Card title="ค้นหา & เพิ่มห้องแชท Feishu (จาก Chat ID / กลุ่มที่บอทอยู่)">
            <div className="space-y-3">
              <div className="flex gap-2">
                <input className={inputClass} placeholder="ค้นหาด้วย Chat ID (oc_...)" value={lookupId} onChange={(e) => setLookupId(e.target.value)} />
                <Button variant="secondary" onClick={async () => {
                  try {
                    const r = await get<{ chat: ChatSearchItem }>(`/api/cogy/chats/${encodeURIComponent(lookupId)}`)
                    setChatResults([r.chat])
                  } catch (e) { flash.show('err', (e as Error).message) }
                }}><Search size={14} /> ตรวจสอบ</Button>
                <Button variant="ghost" onClick={async () => {
                  try {
                    const r = await get<{ chats: ChatSearchItem[] }>('/api/cogy/chats')
                    setChatResults(r.chats)
                  } catch (e) { flash.show('err', (e as Error).message) }
                }}><RefreshCw size={14} /> ดึงกลุ่มทั้งหมด</Button>
              </div>

              <div className="flex gap-2">
                <input className={inputClass} placeholder="ชื่อห้อง (ไม่บังคับ)" value={newRoomName} onChange={(e) => setNewRoomName(e.target.value)} />
                <input className={inputClass} placeholder="Chat ID (oc_...)" value={newRoomId} onChange={(e) => setNewRoomId(e.target.value)} />
                <Button variant="primary" onClick={async () => {
                  try {
                    await post('/api/cogy/rooms/add', { chat_id: newRoomId, name: newRoomName })
                    setNewRoomId(''); setNewRoomName(''); load()
                  } catch (e) { flash.show('err', (e as Error).message) }
                }}><Plus size={14} /> เพิ่ม</Button>
              </div>

              <div className="max-h-60 space-y-1.5 overflow-y-auto">
                {chatResults.map((c) => (
                  <div key={c.chat_id} className="flex items-center justify-between rounded-lg border p-2 text-xs">
                    <div>
                      <div className="font-bold">{c.name}</div>
                      <div className="text-slate-500">{c.chat_id} {c.user_count ? `· สมาชิก ${c.user_count} คน` : ''}</div>
                    </div>
                    <Button variant="secondary" onClick={() => post('/api/cogy/rooms/add', { chat_id: c.chat_id, name: c.name }).then(() => { flash.show('ok', `เพิ่มห้อง ${c.name} แล้ว`); load() })}>
                      + เพิ่มเข้าลิสต์
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </div>
      )}

      {subTab === 'settings' && form && (
        <Card title="ตั้งค่าระบบ CoGY & Auto Job Engine" action={<Button variant="primary" onClick={saveSettings}><Save size={14} /> บันทึก</Button>}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="CoGY Base URL"><input className={inputClass} value={form.cogy_base_url} onChange={(e) => setForm({ ...form, cogy_base_url: e.target.value })} /></Field>
            <Field label="สาขา (Branch)"><input className={inputClass} value={form.branch_name} onChange={(e) => setForm({ ...form, branch_name: e.target.value })} /></Field>
            <Field label="รหัสผ่าน Live Dashboard"><input className={inputClass} value={form.dashboard_passcode} onChange={(e) => setForm({ ...form, dashboard_passcode: e.target.value })} /></Field>
            <Field label="เริ่มกะ A (ชั่วโมง 0-23)"><input type="number" className={inputClass} value={form.shift_a_start} onChange={(e) => setForm({ ...form, shift_a_start: Number(e.target.value) })} /></Field>
            <Field label="เริ่มกะ B (ชั่วโมง 0-23)"><input type="number" className={inputClass} value={form.shift_b_start} onChange={(e) => setForm({ ...form, shift_b_start: Number(e.target.value) })} /></Field>
            <Field label="เวลาส่งสรุปประจำวัน (ชั่วโมง)"><input type="number" className={inputClass} value={form.eod_summary_hour} onChange={(e) => setForm({ ...form, eod_summary_hour: Number(e.target.value) })} /></Field>
            <Field label="ยอดขั้นต่ำเริ่มงาน (ชิ้น)"><input type="number" className={inputClass} value={form.start_min_total_delta} onChange={(e) => setForm({ ...form, start_min_total_delta: Number(e.target.value) })} /></Field>
            <Field label="ยอดต่อเครื่อง (>=2 เครื่อง)"><input type="number" className={inputClass} value={form.start_min_per_machine} onChange={(e) => setForm({ ...form, start_min_per_machine: Number(e.target.value) })} /></Field>
            <Field label="จบงานเมื่อยอดนิ่ง (นาที)"><input type="number" className={inputClass} value={form.idle_finish_minutes} onChange={(e) => setForm({ ...form, idle_finish_minutes: Number(e.target.value) })} /></Field>
            <Field label="Feishu Test Chat ID"><input className={inputClass} value={form.feishu_test_chat_id} onChange={(e) => setForm({ ...form, feishu_test_chat_id: e.target.value })} /></Field>
          </div>
          <div className="mt-3 flex flex-wrap gap-4">
            <Toggle checked={form.auto_start_server} onChange={(v) => setForm({ ...form, auto_start_server: v })} label="เปิด Server & Auto Job อัตโนมัติ" />
            <Toggle checked={form.auto_recall_previous_hour} onChange={(v) => setForm({ ...form, auto_recall_previous_hour: v })} label="Auto-Recall รอบก่อนหน้า" />
            <Toggle checked={form.enable_public_tunnel} onChange={(v) => setForm({ ...form, enable_public_tunnel: v })} label="เปิด Cloudflare Tunnel (HTTPS)" />
          </div>
        </Card>
      )}

      {/* Activity Log กรองตามหมวด */}
      <Card
        title="Activity Log (CoGY & Auto Job)"
        action={
          <div className="flex gap-1">
            {['ALL', 'JOB', 'FEISHU', 'API', 'ALERT', 'SYSTEM'].map((c) => (
              <button key={c} onClick={() => setLogCat(c)}
                className={`rounded-lg px-2 py-0.5 text-xs font-semibold ${logCat === c ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'}`}>
                {c === 'ALL' ? 'ทั้งหมด' : c}
              </button>
            ))}
          </div>
        }
      >
        <div className="h-48 overflow-y-auto rounded-xl bg-console p-3 font-mono text-xs text-slate-200">
          {(st?.app_logs || []).map((l, i) => (
            <div key={i} className={l.level === 'err' || l.level === 'error' ? 'text-rose-400' : l.level === 'ok' ? 'text-emerald-400' : 'text-slate-300'}>
              [{l.ts}] [{l.cat}] {l.msg}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
