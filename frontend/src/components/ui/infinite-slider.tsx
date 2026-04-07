import { motion } from "motion/react";
import React from "react";

export function InfiniteSlider({ logos }: { logos: string[] }) {
  return (
    <div className="relative w-full overflow-hidden bg-black/20 backdrop-blur-sm border-t border-white/5 py-8 flex items-center">
      <div className="absolute left-0 top-0 bottom-0 w-24 bg-gradient-to-r from-[#010101] to-transparent z-10 pointer-events-none" />
      <div className="absolute right-0 top-0 bottom-0 w-24 bg-gradient-to-l from-[#010101] to-transparent z-10 pointer-events-none" />
      
      <div className="hidden md:flex flex-col items-start px-8 pr-12 border-r border-white/10 z-20">
        <span className="text-white/60 text-sm font-semibold tracking-wider uppercase whitespace-nowrap">
          Powering the
        </span>
        <span className="text-white/90 text-sm font-bold tracking-wider uppercase whitespace-nowrap">
          best teams
        </span>
      </div>

      {/* Infinite Marquee Animation Container */}
      <div className="flex flex-1 overflow-hidden">
        <motion.div
          className="flex items-center gap-16 px-8 whitespace-nowrap"
          animate={{ x: ["0%", "-50%"] }}
          transition={{ duration: 15, ease: "linear", repeat: Infinity }}
        >
          {/* Double the array to allow for seamless infinite looping */}
          {[...logos, ...logos].map((src, idx) => (
            <img 
              key={idx} 
              src={src} 
              alt="Brand Logo" 
              className="h-8 md:h-10 opacity-70 brightness-0 invert pointer-events-none"
            />
          ))}
        </motion.div>
      </div>
    </div>
  );
}
