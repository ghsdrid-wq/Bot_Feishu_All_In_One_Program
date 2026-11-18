import { useEffect, useState } from 'react'
import { AlertTriangle, ExternalLink, Pause, Play, RotateCcw, Square, Trash2, Undo2, Zap } from 'lucide-react'
import { get, post, put, type EngineState } from '../api'
import type { Go } from '../App'
import { LiveLog, useLogs } from '../LiveLog'
import { Badge, Button, Card, Modal, PageHeader, Toggle, inputClass, useFlash } from '../ui'

type Step = { key: string; title: string; icon: string; on: boolean }
type Pipeline = { steps: Step[]; report_mode: string; report_modes: { value: string; label: string }[]; summary_header: boolean }

const stateStyle: Record<string, { label: string; cls: string }> = {
  ready: { label: 'พร้อม', cls: 'bg-slate-100 text-slate-500' },
  run: { label: 'กำลังทำ', cls: 'bg-sky-100 text-sky-700 animate-pulse' },
  ok: { label: 'เสร็จ', cls: 'bg-emerald-100 text-emerald-700' },
  warning: { label: 'มีคำเตือน', cls: 'bg-amber-100 text-amber-700' },
  error: { label: 'ผิดพลาด', cls: 'bg-brand-100 text-brand-700' },
  skip: { label: 'ข้าม', cls: 'bg-slate-100 text-slate-400' },
}

const pad = (n: number) => String(n).padStart(2, '0')
const isoDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

