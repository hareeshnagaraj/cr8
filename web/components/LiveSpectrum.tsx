"use client";

import {useEffect, useRef, useState} from "react";

import {usePlayer} from "./PlayerProvider";

const FULL_RATE_MS = 1000 / 30;
const REDUCED_RATE_MS = 500;

export function LiveSpectrum() {
  const {getAnalyser, playing} = usePlayer();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  // Start hidden so SSR and hydration never flash a canvas before the local
  // escape hatch can be read. Storage denial also disables the optional visual.
  const [disabled, setDisabled] = useState(true);

  useEffect(() => {
    try {
      setDisabled(localStorage.getItem("cr8:no-spectrum") === "1");
    } catch {
      setDisabled(true);
    }
  }, []);

  useEffect(() => {
    if (disabled || !playing) return;
    let active = true;
    getAnalyser()
      .then((node) => {
        if (active && node) setAnalyser(node);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [disabled, getAnalyser, playing]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (disabled || !playing || !analyser || !canvas) return;

    let active = true;
    let frame = 0;
    let lastDraw = 0;
    const samples = new Uint8Array(analyser.frequencyBinCount);
    let width = 0;
    let height = 0;
    let observer: ResizeObserver | null = null;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const interval = reduced ? REDUCED_RATE_MS : FULL_RATE_MS;
    const context = canvas.getContext("2d");
    if (!context) return;

    const resize = () => {
      const box = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      width = box.width;
      height = box.height;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    try {
      resize();
      if (typeof ResizeObserver !== "undefined") {
        observer = new ResizeObserver(resize);
        observer.observe(canvas);
      }
    } catch {
      return;
    }

    const draw = (now: number) => {
      frame = 0;
      if (!active || document.hidden) return;
      if (now - lastDraw >= interval) {
        lastDraw = now;
        try {
          analyser.getByteFrequencyData(samples);
          context.clearRect(0, 0, width, height);
          context.beginPath();
          for (let i = 0; i < samples.length; i += 1) {
            const x = samples.length > 1 ? (i / (samples.length - 1)) * width : 0;
            const y = height * 0.93 - (samples[i] / 255) * height * 0.86;
            if (i === 0) context.moveTo(x, y);
            else context.lineTo(x, y);
          }
          const color = getComputedStyle(canvas).getPropertyValue("--t2").trim()
            || "rgba(255,255,255,.6)";
          context.strokeStyle = color;
          context.lineWidth = 1;
          context.lineJoin = "round";
          context.lineCap = "round";
          context.shadowColor = color;
          context.shadowBlur = 3;
          context.stroke();
        } catch {
          // A broken canvas or analyser is allowed to remove this visual, never
          // to escape into React or the persistent player.
          active = false;
          return;
        }
      }
      frame = requestAnimationFrame(draw);
    };

    const start = () => {
      if (!active || document.hidden || frame) return;
      frame = requestAnimationFrame(draw);
    };
    const visibilityChanged = () => {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      if (!document.hidden) start();
    };

    document.addEventListener("visibilitychange", visibilityChanged);
    start();

    return () => {
      active = false;
      if (frame) cancelAnimationFrame(frame);
      observer?.disconnect();
      document.removeEventListener("visibilitychange", visibilityChanged);
      context.clearRect(0, 0, width, height);
    };
  }, [analyser, disabled, playing]);

  if (disabled || !analyser) return null;
  return <canvas ref={canvasRef} className="live-spectrum" aria-hidden="true" />;
}
