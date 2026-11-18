// Live Log ต่อช่อง (main / controller / jms) — ดึงบรรทัดใหม่ทุก 1.5 วินาที
import { useEffect, useRef, useState } from 'react'
import { del, get, type LogLine } from './api'

const levelStyle: Record<string, string> = {
  ERROR: 'text-rose-300',
  WARN: 'text-amber-300',
  WARNING: 'text-amber-300',
  SUCCESS: 'text-emerald-300',
  START: 'text-sky-300',
}

export function useLogs(channel: 'main' | 'controller' | 'jms') {
  const [lines, setLines] = useState<LogLine[]>([])
  const last = useRef(0)
  useEffect(() => {
    let alive = true
    last.current = 0
    setLines([])
    const tick = async () => {
      try {
        const fresh = await get<LogLine[]>(`/api/logs/${channel}?after=${last.current}`)
        if (alive && fresh.length) {
          last.current = fresh[fresh.length - 1].id
          setLines((prev) => [...prev, ...fresh].slice(-600))
        }
      } catch {
        /* เซิร์ฟเวอร์ยังไม่พร้อม */
      }
    }
    tick()
    const t = setInterval(tick, 1500)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [channel])
  const clear = async () => {
    await del(`/api/logs/${channel}`)
    setLines([])
  }
  return { lines, clear }
}

export function LiveLog({ lines, filter, height = 'h-80' }: { lines: LogLine[]; filter?: string; height?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const shown = filter ? lines.filter((l) => l.text.includes(filter)) : lines
  useEffect(() => {
    const el = box.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight
  }, [shown.length])
  return (
    <div ref={box} className={`${height} overflow-y-auto rounded-xl bg-slate-900 p-4 font-mono text-[13px] leading-6`}>
      {shown.length === 0 && <div className="text-slate-500">ยังไม่มีข้อความ</div>}
      {shown.map((l) => (
        <div key={l.id} className="flex gap-3">
          <span className="shrink-0 text-slate-500">{l.time}</span>
          <span className={`whitespace-pre-wrap break-all ${levelStyle[l.level] ?? 'text-slate-200'}`}>{l.text}</span>
        </div>
      ))}
    </div>
  )
}
