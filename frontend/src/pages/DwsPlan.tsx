import { useEffect, useState } from 'react'
import { Power, RefreshCw, Save, Trash2 } from 'lucide-react'
import { get, post, put, type EngineState } from '../api'
import { LiveLog, useLogs } from '../LiveLog'
import { Badge, Button, Card, PageHeader, inputClass, useFlash } from '../ui'

type Client = { name: string; ip: string; port: number | string; pull_folder?: string; pull_file?: string; pull_enabled?: boolean }
type Row = { pc: string; ip: string; port: number; status: string; plan: string; message: string }
type Dws = { bot_running: boolean; plans: string[]; aliases: { day: string; night: string }; clients: Client[]; rows: Row[]; rows_at: string | null }

export function DwsPlan({ st, refresh }: { st: EngineState | null; refresh: () => void }) {
  const [d, setD] = useState<Dws | null>(null)
  const [clients, setClients] = useState<Client[]>([])
  const flash = useFlash()
  const { lines, clear } = useLogs('controller')
  const load = async () => {
    const r = await get<Dws>('/api/dws')
    setD(r)
    return r
  }
  useEffect(() => {
    load().then((r) => setClients(r.clients))
    post('/api/dws/refresh').then(() => setTimeout(load, 4000))
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const bot = async (on: boolean) => {
    await post(`/api/bot/feishu/${on ? 'start' : 'stop'}`)
    load()
    refresh()
  }
  const switchPlan = async (plan: string) => {
    if (!confirm(`เปลี่ยนแพลนทุกเครื่องเป็น ${plan} ใช่ไหม?`)) return
    await post('/api/dws/switch', { plan })
    flash.show('ok', `สั่งเปลี่ยนเป็น ${plan} แล้ว — ดูผลในตารางและ log`)
    setTimeout(load, 5000)
  }

  const online = d?.rows.filter((r) => r.status === 'ONLINE').length ?? 0
  return (
    <>
      <PageHeader title="DWS PLAN" sub="เปลี่ยนแพลนการคัดแยกของเครื่อง DWS ทุกเครื่องพร้อมกัน และบอทสั่งผ่าน Feishu"
        actions={st?.bot_running
          ? <Button icon={<Power size={16} />} onClick={() => bot(false)}>หยุดบอท Feishu</Button>
          : <Button variant="primary" icon={<Power size={16} />} onClick={() => bot(true)}>เริ่มบอท Feishu</Button>} />
      {flash.node}
      <div className="grid gap-4 md:grid-cols-3">
        <div className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200">
          <div className="text-sm text-slate-500">บอท Feishu (webhook)</div>
          <div className="mt-1 text-xl font-semibold">{st?.bot_running ? <span className="text-emerald-600">ONLINE</span> : <span className="text-slate-400">OFFLINE</span>}</div>
        </div>
        <div className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200">
          <div className="text-sm text-slate-500">เครื่องที่ติดต่อได้</div>
          <div className="mt-1 text-xl font-semibold tabular-nums">{online} / {d?.rows.length ?? 0}</div>
        </div>
        <div className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200">
          <div className="text-sm text-slate-500">ชื่อแพลน กลางวัน / กลางคืน</div>
          <div className="mt-1 text-xl font-semibold">{d?.aliases.day || '–'} / {d?.aliases.night || '–'}</div>
        </div>
      </div>

      <Card title="เปลี่ยนแพลน" action={<Button variant="ghost" icon={<RefreshCw size={15} />} onClick={async () => setD(await post('/api/dws/plans/reload'))}>โหลดรายชื่อแพลน</Button>}>
        <div className="flex flex-wrap gap-2">
          {d?.plans.map((p) => (
            <button key={p} onClick={() => switchPlan(p)}
              className="rounded-xl bg-white px-5 py-3 font-semibold ring-1 ring-slate-300 transition hover:bg-brand-50 hover:text-brand-700 hover:ring-brand-500">{p}</button>
          ))}
        </div>
      </Card>

      <Card title="สถานะเครื่อง" action={
        <div className="flex items-center gap-3 text-xs text-slate-500">
          {d?.rows_at && `อัปเดต ${d.rows_at}`}
          <Button variant="ghost" icon={<RefreshCw size={15} />} onClick={async () => { await post('/api/dws/refresh'); setTimeout(load, 4000) }}>รีเฟรช</Button>
        </div>
      }>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500">
            <th className="py-2 pr-4 font-medium">เครื่อง</th><th className="py-2 pr-4 font-medium">IP</th>
            <th className="py-2 pr-4 font-medium">สถานะ</th><th className="py-2 pr-4 font-medium">แพลนปัจจุบัน</th><th className="py-2 font-medium">ข้อความ</th></tr></thead>
          <tbody>
            {(d?.rows ?? []).map((r) => (
              <tr key={r.pc} className="border-t border-slate-100">
                <td className="py-2.5 pr-4 font-medium">{r.pc}</td><td className="py-2.5 pr-4 text-slate-500">{r.ip}:{r.port}</td>
                <td className="py-2.5 pr-4"><Badge tone={r.status === 'ONLINE' ? 'green' : r.status === 'ERROR' ? 'amber' : 'red'}>{r.status}</Badge></td>
                <td className="py-2.5 pr-4 font-semibold">{r.plan}</td>
                <td className="max-w-xs truncate py-2.5 text-xs text-slate-500" title={r.message}>{r.message}</td>
              </tr>
            ))}
            {d && d.rows.length === 0 && <tr><td colSpan={5} className="py-4 text-center text-slate-400">กำลังตรวจสถานะ…</td></tr>}
          </tbody>
        </table>
      </Card>

      <Card title="Log" action={<Button variant="ghost" icon={<Trash2 size={15} />} onClick={clear}>ล้าง</Button>}>
        <LiveLog lines={lines} height="h-64" />
      </Card>

      <Card title="ตั้งค่าเครื่อง DWS" action={
        <Button variant="primary" icon={<Save size={16} />} onClick={async () => {
          try {
            const r = await put<Dws>('/api/dws/clients', { clients })
            setD(r)
            setClients(r.clients)
            flash.show('ok', 'บันทึกแล้ว')
          } catch (e) {
            flash.show('err', (e as Error).message)
          }
        }}>บันทึก</Button>
      }>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-slate-500">
              <th className="py-2 pr-2 font-medium">เครื่อง</th><th className="py-2 pr-2 font-medium">IP</th><th className="py-2 pr-2 font-medium">Port</th>
              <th className="py-2 pr-2 font-medium">ดึงไฟล์</th><th className="py-2 pr-2 font-medium">โฟลเดอร์ไฟล์</th><th className="py-2 font-medium">ชื่อไฟล์</th></tr></thead>
            <tbody>
              {clients.map((c, i) => {
                const upd = (patch: Partial<Client>) => setClients(clients.map((x, j) => (j === i ? { ...x, ...patch } : x)))
                const pull = c.pull_folder !== undefined
                return (
                  <tr key={c.name} className="border-t border-slate-100">
                    <td className="py-1.5 pr-2 font-medium">{c.name}</td>
                    <td className="py-1.5 pr-2"><input className={`${inputClass} w-36 py-1.5`} value={c.ip} onChange={(e) => upd({ ip: e.target.value })} /></td>
                    <td className="py-1.5 pr-2"><input className={`${inputClass} w-20 py-1.5`} value={c.port} onChange={(e) => upd({ port: e.target.value })} /></td>
                    <td className="py-1.5 pr-2">{pull && <input type="checkbox" className="h-4 w-4 accent-brand-600" checked={!!c.pull_enabled} onChange={(e) => upd({ pull_enabled: e.target.checked })} />}</td>
                    <td className="py-1.5 pr-2">{pull && <input className={`${inputClass} py-1.5`} value={c.pull_folder ?? ''} onChange={(e) => upd({ pull_folder: e.target.value })} />}</td>
                    <td className="py-1.5">{pull && <input className={`${inputClass} py-1.5`} value={c.pull_file ?? ''} onChange={(e) => upd({ pull_file: e.target.value })} />}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}
