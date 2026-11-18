import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// build ลง webui/static ให้ FastAPI เสิร์ฟ / ตอนพัฒนา (npm run dev) ส่ง /api ไปเซิร์ฟเวอร์ Python พอร์ต 6300
// ดูข้อมูลจริงของเครื่องรัน: set API=http://10.30.32.163:6300 แล้ว npm run dev (ระวังปุ่มสั่งงานทำงานจริง)
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: '../webui/static', emptyOutDir: true },
  server: { proxy: { '/api': process.env.API ?? 'http://127.0.0.1:6300' } },
})