export function Report({ st, refresh, go }: { st: EngineState | null; refresh: () => void; go: Go }) {
  const [pl, setPl] = useState<Pipeline | null>(null)
  const { lines, clear } = useLogs('main')
  const flash = useFlash()
  const [recallOpen, setRecallOpen] = useState(false)

  useEffect(() => {
    get<Pipeline>('/api/pipeline').then(setPl)
  }, [])

  const act = async (action: string, okText: string) => {
    try {
      await post(`/api/run/${action}`)
      flash.show('ok', okText)
    } catch (e) {
      flash.show('err', (e as Error).message)
    }
    refresh()
  }
  const savePipeline = async (body: object) => setPl(await put<Pipeline>('/api/pipeline', body))
  const setSchedule = async (body: object) => {
    try {
      await put('/api/schedule', body)
      refresh()
    } catch (e) {
      flash.show('err', (e as Error).message)
    }
  }

  const locked = !!(st?.running || st?.scheduler_running)
  const warnings: string[] = []
  if (st && !st.jms_token_set) warnings.push('ยังไม่ได้ใส่ JMS Token')
  if (st && !st.feishu_ready) warnings.push('ยังไม่ได้ใส่ App ID / App Secret ของ Feishu')

  return (
    <>
      <PageHeader title="รายงานรายชั่วโมง" sub="ดึงยอด DWS / JMS → ทำตาราง → ส่ง Feishu ตามรอบเวลา"
        actions={<Button variant="ghost" icon={<Undo2 size={16} />} onClick={() => setRecallOpen(true)}>ถอนข้อความ</Button>} />

      {warnings.map((w) => (
        <div key={w} className="flex items-center gap-3 rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-800 ring-1 ring-amber-200">
          <AlertTriangle size={18} /> <span className="flex-1">{w}</span>
          <Button onClick={() => go('settings')}>ไปหน้าตั้งค่า</Button>
        </div>
      ))}
      {flash.node}

      {/* ---- สถานะ + ปุ่มหลัก */}
      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200">
        <div className="flex flex-wrap items-center gap-5">
          <div className="flex-1">
            <div className="text-sm text-slate-500">สถานะ</div>
            <div className="text-2xl font-semibold" style={{ color: st?.running ? '#b45309' : undefined }}>{st?.status ?? '…'}</div>
            <div className="mt-1 text-sm text-slate-500">
              {st?.scheduler_running ? <>Auto เปิดอยู่ · รอบถัดไป <b className="text-ink">{st.next_run ?? '–'}</b></> : 'Auto ปิดอยู่ — โปรแกรมจำไว้ เปิดใหม่แล้วกลับมาตามเดิม'}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {st?.scheduler_running ? (
              <Button icon={<Pause size={16} />} onClick={() => act('auto-stop', 'หยุด Auto แล้ว')}>หยุด Auto</Button>
            ) : (
              <Button variant="primary" icon={<Play size={16} />} disabled={st?.running} onClick={() => act('auto-start', 'เปิด Auto แล้ว')}>เริ่ม Auto</Button>
            )}
            {st?.running ? (
              <Button variant="danger" icon={<Square size={16} />} onClick={() => act('stop', 'สั่งหยุดรอบนี้แล้ว')}>หยุดรอบนี้</Button>
            ) : (
              <Button icon={<Zap size={16} />} onClick={() => act('now', 'เริ่มรันรอบนี้แล้ว')}>รันตอนนี้</Button>
            )}
          </div>
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100">
          <div className="h-full rounded-full bg-brand-600 transition-all" style={{ width: `${st?.progress ?? 0}%` }} />
        </div>
      </section>

      {/* ---- รอบเวลา */}
      <Card title="รอบเวลา" action={locked && <Badge tone="amber">แก้ได้ แต่มีผลรอบถัดไป</Badge>}>
        <div className="grid gap-5 md:grid-cols-[repeat(3,minmax(0,1fr))_2fr]">
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-700">รันทุกชั่วโมงที่นาที</span>
            <select className={inputClass} value={st?.time.run_minute ?? 5} onChange={(e) => setSchedule({ run_minute: +e.target.value })}>
              {Array.from({ length: 60 }, (_, m) => <option key={m} value={m}>{pad(m)}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-700">เริ่ม</span>
            <select className={inputClass} value={st?.time.start_hour ?? 15} onChange={(e) => setSchedule({ start_hour: +e.target.value })}>
              {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{pad(h)}:00</option>)}
            </select>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-700">สิ้นสุด</span>
            <select className={inputClass} value={st?.time.end_hour ?? 12} onChange={(e) => setSchedule({ end_hour: +e.target.value })}>
              {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{pad(h)}:00</option>)}
            </select>
          </label>
          <div>
            <span className="mb-1.5 block text-sm font-medium text-slate-700">ช่วงวันที่ {st?.dates.manual ? <Badge tone="amber">เลือกเอง</Badge> : <Badge tone="slate">ตามรอบงาน</Badge>}</span>
            <div className="flex items-center gap-2">
              <input type="date" className={inputClass} value={st?.dates.start ?? ''}
                onChange={(e) => st && setSchedule({ start_date: e.target.value, end_date: st.dates.end })} />
              <span className="text-slate-400">→</span>
              <input type="date" className={inputClass} value={st?.dates.end ?? ''}
                onChange={(e) => st && setSchedule({ start_date: st.dates.start, end_date: e.target.value })} />
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {[0, -1].map((off) => (
                <button key={off} className="rounded-lg px-2.5 py-1 text-xs ring-1 ring-slate-200 hover:bg-slate-50"
                  onClick={() => {
                    const s = new Date(); s.setDate(s.getDate() + off)
                    const e = new Date(s); e.setDate(e.getDate() + 1)
                    setSchedule({ start_date: isoDay(s), end_date: isoDay(e) })
                  }}>{off === 0 ? 'วันนี้' : 'เมื่อวาน'}</button>
              ))}
              {st?.dates.manual && (
                <button className="inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs text-brand-700 ring-1 ring-brand-100 hover:bg-brand-50"
                  onClick={() => setSchedule({ dates: 'auto' })}><RotateCcw size={12} /> กลับไปตามรอบงาน</button>
              )}
            </div>
          </div>
        </div>
      </Card>

      {/* ---- ลำดับการทำงาน */}
      {pl && (
        <Card title="ลำดับการทำงาน" action={
          <Toggle checked={pl.summary_header} onChange={(v) => savePipeline({ summary_header: v })} label="เพิ่มหัว Dashboard" />
        }>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {pl.steps.map((s, i) => {
              const state = st?.pipeline[s.key] ?? 'ready'
              const sty = stateStyle[state] ?? stateStyle.ready
              return (
                <div key={s.key} className={`rounded-2xl p-4 ring-1 transition ${s.on ? 'bg-white ring-slate-200' : 'bg-slate-50 ring-slate-200'}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="text-xs text-slate-400">ขั้นที่ {i + 1}</div>
                      <div className={`mt-0.5 font-semibold ${s.on ? '' : 'text-slate-400'}`}><span className="mr-1.5">{s.icon}</span>{s.title}</div>
                    </div>
                    <Toggle checked={s.on} onChange={(v) => savePipeline({ steps: { [s.key]: v } })} label="" />
                  </div>
                  {s.key === 'report' && (
                    <select className={`${inputClass} mt-3 py-1.5`} value={pl.report_mode} onChange={(e) => savePipeline({ report_mode: e.target.value })}>
                      {pl.report_modes.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                    </select>
                  )}
                  <div className="mt-3"><span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${s.on ? sty.cls : 'bg-slate-100 text-slate-400'}`}>{s.on ? sty.label : 'ไม่ทำ'}</span></div>
                </div>
              )
            })}
          </div>
        </Card>
      )}

      <Card title="Live Log" action={<Button variant="ghost" icon={<Trash2 size={15} />} onClick={clear}>ล้าง</Button>}>
        <LiveLog lines={lines} height="h-[440px]" />
      </Card>

      <DashboardCard />
      {recallOpen && <RecallDialog onClose={() => setRecallOpen(false)} />}
    </>
  )
}

