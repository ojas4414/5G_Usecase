import React, { useEffect, useRef, useState } from 'react';
import { io, Socket } from 'socket.io-client';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, Title, Tooltip, Legend
} from 'chart.js';
import { Activity, Zap } from 'lucide-react';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

export function Dashboard() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [metrics, setMetrics] = useState({ people: 0, queued: 0, waitSec: 0, density: 0 });
  const [densityHistory, setDensityHistory] = useState<number[]>(Array(20).fill(0));
  
  useEffect(() => {
    // Connect to Flask backend
    const ioConnection = io("http://localhost:5000");

    let isDrawing = false;
    let current_ai_metadata: any = null;
    const imgObj = new Image();

    ioConnection.on('ai_metadata', (data) => {
        current_ai_metadata = data;
    });

    imgObj.onload = function() {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctxCanvas = canvas.getContext('2d');
        if (!ctxCanvas) return;

        if (canvas.width !== imgObj.width) {
            canvas.width = imgObj.width;
            canvas.height = imgObj.height;
        }
        ctxCanvas.drawImage(imgObj, 0, 0, canvas.width, canvas.height);
        
        // Apply Client-Side Overlay
        if (current_ai_metadata) {
            const roi = current_ai_metadata.roi;
            if (roi && roi.length > 0) {
                ctxCanvas.beginPath();
                ctxCanvas.moveTo(roi[0][0], roi[0][1]);
                for(let i=1; i<roi.length; i++) ctxCanvas.lineTo(roi[i][0], roi[i][1]);
                ctxCanvas.closePath();
                ctxCanvas.lineWidth = 3;
                ctxCanvas.strokeStyle = '#00f0ff'; // Cyan for ROI
                ctxCanvas.stroke();
            }
            
            if (current_ai_metadata.boxes) {
                current_ai_metadata.boxes.forEach((b: any) => {
                    ctxCanvas.beginPath();
                    ctxCanvas.rect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
                    ctxCanvas.lineWidth = 2;
                    ctxCanvas.strokeStyle = b.status === 'queued' ? '#ff3366' : '#00f0ff';
                    ctxCanvas.stroke();
                    
                    ctxCanvas.fillStyle = b.status === 'queued' ? '#ff3366' : '#00f0ff';
                    ctxCanvas.font = "bold 14px monospace";
                    const txt = b.status === 'queued' ? `ID: ${b.id} | QUEUED: ${b.time}s` : `ID: ${b.id} | Wait: ${b.time}s`;
                    ctxCanvas.fillText(txt, b.x1, b.y1 - 8);
                    
                    ctxCanvas.beginPath();
                    ctxCanvas.arc(b.cx, b.cy, 5, 0, 2 * Math.PI);
                    ctxCanvas.fillStyle = "#ffffff";
                    ctxCanvas.fill();
                });
            }
        }
        isDrawing = false;
    };

    ioConnection.on('video_frame', (data) => {
        if (isDrawing) return; 
        isDrawing = true;
        imgObj.src = "data:image/jpeg;base64," + data.image;
    });

    ioConnection.on('telemetry_stream', async (data) => {
       const buffer = await new Blob([data]).arrayBuffer();
       const view = new DataView(buffer);
       const tDetect = view.getInt32(0, false);
       const tQueue = view.getInt32(4, false);
       const den = view.getFloat32(8, false);
       const extWait = view.getFloat32(12, false);

       setMetrics({ people: tDetect, queued: tQueue, density: den, waitSec: extWait });
       setDensityHistory(prev => {
           const next = [...prev, den];
           next.shift();
           return next;
       });
    });

    return () => {
        ioConnection.disconnect();
    };
  }, []);

  const chartData = {
      labels: Array(20).fill(''),
      datasets: [{
          label: 'Crowd Density (People / SQM)',
          data: densityHistory,
          borderColor: '#00f0ff',
          backgroundColor: 'rgba(0, 240, 255, 0.1)',
          fill: true,
          tension: 0.4,
          pointRadius: 0,
      }]
  };

  const chartOptions = {
    animation: { duration: 100 },
    scales: { 
        y: { beginAtZero: true, suggestedMax: 1.0, grid: { color: 'rgba(255,255,255,0.05)' }, border: { dash: [4, 4] }, ticks: { color: 'rgba(255,255,255,0.5)', font: { family: 'monospace' } } },
        x: { grid: { display: false }, border: { display: false } }
    },
    plugins: { legend: { display: false } },
    maintainAspectRatio: false
  };

  return (
    <section id="dashboard" className="relative w-full max-w-[1600px] mx-auto py-12 px-6 z-20 min-h-screen">
      
      {/* Dashboard Header */}
      <div className="flex items-center justify-between mb-8 pb-4 border-b border-white/10">
          <div>
            <h2 className="text-xl md:text-2xl font-bold text-white flex items-center gap-3 tracking-wide">
              <Activity className="w-5 h-5 text-cyan-400" />
              5G EDGE INFERENCE
            </h2>
            <p className="text-white/50 text-sm font-mono mt-1">Real-time object tracking utilizing decentralized hardware processing logic.</p>
          </div>
          <div className="hidden md:flex items-center gap-4 text-sm font-mono text-white/40">
            <span>SYS: OPTIMAL</span>
            <span>|</span>
            <span>PORT: 5000</span>
          </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Left Panel: Live Video Canvas (2/3 width) */}
        <div className="lg:col-span-2 relative bg-black border border-white/10 rounded-xl overflow-hidden shadow-[0_0_30px_rgba(0,0,0,0.8)]">
            <div className="absolute top-4 left-4 z-30 px-3 py-1.5 bg-black/80 backdrop-blur-md rounded border border-white/5 flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                <span className="text-[10px] font-mono font-bold tracking-widest text-red-500">LIVE VIDEO</span>
            </div>
            
            {/* Corner Bracket Accents */}
            <div className="absolute top-0 left-0 w-8 h-8 border-t-2 border-l-2 border-white/20 z-20 m-4 rounded-tl-sm pointer-events-none" />
            <div className="absolute top-0 right-0 w-8 h-8 border-t-2 border-r-2 border-white/20 z-20 m-4 rounded-tr-sm pointer-events-none" />
            <div className="absolute bottom-0 left-0 w-8 h-8 border-b-2 border-l-2 border-white/20 z-20 m-4 rounded-bl-sm pointer-events-none" />
            <div className="absolute bottom-0 right-0 w-8 h-8 border-b-2 border-r-2 border-white/20 z-20 m-4 rounded-br-sm pointer-events-none" />

            <canvas ref={canvasRef} className="w-full h-full bg-[#050505] object-cover mix-blend-screen" style={{ minHeight: '500px' }} />
        </div>

        {/* Right Panel: Telemetry Widgets (1/3 width) */}
        <div className="flex flex-col gap-6">
            
            {/* Card 1: Capacity */}
            <div className="bg-white/5 border border-white/10 backdrop-blur-xl rounded-xl p-6 flex flex-col justify-center h-[140px] relative overflow-hidden group hover:bg-white/10 transition-colors">
                <div className="absolute top-0 left-0 w-1 h-full bg-cyan-500" />
                <div className="flex justify-between items-end">
                  <div>
                      <h3 className="text-white/40 text-xs font-mono font-semibold uppercase tracking-widest">Line Capacity</h3>
                      <div className="text-5xl font-black text-white mt-1 font-sans">{metrics.queued}</div>
                  </div>
                  <div className="text-right">
                      <h3 className="text-white/40 text-[10px] font-mono uppercase tracking-widest">Total People</h3>
                      <div className="text-xl font-bold text-cyan-400 mt-1 font-mono">{metrics.people}</div>
                  </div>
                </div>
            </div>

            {/* Card 2: ETA Glow */}
            <div className="bg-gradient-primary p-[1px] rounded-xl shadow-[0_0_30px_rgba(0,112,243,0.3)] h-[180px]">
                <div className="bg-black/90 backdrop-blur-2xl rounded-[11px] h-full p-6 text-center flex flex-col items-center justify-center relative">
                    <div className="absolute top-4 right-4 animate-pulse">
                      <Zap className="w-4 h-4 text-cyan-400/50" />
                    </div>
                    <h3 className="text-cyan-400/80 text-xs font-mono font-bold uppercase tracking-widest mb-3">Estimated Queue Wait</h3>
                    <div className="text-6xl md:text-7xl font-black text-transparent bg-clip-text bg-gradient-to-r from-white to-cyan-200 tracking-tighter">
                      {metrics.waitSec.toFixed(1)} <span className="text-2xl text-cyan-500/50">s</span>
                    </div>
                </div>
            </div>

            {/* Card 3: Density Chart */}
            <div className="bg-white/5 border border-white/10 backdrop-blur-xl rounded-xl p-6 flex-1 flex flex-col min-h-[220px]">
                <h3 className="text-white/40 text-xs font-mono font-semibold uppercase tracking-widest mb-4 flex items-center justify-between">
                  Density Timeline
                  <span className="text-cyan-400/60 font-mono text-[10px]">P/SQM</span>
                </h3>
                <div className="flex-1 w-full relative">
                    <Line data={chartData} options={chartOptions as any} />
                </div>
            </div>

        </div>
      </div>
    </section>
  );
}
