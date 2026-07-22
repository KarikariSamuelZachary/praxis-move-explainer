'use client';

import { useEffect, useRef } from 'react';

type Mote = {
  x: number;
  y: number;
  r: number;
  speed: number;
  wobble: number;
  wobbleSpeed: number;
  alpha: number;
};

export default function DustCanvas({ className = '' }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let rafId = 0;
    let running = true;
    let motes: Mote[] = [];
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));

      const count = Math.min(70, Math.floor((rect.width * rect.height) / 22000));
      motes = Array.from({ length: count }, () => ({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        r: (0.6 + Math.random() * 1.5) * dpr,
        speed: (0.06 + Math.random() * 0.2) * dpr,
        wobble: Math.random() * Math.PI * 2,
        wobbleSpeed: 0.002 + Math.random() * 0.006,
        alpha: 0.08 + Math.random() * 0.22,
      }));
    };

    const tick = () => {
      if (!running) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (const mote of motes) {
        mote.y -= mote.speed;
        mote.wobble += mote.wobbleSpeed;
        const x = mote.x + Math.sin(mote.wobble) * 14 * dpr;

        if (mote.y < -4) {
          mote.y = canvas.height + 4;
          mote.x = Math.random() * canvas.width;
        }

        ctx.beginPath();
        ctx.arc(x, mote.y, mote.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(239, 217, 167, ${mote.alpha})`;
        ctx.fill();
      }
      rafId = requestAnimationFrame(tick);
    };

    const observer = new IntersectionObserver(
      ([entry]) => {
        const shouldRun = entry.isIntersecting;
        if (shouldRun && !running) {
          running = true;
          rafId = requestAnimationFrame(tick);
        } else if (!shouldRun && running) {
          running = false;
          cancelAnimationFrame(rafId);
        }
      },
      { threshold: 0 }
    );

    resize();
    observer.observe(canvas);
    window.addEventListener('resize', resize);
    rafId = requestAnimationFrame(tick);

    return () => {
      running = false;
      cancelAnimationFrame(rafId);
      observer.disconnect();
      window.removeEventListener('resize', resize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className={`pointer-events-none absolute inset-0 h-full w-full ${className}`}
    />
  );
}
