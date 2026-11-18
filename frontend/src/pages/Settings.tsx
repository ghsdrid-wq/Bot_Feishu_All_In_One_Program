import { useEffect, useState } from 'react'
import { Save } from 'lucide-react'
import { get, put, type Group, type Values } from '../api'
import { SchemaForm } from '../SchemaForm'
import { Button, PageHeader, useFlash } from '../ui'

export function Settings() {
  const [groups, setGroups] = useState<Group[]>([])
  const [values, setValues] = useState<Values>({})
  const [saved, setSaved] = useState('')
  const flash = useFlash()
  useEffect(() => {
    get<{ groups: Group[]; values: Values }>('/api/settings').then((r) => {
      setGroups(r.groups)
      setValues(r.values)
      setSaved(JSON.stringify(r.values))
    })
  }, [])
  const dirty = JSON.stringify(values) !== saved
  const save = async () => {
    try {
      const r = await put<{ values: Values }>('/api/settings', { values })
      setValues(r.values)
      setSaved(JSON.stringify(r.values))
      flash.show('ok', 'บันทึกแล้ว')
    } catch (e) {
      flash.show('err', (e as Error).message)
    }
  }
  return (
    <>
      <PageHeader title="ตั้งค่า" sub="ใช้ config.ini ไฟล์เดียวกับโปรแกรมเดิม"
        actions={<Button variant="primary" icon={<Save size={16} />} disabled={!dirty} onClick={save}>{dirty ? 'บันทึกการเปลี่ยนแปลง' : 'บันทึกแล้ว'}</Button>} />
      {flash.node}
      <SchemaForm groups={groups} values={values} onChange={setValues} />
      {dirty && (
        <div className="sticky bottom-4 flex justify-end">
          <Button variant="primary" icon={<Save size={16} />} onClick={save}>บันทึกการเปลี่ยนแปลง</Button>
        </div>
      )}
    </>
  )
}
