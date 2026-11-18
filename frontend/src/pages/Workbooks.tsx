import { useEffect, useState } from 'react'
import { FilePlus2, FolderOpen, Plus, Trash2 } from 'lucide-react'
import { del, get, pick, post, put } from '../api'
import { Badge, Button, Card, PageHeader, Toggle, inputClass, useFlash } from '../ui'

type Export = { name: string; enabled: boolean; send_enabled: boolean; delete_by_start: boolean; sheet: string; range: string; file: string; caption: string }
type Workbook = { key: string; name: string; path: string; exists: boolean; enabled: boolean; send_excel: boolean; group: string; exports: Export[] }

export function Workbooks() {
  const [list, setList] = useState<Workbook[] | null>(null)
  const [newPath, setNewPath] = useState('')
  const flash = useFlash()
  useEffect(() => {
    get<Workbook[]>('/api/workbooks').then(setList)
  }, [])

  const call = async (p: Promise<Workbook[]>, ok?: string) => {
    try {
      setList(await p)
      if (ok) flash.show('ok', ok)
    } catch (e) {
      flash.show('err', (e as Error).message)
    }
  }

  return (
    <>
      <PageHeader title="ไฟล์ Excel" sub="ไฟล์ Excel ที่แคปตารางเป็นรูปส่งเข้า Feishu (ขั้นตอน Excel Image) — แก้แล้วบันทึกทันที" />
      {flash.node}
      <Card title="เพิ่มไฟล์ Excel">
        <div className="flex gap-2">
          <input className={inputClass} value={newPath} onChange={(e) => setNewPath(e.target.value)} placeholder="ที่อยู่ไฟล์บนเครื่องรัน เช่น C:\Excel\1AUTOPACKING_BOT.xlsx" />
          <Button icon={<FolderOpen size={16} />} onClick={async () => {
            try {
              const p = await pick('file')
              if (p) setNewPath(p)
            } catch (e) {
              flash.show('err', (e as Error).message)
            }
          }}>เลือก</Button>
          <Button variant="primary" icon={<FilePlus2 size={16} />} disabled={!newPath.trim()}
            onClick={() => call(post('/api/workbooks', { path: newPath }), 'เพิ่มไฟล์แล้ว').then(() => setNewPath(''))}>เพิ่ม</Button>
        </div>
      </Card>

      {list?.map((wb) => <WorkbookCard key={wb.key} wb={wb} call={call} />)}
      {list && list.length === 0 && <Card><div className="py-4 text-center text-sm text-slate-500">ยังไม่มีไฟล์ Excel</div></Card>}
    </>
  )
}

function WorkbookCard({ wb, call }: { wb: Workbook; call: (p: Promise<Workbook[]>, ok?: string) => void }) {
  const [group, setGroup] = useState(wb.group)
  const edit = (body: object) => call(put(`/api/workbooks/${wb.key}`, body))
  return (
    <Card title={
      <span className="flex items-center gap-2">
        {wb.name}
        {!wb.exists && <Badge tone="red">ไม่พบไฟล์</Badge>}
        {!wb.enabled && <Badge tone="slate">ปิด</Badge>}
      </span>
    } action={
      <Button variant="ghost" icon={<Trash2 size={15} />} onClick={() => {
        if (confirm(`ลบ ${wb.name} และรายการแคปทั้งหมดของไฟล์นี้ใช่ไหม?`)) call(del(`/api/workbooks/${wb.key}`), 'ลบแล้ว')
      }}>ลบไฟล์</Button>
    }>
      <div className="mb-4 truncate text-xs text-slate-500" title={wb.path}>{wb.path}</div>
      <div className="flex flex-wrap items-end gap-6">
        <Toggle checked={wb.enabled} onChange={(v) => edit({ enabled: v })} label="ใช้ไฟล์นี้" />
        <Toggle checked={wb.send_excel} onChange={(v) => edit({ send_excel: v })} label="ส่งไฟล์ Excel เข้า Feishu ด้วย" />
        <label className="block w-48">
          <span className="mb-1 block text-xs font-medium text-slate-600">กลุ่มการ์ด</span>
          <input className={inputClass} value={group} placeholder="ว่าง = กลุ่มหลัก" onChange={(e) => setGroup(e.target.value)}
            onBlur={() => group !== wb.group && edit({ group })} />
        </label>
      </div>

      <div className="mt-5 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="py-2 pr-2 font-medium">#</th><th className="py-2 pr-2 font-medium">ใช้</th>
              <th className="py-2 pr-2 font-medium">Sheet</th><th className="py-2 pr-2 font-medium">ช่วง</th>
              <th className="py-2 pr-2 font-medium">ไฟล์รูป</th><th className="py-2 pr-2 font-medium">หัวตาราง</th>
              <th className="py-2 pr-2 font-medium">ส่ง</th><th className="py-2 pr-2 font-medium" title="ลบคอลัมน์ก่อนชั่วโมงเริ่ม">ตัดคอลัมน์</th><th />
            </tr>
          </thead>
          <tbody>
            {wb.exports.map((ex) => <ExportRow key={ex.name} ex={ex} call={call} />)}
          </tbody>
        </table>
      </div>
      <div className="mt-3">
        <Button variant="ghost" icon={<Plus size={16} />} onClick={() => call(post(`/api/workbooks/${wb.key}/exports`))}>เพิ่ม Sheet ที่แคป</Button>
      </div>
    </Card>
  )
}

function ExportRow({ ex, call }: { ex: Export; call: (p: Promise<Workbook[]>) => void }) {
  const [v, setV] = useState(ex)
  const save = (body: object) => call(put(`/api/exports/${ex.name}`, body))
  const text = (k: 'sheet' | 'range' | 'file' | 'caption', w: string) => (
    <td className="py-1.5 pr-2">
      <input className={`${inputClass} py-1.5 ${w}`} value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })}
        onBlur={() => v[k] !== ex[k] && save({ [k]: v[k] })} />
    </td>
  )
  return (
    <tr className={`border-t border-slate-100 ${ex.enabled ? '' : 'opacity-50'}`}>
      <td className="py-1.5 pr-2 text-slate-400">{ex.name}</td>
      <td className="py-1.5 pr-2"><input type="checkbox" className="h-4 w-4 accent-brand-600" checked={ex.enabled} onChange={(e) => save({ enabled: e.target.checked })} /></td>
      {text('sheet', 'w-40')}
      {text('range', 'w-28')}
      {text('file', 'w-40')}
      {text('caption', 'w-40')}
      <td className="py-1.5 pr-2"><input type="checkbox" className="h-4 w-4 accent-brand-600" checked={ex.send_enabled} onChange={(e) => save({ send_enabled: e.target.checked })} /></td>
      <td className="py-1.5 pr-2"><input type="checkbox" className="h-4 w-4 accent-brand-600" checked={ex.delete_by_start} onChange={(e) => save({ delete_by_start: e.target.checked })} /></td>
      <td className="py-1.5 text-right">
        <button className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-brand-600" title="ลบ"
          onClick={() => confirm(`ลบรายการ ${ex.name} ใช่ไหม?`) && call(del(`/api/exports/${ex.name}`))}><Trash2 size={15} /></button>
      </td>
    </tr>
  )
}
