import React from 'react'
import { Hero } from './components/Hero'
import { Dashboard } from './components/Dashboard'

function App() {
  return (
    <main className="min-h-screen bg-black selection:bg-cyan-400 selection:text-black">
      <Hero />
      
      {/* Decorative vertical spacer separating the bold minimalist hero from the technical interface */}
      <div className="w-full flex items-center justify-center -my-12 relative z-10">
        <div className="w-[1px] h-48 bg-gradient-to-b from-transparent via-cyan-500/50 to-transparent" />
      </div>

      <Dashboard />
      
      <footer className="w-full py-12 text-center text-white/30 text-[10px] font-mono tracking-wider border-t border-white/5 mt-12 bg-black">
         5G EDGE QUEUE ANALYTICS © {new Date().getFullYear()} // NODE: EDGE-NYC-5G-01
      </footer>
    </main>
  )
}

export default App