function DashboardCard() {
  const [d, setD] = useState<{ started: boolean; status: string; url: string } | null>(null)
  useEffect(() => {
    get<typeof d>('/api/dashboard').then(setD)
  }, [])
  if (!d) return null
  return (
    <Card title="Dashboard เว็บ (พอร์ต 6200)">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Badge tone={d.started ? 'green' : 'slate'}>{d.started ? 'เปิดอยู่' : 'ยังไม่เปิด'}</Badge>
        <span className="text-slate-500">{d.status}</span>
        <span className="flex-1" />
        {!d.started && <Button onClick={async () => setD(await post('/api/dashboard/start'))}>เปิดเซิร์ฟเวอร์</Button>}
        {d.url && <a className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 font-medium text-brand-700 hover:bg-brand-50" href={d.url} target="_blank" rel="noreferrer"><ExternalLink size={15} /> เปิด Dashboard</a>}
      </div>
    </Card>
  )
}

type Round = { id: string; label: string; kind: string; sent_at: string; status: string; left: number }

function RecallDialog({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<Round[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const load = () => get<Round[]>('/api/recall').then(setRows)
  useEffect(() => {
    load()
  }, [])
  return (
    <Modal title="ถอนข้อความที่บอทส่ง (30 รอบล่าสุด)" onClose={onClose}>
      {!rows ? 'กำลังโหลด…' : rows.length === 0 ? <div className="text-sm text-slate-500">ยังไม่มีรอบที่ส่ง</div> : (
        <div className="divide-y divide-slate-100">
          {rows.map((r) => (
            <div key={r.id} className="flex items-center gap-3 py-3 text-sm">
              <div className="flex-1">
                <div className="font-medium">{r.label}</div>
                <div className="text-xs text-slate-500">{r.sent_at} · {r.status}</div>
              </div>
              <Button variant="danger" disabled={r.left <= 0 || busy !== null}
                onClick={async () => {
                  if (!confirm(`ถอน ${r.left} ข้อความของ "${r.label}" ใช่ไหม? ถอนแล้วกู้คืนไม่ได้`)) return
                  setBusy(r.id)
                  try {
                    const res = await post<{ done: number; errors: string[] }>(`/api/recall/${r.id}`)
                    alert(`ถอนสำเร็จ ${res.done} ข้อความ${res.errors.length ? `\nผิดพลาด ${res.errors.length}` : ''}`)
                  } catch (e) {
                    alert((e as Error).message)
                  }
                  setBusy(null)
                  load()
                }}>{busy === r.id ? 'กำลังถอน…' : 'ถอน'}</Button>
            </div>
          ))}
        </div>
      )}
    </Modal>
  )
}
