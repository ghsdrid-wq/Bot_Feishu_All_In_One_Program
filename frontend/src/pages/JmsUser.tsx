import { useState } from 'react'
import { Power, Search, Trash2 } from 'lucide-react'
import { get, post, type EngineState } from '../api'
import type { Go } from '../App'
import { LiveLog, useLogs } from '../LiveLog'
import { Badge, Button, Card, PageHeader, inputClass } from '../ui'

export function JmsUser({ st, refresh, go }: { st: EngineState | null; refresh: () => void; go: Go }) {
  const { lines, clear } = useLogs('jms')
  const [code, setCode] = useState('')
  const [check, setCheck] = useState<{ code: string; allowed: boolean; reason: string } | null>(null)

  const bot = async (on: boolean) => {
    await post(`/api/bot/jms/${on ? 'start' : 'stop'}`)
    refresh()
  }
  return (
    <>
      <PageHeader title="JMS USER" sub="รับคำสั่งภาษาไทยผ่าน Feishu แล้ว reset รหัส / ปลดล็อก user บนระบบ JMS"
        actions={st?.jms_running
          ? <Button icon={<Power size={16} />} onClick={() => bot(false)}>หยุดบอท JMS</Button>
          : <Button variant="primary" icon={<Power size={16} />} onClick={() => bot(true)}>เริ่มบอท JMS</Button>} />

      <div className="grid gap-4 md:grid-cols-3">
        <div className="rounded-2xl bg-surface p-4 shadow-sm ring-1 ring-slate-200">
          <div className="text-sm text-slate-500">บอท JMS USER</div>
          <div className="mt-1 text-xl font-semibold">{st?.jms_running ? <span className="text-emerald-600">ONLINE</span> : <span className="text-slate-400">OFFLINE</span>}</div>
        </div>
        <div className="rounded-2xl bg-surface p-4 shadow-sm ring-1 ring-slate-200">
          <div className="text-sm text-slate-500">รับข้อความจาก Feishu (webhook)</div>
          <div className="mt-1 text-xl font-semibold">{st?.bot_running ? <span className="text-emerald-600">ONLINE</span> : <span className="text-slate-400">OFFLINE</span>}</div>
          {!st?.bot_running && <div className="mt-1 text-xs text-slate-500">ต้องเปิดบอท Feishu ที่หน้า DWS PLAN ด้วย</div>}
        </div>
        <div className="rounded-2xl bg-surface p-4 shadow-sm ring-1 ring-slate-200">
          <div className="text-sm text-slate-500">JMS Token</div>
          <div className="mt-1 text-xl font-semibold">{st?.jms_token_set ? <span className="text-emerald-600">พร้อม</span> : <button className="text-brand-700 hover:underline" onClick={() => go('settings')}>ยังไม่ใส่</button>}</div>
        </div>
      </div>

      <Card title="ตรวจสิทธิ์รหัสพนักงาน">
        <div className="flex gap-2">
          <input className={`${inputClass} max-w-xs`} value={code} onChange={(e) => setCode(e.target.value)} placeholder="รหัสพนักงาน" />
          <Button icon={<Search size={16} />} disabled={!code.trim()} onClick={async () => setCheck(await get(`/api/policy/check/${encodeURIComponent(code.trim())}`))}>ตรวจ</Button>
          {check && (
            <span className="flex items-center gap-2 text-sm">
              {check.code}: {check.allowed ? <Badge tone="green">อนุญาต</Badge> : <Badge tone="red">บล็อก</Badge>}
              <span className="text-slate-500">{check.reason}</span>
            </span>
          )}
          <span className="flex-1" />
          <Button variant="ghost" onClick={() => go('policy')}>จัดการสิทธิ์รหัส</Button>
        </div>
      </Card>

      <Card title="Log" action={<Button variant="ghost" icon={<Trash2 size={15} />} onClick={clear}>ล้าง</Button>}>
        <LiveLog lines={lines} height="h-[480px]" />
      </Card>
    </>
  )
}
