import { useLayoutEffect, useRef, useState } from "react";
import "./browserPointer.css";

export interface BrowserPointerState {
  x: number;
  y: number;
  width: number;
  height: number;
  click_id: number;
  click_x: number;
  click_y: number;
}

/** A new vector mark driven by actual agent mouse events, never the OS cursor. */
export function BrowserPointer({ pointer }: { pointer?: BrowserPointerState }) {
  const host = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const el = host.current;
    if (!el) return;
    const measure = () => setSize({ width: el.clientWidth, height: el.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const scale = pointer ? Math.min(size.width / pointer.width, size.height / pointer.height) : 0;
  const point = (x: number, y: number) => ({
    left: (size.width - pointer!.width * scale) / 2 + x * scale,
    top: (size.height - pointer!.height * scale) / 2 + y * scale,
  });
  return <div ref={host} className="jarvis-browser-pointer-layer" aria-hidden="true" data-testid="browser-pointer-layer">
    {pointer && scale > 0 && <>
      {pointer.click_id > 0 && <span key={pointer.click_id} className="jarvis-browser-click"
        style={point(pointer.click_x, pointer.click_y)} data-testid="browser-click-mark" />}
      <span className="jarvis-browser-pointer" style={point(pointer.x, pointer.y)} data-testid="browser-agent-pointer">
        <svg width="23" height="28" viewBox="0 0 23 28" fill="none">
          <path d="M3 2.5C2.1 1.8 1.3 2.4 1.5 3.5L4.7 23.1C4.9 24.4 6.1 24.7 6.9 23.6L11.1 17.7L18.4 16.7C19.8 16.5 20.2 15.4 19.1 14.5L3 2.5Z"
            fill="var(--browser-pointer-fill)" stroke="var(--browser-pointer-edge)" strokeWidth="1.6" strokeLinejoin="round" />
          <path d="M5 6.5L8 17" stroke="white" strokeOpacity=".45" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
        <span className="jarvis-browser-pointer-label">Jarvis</span>
      </span>
    </>}
  </div>;
}
