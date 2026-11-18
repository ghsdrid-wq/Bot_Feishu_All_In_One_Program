// วาดฟอร์มตั้งค่าจากรายการช่องที่หลังบ้านส่งมา (webui/schema.py)
import { useState } from 'react'
import { FolderOpen } from 'lucide-react'
import { pick, type Group, type Values } from './api'
import { Card, Field, Toggle, inputClass } from './ui'

export function SchemaForm({ groups, values, onChange }: {
  groups: Group[]
  values: Values
  onChange: (v: Values) => void
}) {
  const set = (section: string, key: string, val: string) =>
    onChange({ ...values, [section]: { ...(values[section] ?? {}), [key]: val } })

  return (
    <div className="space-y-5">
      {groups.map((g) => (
        <GroupCard key={g.id} group={g} values={values} set={set} />
      ))}
    </div>
  )
}

function GroupCard({ group, values, set }: { group: Group; values: Values; set: (s: string, k: string, v: string) => void }) {
  const [open, setOpen] = useState(!group.collapsed)
  const bools = group.fields.filter((f) => f.type === 'bool')
  const others = group.fields.filter((f) => f.type !== 'bool')
  return (
    <Card title={group.title} action={group.collapsed && (
      <button className="text-sm text-brand-700 hover:underline" onClick={() => setOpen(!open)}>{open ? 'ซ่อน' : 'แสดง'}</button>
    )}>
      {open ? (
        <>
          <div className="grid gap-5 md:grid-cols-2">
            {others.map((f) => {
              const v = values[f.section]?.[f.key] ?? ''
              const change = (val: string) => set(f.section, f.key, val)
              if (f.type === 'sections') {
                const chosen = new Set(v.split(',').map((x) => x.trim()).filter(Boolean))
                return (
                  <Field key={f.key} label={f.label} hint={f.hint}>
                    <div className="flex flex-wrap gap-2">
                      {f.options.map((o) => {
                        const on = chosen.has(o.value)
                        return (
                          <button key={o.value} type="button"
                            onClick={() => {
                              const next = new Set(chosen)
                              if (on) next.delete(o.value)
                              else next.add(o.value)
                              change(f.options.map((x) => x.value).filter((x) => next.has(x)).join(','))
                            }}
                            className={`rounded-full px-3 py-1 text-sm ring-1 transition ${on ? 'bg-brand-600 text-white ring-brand-600' : 'bg-white text-slate-600 ring-slate-300 hover:bg-slate-50'}`}>
                            {o.label}
                          </button>
                        )
                      })}
                    </div>
                  </Field>
                )
              }
              return (
                <Field key={f.key} label={f.label} hint={f.type === 'secret' ? `${f.hint ? f.hint + ' · ' : ''}ตอนนี้: ${v || 'ยังไม่ใส่'} — เว้นว่าง = ไม่เปลี่ยน` : f.hint}>
                  <div className="flex gap-2">
                    <input
                      className={inputClass}
                      type={f.type === 'secret' ? 'password' : f.type === 'number' ? 'number' : f.type === 'time' ? 'time' : 'text'}
                      value={f.type === 'secret' ? (v.startsWith('••••') ? '' : v) : v}
                      placeholder={f.type === 'secret' ? 'ใส่เมื่อต้องการเปลี่ยน' : f.default}
                      onChange={(e) => change(e.target.value)}
                    />
                    {(f.type === 'folder' || f.type === 'path') && (
                      <button type="button" title="เลือก" className="rounded-xl px-3 text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50"
                        onClick={async () => {
                          try {
                            const p = await pick(f.type === 'folder' ? 'folder' : 'file')
                            if (p) change(p)
                          } catch (e) {
                            alert((e as Error).message)
                          }
                        }}>
                        <FolderOpen size={17} />
                      </button>
                    )}
                  </div>
                </Field>
              )
            })}
          </div>
          {bools.length > 0 && (
            <div className={`grid gap-4 md:grid-cols-2 ${others.length ? 'mt-6 border-t border-slate-100 pt-5' : ''}`}>
              {bools.map((f) => (
                <Toggle key={f.key} label={f.label} hint={f.hint}
                  checked={(values[f.section]?.[f.key] ?? f.default) === 'true'}
                  onChange={(on) => set(f.section, f.key, on ? 'true' : 'false')} />
              ))}
            </div>
          )}
        </>
      ) : (
        <div className="text-sm text-slate-500">{group.fields.length} ช่อง — กด "แสดง" เพื่อแก้</div>
      )}
    </Card>
  )
}
