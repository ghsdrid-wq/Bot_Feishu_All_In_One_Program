import { useEffect, useRef, useState } from 'react'
import { Download, Eye, Loader2, Plus, Save, Send, X } from 'lucide-react'
import { fileUrl, get, post, put, type EngineState } from '../api'
import type { Go } from '../App'
import { LiveLog, useLogs } from '../LiveLog'
import { Badge, Button, Card, Field, PageHeader, Stat, Toggle, fmt, inputClass, useFlash } from '../ui'

type BrandRow = { name: string; total: number; last_hour: number; hourly: number[]; sent: number; stuck: number; missed: number }
type Result = {
  period: string; until: string; closing: boolean; status_at: string | null; hours: string[]; brands: BrandRow[]
  totals: { in: number; sent: number; stuck: number; missed: number }; pending: number
  stuck_list: { awb: string; brand: string; first_scan: string; hours: number | null; status: string; last: string }[]
  image: string | null; excel: string | null; sent: boolean; skipped: string; finished: string
}
type Settings = {
  chat_id: string; day_start_hour: number; send_image: boolean; send_excel: boolean; title: string
  brands: { name: string; terms: string[] }[]; scan_types: string; lookup_prefixes: string; customer_code_prefix: string
}
type VipState = { busy: boolean; status: string; last: Result | Record<string, never>; stage_on: boolean; settings: Settings }

export function Vip({ st, go }: { st: EngineState | null; go: Go }) {
  const [v, setV] = useState<VipState | null>(null)
  const flash = useFlash()
  const { lines } = useLogs('main')
  const wasBusy = useRef(false)
  const load = () => get<VipState>('/api/vip').then(setV)
  useEffect(() => {
    load()
  }, [])
  useEffect(() => {
    if (wasBusy.current && !st?.vip_busy) load()
    wasBusy.current = !!st?.vip_busy
  }, [st?.vip_busy])

  const run = async (send: boolean) => {
    try {
      await post('/api/vip/run', { send })
      flash.show('ok', send ? 'เริ่มส่งแล้ว — ดูความคืบหน้าด้านล่าง' : 'เริ่มสร้างตัวอย่างแล้ว (ไม่ส่ง Feishu)')
    } catch (e) {
      flash.show('err', (e as Error).message)
    }
  }
  const latest = [...lines].reverse().find((l) => l.text.startsWith('VIP AWB'))
  const result = v && 'brands' in v.last ? (v.last as Result) : null

  return (
    <>
      <PageHeader title="VIP AWB" sub="พัสดุ 8 แบรนด์ VIP ที่เข้า KKN_GW รายชั่วโมง พร้อมสถานะ ออกแล้ว / คงค้าง"
        actions={<>
          <Button icon={<Eye size={16} />} onClick={() => run(false)} disabled={st?.vip_busy}>สร้างตัวอย่าง</Button>
          <Button variant="primary" icon={<Send size={16} />} onClick={() => run(true)} disabled={st?.vip_busy}>ส่งเข้า Feishu ตอนนี้</Button>
        </>} />

      <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-surface px-5 py-4 text-sm shadow-sm ring-1 ring-slate-200">
        <span className="font-medium">ส่งอัตโนมัติ</span>
        {v?.stage_on ? <Badge tone="green">เปิดในรอบรายงานรายชั่วโมง</Badge> : <Badge tone="slate">ปิดอยู่</Badge>}
        <span className="text-slate-500">ส่งหลังขั้นตอน Feishu ของทุกรอบ ขณะเปิด Auto</span>
        <span className="flex-1" />
        <Button variant="ghost" onClick={() => go('report')}>ตั้งที่หน้ารายงานรายชั่วโมง</Button>
      </div>

      {st?.vip_busy && (
        <div className="flex items-center gap-3 rounded-2xl bg-sky-50 px-5 py-4 text-sm text-sky-800 ring-1 ring-sky-200">
          <Loader2 size={18} className="shrink-0 animate-spin" />
          <div>
            <div className="font-medium">กำลังทำงาน — รอบแรกของรอบวันอาจใช้ 10–30 นาที</div>
            {latest && <div className="text-sky-700">{latest.text}</div>}
          </div>
        </div>
      )}
      {flash.node}

      {result ? <ResultView r={result} /> : (
        <Card><div className="py-6 text-center text-sm text-slate-500">ยังไม่มีผลตั้งแต่เปิดโปรแกรม — กด "สร้างตัวอย่าง" เพื่อดึงยอดล่าสุด</div></Card>
      )}

      <Card title="ความคืบหน้า (Live Log)">
        <LiveLog lines={lines} filter="VIP AWB" height="h-56" />
      </Card>

      {v && <VipSettingsCard initial={v.settings} onSaved={(s) => setV({ ...v, settings: s })} />}
    </>
  )
}

