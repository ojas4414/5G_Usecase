import React from 'react';
import { ArrowRight, Activity, Cpu, Network, Zap } from 'lucide-react';

export function Hero() {
  const scrollToDashboard = () => {
    document.getElementById('dashboard')?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <section className="relative w-full min-h-screen pt-24 flex flex-col items-center justify-start overflow-hidden bg-black">
      
      {/* Background Animated Gradient Mesh / Particle glow simulation */}
      <div className="absolute inset-x-0 -top-40 h-[600px] bg-blue-900/20 filter blur-[100px] pointer-events-none rounded-full" />
      <div className="absolute top-40 -left-64 h-[400px] w-[400px] bg-cyan-700/10 filter blur-[120px] pointer-events-none rounded-full" />
      <div className="absolute top-20 -right-64 h-[400px] w-[400px] bg-[#0070f3]/10 filter blur-[120px] pointer-events-none rounded-full" />

      {/* Absolute Z-20 Foreground Context */}
      <div className="relative z-20 flex flex-col items-center text-center px-4 max-w-5xl mx-auto mt-24 space-y-10">
        
        {/* Announcement Pill */}
        <div className="inline-flex items-center gap-3 px-5 py-2 rounded-full border border-blue-500/20 bg-blue-950/30 backdrop-blur-md shadow-2xl">
          <div className="flex items-center justify-center w-2 h-2 rounded-full bg-cyan-400 shadow-[0_0_10px_rgba(0,240,255,0.8)] animate-pulse" />
          <span className="text-xs font-mono font-bold text-cyan-400 tracking-widest uppercase">
            EDGE-NYC-5G-01 • ONLINE
          </span>
        </div>

        {/* Headlines */}
        <div className="space-y-4">
          <h1 className="text-6xl md:text-[80px] font-black tracking-tighter leading-[1.1] font-sans">
            <span className="block text-white">5G Edge</span>
            <span className="block text-gradient-primary">Queue Analytics.</span>
          </h1>
          <p className="text-lg md:text-xl text-white/60 max-w-2xl mx-auto font-medium leading-relaxed mt-6">
            Real-time crowd detection and wait-time estimation powered by YOLOv8 inference at the edge — streamed over 5G WebSockets.
          </p>
        </div>

        {/* CTA Button */}
        <div className="p-1 rounded-full bg-white/5 border border-white/10 backdrop-blur-xl group cursor-pointer hover:bg-white/10 transition-all duration-300">
          <button 
            onClick={scrollToDashboard}
            className="flex items-center gap-3 bg-white text-black pl-8 pr-2 py-2 rounded-full font-bold text-base hover:scale-[1.02] transition-transform"
          >
            View Live Dashboard
            <div className="bg-gradient-primary p-2 rounded-full text-white group-hover:rotate-90 transition-transform duration-300">
              <ArrowRight className="w-4 h-4" />
            </div>
          </button>
        </div>

        {/* Feature Badges */}
        <div className="flex flex-wrap justify-center gap-4 mt-16 pt-12 border-t border-white/5">
          <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-black/40 border border-white/10 backdrop-blur-sm">
            <Cpu className="w-4 h-4 text-cyan-400" />
            <span className="text-sm font-mono text-white/80">YOLOv8 ONNX</span>
          </div>
          <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-black/40 border border-white/10 backdrop-blur-sm">
            <Network className="w-4 h-4 text-cyan-400" />
            <span className="text-sm font-mono text-white/80">WebSocket Telemetry</span>
          </div>
          <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-black/40 border border-white/10 backdrop-blur-sm">
            <Zap className="w-4 h-4 text-cyan-400" />
            <span className="text-sm font-mono text-white/80">&lt; 100ms Latency</span>
          </div>
          <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-black/40 border border-white/10 backdrop-blur-sm">
            <Activity className="w-4 h-4 text-cyan-400" />
            <span className="text-sm font-mono text-white/80">CUDA Accelerated</span>
          </div>
        </div>

      </div>

    </section>
  );
}
