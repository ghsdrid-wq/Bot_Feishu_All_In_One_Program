// ธีมสว่าง/มืด: 'auto' = ตาม Windows/มือถือ  (จำไว้ใน localStorage ของแต่ละเครื่อง)
// index.html ใส่ class "dark" ก่อนหน้าเว็บโหลด (ไม่กระพริบขาว) — ไฟล์นี้ใช้ตอนกดเปลี่ยน
import { useEffect, useState } from 'react'

export type ThemeMode = 'auto' | 'light' | 'dark'
const KEY = 'theme'
const media = () => window.matchMedia('(prefers-color-scheme: dark)')

function load(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : 'auto'
  } catch {
    return 'auto'
  }
}

function apply(mode: ThemeMode) {
  const dark = mode === 'dark' || (mode === 'auto' && media().matches)
  document.documentElement.classList.toggle('dark', dark)
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#1a2130' : '#ffffff')
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(load)
  useEffect(() => {
    apply(mode)
    try {
      if (mode === 'auto') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, mode)
    } catch {
      /* ไม่มี storage ก็ใช้ได้ */
    }
    if (mode !== 'auto') return
    const m = media()
    const on = () => apply('auto')
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [mode])
  const next = () => setMode((m) => (m === 'auto' ? 'light' : m === 'light' ? 'dark' : 'auto'))
  return { mode, next }
}
