import { useEffect, useMemo, useState } from 'react'
import { Plus, Save, Search, X } from 'lucide-react'
import { get, post, put } from '../api'
import { Badge, Button, Card, PageHeader, inputClass, useFlash } from '../ui'

type Policy = { version: number; blocked_prefixes: string[]; exempt_codes: string[] }
type Kind = 'blocked_prefixes' | 'exempt_codes'
const meta: Record<Kind, { title: string; hint: string; tone: 'red' | 'green' }> = {
  blocked_prefixes: { title: 'หัวรหัสที่บล็อก', hint: 'บล็อกทุก USER ที่ขึ้นต้นด้วยค่าเหล่านี้', tone: 'red' },
  exempt_codes: { title: 'รหัสละเว้น', hint: 'อนุญาตเฉพาะรหัสเต็มที่ตรงกัน — สิทธิ์เหนือหัวรหัสที่บล็อก', tone: 'green' },
}

export function Policy() {
  const [p, setP] = useState<Policy | null>(null)
  const [saved, setSaved] = useState<Policy | null>(null)
  const flash = useFlash()
  useEffect(() => {
    get<Policy>('/api/policy').then((r) => {
      setP(r)
      setSaved(r)
    })
  }, [])
  if (!p) return null
  const dirty = JSON.stringify(p) !== JSON.stringify(saved)

  return (
    <>
      <PageHeader title="สิทธิ์รหัส" sub="นโยบายของ JMS USER Bot: ลำดับตัดสิน — รหัสละเว้น → หัวรหัสที่บล็อก → อนุญาต"
        actions={<Button variant="primary" icon={<Save size={16} />} disabled={!dirty} onClick={async () => {
          try {
            const r = await put<Policy>('/api/policy', p)
            setP(r)
            setSaved(r)
            flash.show('ok', 'บันทึกแล้ว')
          } catch (e) {
            flash.show('err', (e as Error).message)
          }
        }}>{dirty ? 'บันทึกการเปลี่ยนแปลง' : 'บันทึกแล้ว'}</Button>} />
      {flash.node}
      <div className="grid gap-6 lg:grid-cols-2">
        {(Object.keys(meta) as Kind[]).map((k) => (
          <PolicyList key={k} kind={k} items={p[k]} onChange={(items) => setP({ ...p, [k]: items })} />
        ))}
      </div>
    </>
  )
}

function PolicyList({ kind, items, onChange }: { kind: Kind; items: string[]; onChange: (v: string[]) => void }) {
  const [q, setQ] = useState('')
  const [bulk, setBulk] = useState('')
  const [msg, setMsg] = useState('')
  const shown = useMemo(() => items.filter((x) => x.includes(q.trim().toUpperCase())), [items, q])
  const m = meta[kind]

  const add = async () => {
    const r = await post<{ valid: string[]; invalid: string[]; duplicates: string[] }>('/api/policy/parse', { kind, text: bulk })
    const merged = [...items]
    let added = 0
    for (const v of r.valid) if (!merged.includes(v)) { merged.push(v); added++ }
    onChange(merged)
    setBulk('')
    setMsg(`เพิ่ม ${added}${r.invalid.length ? ` · รูปแบบไม่ถูกต้อง ${r.invalid.length}: ${r.invalid.slice(0, 5).join(', ')}` : ''}${r.valid.length - added ? ` · มีอยู่แล้ว ${r.valid.length - added}` : ''}`)
  }

  return (
    <Card title={<span className="flex items-center gap-2">{m.title} <Badge tone={m.tone}>{items.length}</Badge></span>}>
      <p className="mb-4 text-xs text-slate-500">{m.hint}</p>
      <div className="flex gap-2">
        <textarea className={`${inputClass} h-20`} value={bulk} onChange={(e) => setBulk(e.target.value)}
          placeholder="วางได้หลายรายการ คั่นด้วยเว้นวรรค / จุลภาค / ขึ้นบรรทัดใหม่" />
        <Button icon={<Plus size={16} />} disabled={!bulk.trim()} onClick={add}>เพิ่ม</Button>
      </div>
      {msg && <div className="mt-2 text-xs text-slate-600">{msg}</div>}
      <div className="relative mt-4">
        <Search size={15} className="absolute top-3 left-3 text-slate-400" />
        <input className={`${inputClass} pl-9`} value={q} onChange={(e) => setQ(e.target.value)} placeholder="ค้นหา" />
      </div>
      <div className="mt-3 flex max-h-80 flex-wrap gap-2 overflow-y-auto">
        {shown.map((x) => (
          <span key={x} className="inline-flex items-center gap-1 rounded-lg bg-slate-100 py-1 pr-1 pl-2.5 font-mono text-sm">
            {x}
            <button className="rounded p-0.5 text-slate-400 hover:bg-white hover:text-brand-600" onClick={() => onChange(items.filter((y) => y !== x))}><X size={14} /></button>
          </span>
        ))}
        {shown.length === 0 && <span className="text-sm text-slate-400">ไม่มีรายการ</span>}
      </div>
    </Card>
  )
}
