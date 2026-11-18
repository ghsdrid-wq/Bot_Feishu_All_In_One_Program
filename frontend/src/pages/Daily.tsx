import { useEffect, useState } from 'react'
import { Eye, Loader2, Save, Send } from 'lucide-react'
import { get, post, put, type EngineState, type Group, type Values } from '../api'
import { LiveLog, useLogs } from '../LiveLog'
import { SchemaForm } from '../SchemaForm'
import { Button, Card, PageHeader, inputClass, useFlash } from '../ui'

type Daily = { groups: Group[]; values: Values; busy: boolean; status: string; last_sent: string; range: { start: string; end: string } }
const toLocal = (s: string) => s.replace(' ', 'T').slice(0, 16)

export function Daily({ st }: { st: EngineState | null }) {
  const [d, setD] = useState<Daily | null>(null)
  const [values, setValues] = useState<Values>({})
  const [range, setRange] = useState({ start: '', end: '' })
  const flash = useFlash()
  const { lines } = useLogs('main')

  useEffect(() => {
    get<Daily>('/api/daily').then((r) => {
      setD(r)
      setValues(r.values)
      setRange({ start: toLocal(r.range.start), end: toLocal(r.range.end) })
    })
  }, [])

  const run = async (send: boolean) => {
    if (send && !confirm(`ส่งสรุปยอดช่วง ${range.start.replace('T', ' ')} → ${range.end.replace('T', ' ')} เข้า Feishu ใช่ไหม?`)) return
    try {
      await post('/api/daily/run', { start: range.start, end: range.end, send })
      flash.show('ok', send ? 'เริ่มส่งสรุปแล้ว' : 'เริ่มสร้างตัวอย่างแล้ว (ไม่ส่ง)')
    } catch (e) {
      flash.show('err', (e as Error).message)
    }
  }
  const save = async () => {
    try {
      const r = await put<Daily>('/api/daily', { values })
      setValues(r.values)
      flash.show('ok', 'บันทึกแล้ว')
    } catch (e) {
      flash.show('err', (e as Error).message)
    }
  }

  return (
    <>
      <PageHeader title="สรุปรายวัน" sub="สรุปยอดประจำวันแบบรายงาน manual + ไฟล์ข้อมูลดิบ — ส่งอัตโนมัติวันละครั้งขณะเปิด Auto"
        actions={<Button variant="primary" icon={<Save size={16} />} onClick={save}>บันทึกการตั้งค่า</Button>} />
      {flash.node}
      <Card title="สรุปตามช่วงที่เลือก">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block"><span className="mb-1.5 block text-sm font-medium text-slate-700">เริ่ม</span>
            <input type="datetime-local" className={inputClass} value={range.start} onChange={(e) => setRange({ ...range, start: e.target.value })} /></label>
          <label className="block"><span className="mb-1.5 block text-sm font-medium text-slate-700">ถึง</span>
            <input type="datetime-local" className={inputClass} value={range.end} onChange={(e) => setRange({ ...range, end: e.target.value })} /></label>
          {d && <button className="mb-2 text-sm text-brand-700 hover:underline" onClick={() => setRange({ start: toLocal(d.range.start), end: toLocal(d.range.end) })}>รอบล่าสุด</button>}
          <span className="flex-1" />
          <Button icon={<Eye size={16} />} disabled={st?.ds_busy} onClick={() => run(false)}>สร้างตัวอย่าง</Button>
          <Button variant="primary" icon={<Send size={16} />} disabled={st?.ds_busy} onClick={() => run(true)}>ส่งสรุปตอนนี้</Button>
        </div>
        <div className="mt-4 flex items-center gap-2 text-sm text-slate-600">
          {st?.ds_busy && <Loader2 size={16} className="animate-spin text-brand-600" />}
          {st?.ds_status || 'ยังไม่ได้สั่งงาน'}
          <span className="ml-auto text-slate-500">{d?.last_sent ? `ส่งอัตโนมัติล่าสุด: รอบงานวันที่ ${d.last_sent}` : 'ยังไม่เคยส่งอัตโนมัติ'}</span>
        </div>
      </Card>
      <Card title="ความคืบหน้า">
        <LiveLog lines={lines} filter="aily" height="h-48" />
      </Card>
      {d && <SchemaForm groups={d.groups} values={values} onChange={setValues} />}
    </>
  )
}