function ResultView({ r }: { r: Result }) {
  const max = Math.max(1, ...r.brands.flatMap((b) => b.hourly))
  const until = `${r.until.slice(8, 10)}/${r.until.slice(5, 7)} ${r.until.slice(11, 16)}`
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
        <Badge tone={r.closing ? 'green' : 'blue'}>{r.closing ? 'สรุปปิดรอบ' : 'ยอดสะสม'}</Badge>
        รอบ <b>{r.period}</b> · ข้อมูลถึง <b>{until} น.</b>
        {r.status_at && <> · สถานะ ณ {r.status_at.slice(11)} น.</>}
        {r.sent ? <Badge tone="green">ส่ง Feishu แล้ว</Badge> : r.skipped ? <Badge tone="amber">{r.skipped}</Badge> : <Badge tone="slate">ยังไม่ส่ง</Badge>}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="เข้าคลัง" value={fmt(r.totals.in)} sub={`ชม.ล่าสุด +${fmt(r.brands.reduce((s, b) => s + b.last_hour, 0))}`} />
        <Stat label="สแกนออกแล้ว" value={fmt(r.totals.sent)} tone="good" />
        <Stat label="คงค้างในคลัง" value={fmt(r.totals.stuck)} tone={r.totals.stuck ? 'bad' : 'default'} />
        <Stat label="หลุดสแกนส่ง" value={fmt(r.totals.missed)} tone={r.totals.missed ? 'warn' : 'default'} />
      </div>
      <Card title="ยอดแยกแบรนด์" action={r.excel && (
        <a href={fileUrl(r.excel)} className="inline-flex items-center gap-2 rounded-xl px-3 py-1.5 text-sm font-medium text-brand-700 hover:bg-brand-50"><Download size={16} /> Excel</a>
      )}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-500">
              <th className="py-2 pr-4 font-medium">แบรนด์</th><th className="py-2 pr-4 text-right font-medium">เข้าคลัง</th>
              <th className="py-2 pr-4 text-right font-medium">ออกแล้ว</th><th className="py-2 pr-4 text-right font-medium">คงค้าง</th>
              <th className="py-2 pr-4 text-right font-medium">หลุดสแกน</th>
              <th className="py-2 font-medium">รายชั่วโมง ({r.hours[0]}–{r.hours[r.hours.length - 1] ?? ''})</th></tr></thead>
            <tbody>
              {r.brands.map((b) => (
                <tr key={b.name} className={`border-t border-slate-100 ${b.total ? '' : 'text-slate-400'}`}>
                  <td className="py-2.5 pr-4 font-medium">{b.name}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums"><b>{fmt(b.total)}</b>{b.last_hour > 0 && <span className="ml-1 text-emerald-600">+{b.last_hour}</span>}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{b.total ? fmt(b.sent) : '–'}</td>
                  <td className={`py-2.5 pr-4 text-right tabular-nums ${b.stuck ? 'font-semibold text-brand-600' : ''}`}>{b.total ? fmt(b.stuck) : '–'}</td>
                  <td className={`py-2.5 pr-4 text-right tabular-nums ${b.missed ? 'font-semibold text-amber-600' : ''}`}>{b.total ? fmt(b.missed) : '–'}</td>
                  <td className="py-2.5"><div className="flex h-6 items-end gap-0.5">
                    {b.hourly.map((n, i) => (
                      <div key={i} title={`${r.hours[i]} · ${n}`} className={`w-2 rounded-sm ${n ? 'bg-brand-500' : 'bg-slate-100'}`}
                        style={{ height: `${n ? Math.max(18, (n / max) * 100) : 18}%` }} />
                    ))}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {r.stuck_list.length > 0 && (
        <Card title={`เลขที่ยังค้าง / หลุดสแกน (${r.stuck_list.length})`}>
          <div className="max-h-72 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface"><tr className="text-left text-slate-500">
                <th className="py-2 pr-4 font-medium">หมายเลขพัสดุ</th><th className="py-2 pr-4 font-medium">แบรนด์</th>
                <th className="py-2 pr-4 font-medium">เข้าคลัง</th><th className="py-2 pr-4 text-right font-medium">ค้างมา (ชม.)</th>
                <th className="py-2 font-medium">สถานะล่าสุด</th></tr></thead>
              <tbody>{r.stuck_list.map((a) => (
                <tr key={a.awb} className="border-t border-slate-100">
                  <td className="py-2 pr-4 font-mono">{a.awb}</td><td className="py-2 pr-4">{a.brand}</td>
                  <td className="py-2 pr-4">{a.first_scan.slice(5, 16)}</td><td className="py-2 pr-4 text-right tabular-nums">{a.hours ?? '–'}</td>
                  <td className="py-2">{a.status === 'หลุดสแกนส่ง' ? <Badge tone="amber">หลุดสแกนส่ง</Badge> : a.last}</td>
                </tr>))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {r.image && (
        <Card title="รูปตารางที่ส่งเข้า Feishu">
          <a href={fileUrl(r.image)} target="_blank" rel="noreferrer">
            <img src={fileUrl(r.image)} alt="ตารางยอดรายชั่วโมง" className="w-full rounded-xl ring-1 ring-slate-200" />
          </a>
        </Card>
      )}
    </>
  )
}

function VipSettingsCard({ initial, onSaved }: { initial: Settings; onSaved: (s: Settings) => void }) {
  const [s, setS] = useState(initial)
  const flash = useFlash()
  const set = (patch: Partial<Settings>) => setS({ ...s, ...patch })
  const save = async () => {
    try {
      const r = await put<{ settings: Settings }>('/api/vip', s)
      setS(r.settings)
      onSaved(r.settings)
      flash.show('ok', 'บันทึกแล้ว')
    } catch (e) {
      flash.show('err', (e as Error).message)
    }
  }
  return (
    <Card title="ตั้งค่า VIP AWB" action={<Button variant="primary" icon={<Save size={16} />} onClick={save}>บันทึก</Button>}>
      {flash.node && <div className="mb-4">{flash.node}</div>}
      <div className="grid gap-5 md:grid-cols-2">
        <Field label="Chat ID ที่ส่ง" hint="ว่าง = ใช้ Chat ID ในหน้าตั้งค่า">
          <input className={inputClass} value={s.chat_id} onChange={(e) => set({ chat_id: e.target.value })} placeholder="oc_…" />
        </Field>
        <Field label="รอบวันเริ่ม" hint="ถึงก่อนเวลาเดียวกันของวันถัดไป · รันตอนชั่วโมงนี้ = สรุปปิดรอบ">
          <select className={inputClass} value={s.day_start_hour} onChange={(e) => set({ day_start_hour: +e.target.value })}>
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
          </select>
        </Field>
      </div>
      <div className="mt-5 flex flex-wrap gap-8">
        <Toggle checked={s.send_image} onChange={(x) => set({ send_image: x })} label="แนบรูปตารางรายชั่วโมง" />
        <Toggle checked={s.send_excel} onChange={(x) => set({ send_excel: x })} label="ส่งไฟล์ Excel" hint="สรุป + รายการ AWB + เลขที่ค้าง" />
      </div>
      <div className="mt-7">
        <div className="mb-2 flex items-center justify-between">
          <div>
            <div className="text-sm font-medium text-slate-700">แบรนด์</div>
            <div className="text-xs text-slate-500">คำค้นเทียบเป็นคำเต็มกับ แหล่งที่มาคำสั่งซื้อ / รหัสลูกค้า / ชื่อลูกค้า (เช่น ADIDAS, VIP111111383) คั่นด้วย ,</div>
          </div>
          <Button variant="ghost" icon={<Plus size={16} />} onClick={() => set({ brands: [...s.brands, { name: '', terms: [] }] })}>เพิ่มแบรนด์</Button>
        </div>
        <div className="space-y-2">
          {s.brands.map((b, i) => (
            <div key={i} className="flex gap-2">
              <input className={`${inputClass} max-w-48`} value={b.name} placeholder="ชื่อที่แสดง"
                onChange={(e) => set({ brands: s.brands.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} />
              <input className={inputClass} value={b.terms.join(', ')} placeholder="คำค้น, รหัสลูกค้า"
                onChange={(e) => set({ brands: s.brands.map((x, j) => (j === i ? { ...x, terms: e.target.value.split(',').map((t) => t.trim()) } : x)) })} />
              <button className="rounded-xl px-2 text-slate-400 hover:bg-slate-100 hover:text-brand-600" title="ลบ"
                onClick={() => set({ brands: s.brands.filter((_, j) => j !== i) })}><X size={18} /></button>
            </div>
          ))}
        </div>
      </div>
      <details className="mt-7 rounded-xl bg-slate-50 p-4 ring-1 ring-slate-200">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">ขั้นสูง</summary>
        <div className="mt-4 grid gap-5 md:grid-cols-3">
          <Field label="ประเภทสแกนเข้าคลัง" hint="卸车扫描 ลงรถ / 发件扫描 ส่งออก / 建包扫描 บรรจุกระสอบ">
            <input className={inputClass} value={s.scan_types} onChange={(e) => set({ scan_types: e.target.value })} />
          </Field>
          <Field label="ค้นเฉพาะเลขขึ้นต้น" hint="JTTH = TikTok ไม่ต้องค้น">
            <input className={inputClass} value={s.lookup_prefixes} onChange={(e) => set({ lookup_prefixes: e.target.value })} />
          </Field>
          <Field label="นับ VIP ทุกรหัสขึ้นต้น" hint="ว่าง = เฉพาะแบรนด์ด้านบน">
            <input className={inputClass} value={s.customer_code_prefix} onChange={(e) => set({ customer_code_prefix: e.target.value })} placeholder="VIP111111" />
          </Field>
        </div>
      </details>
    </Card>
  )
}
