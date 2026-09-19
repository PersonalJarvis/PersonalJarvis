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
        <svg width="24" height="30" viewBox="0 0 24 30" fill="none">
          <path d="M3 3L20 15L12 17L8 25L3 3Z"
            fill="var(--browser-pointer-fill)" stroke="var(--browser-pointer-edge)" strokeWidth="2" strokeLinejoin="round" />
          <path d="M12 17L17 25" stroke="var(--browser-pointer-edge)" strokeWidth="5" strokeLinecap="round" />
          <path d="M12 17L17 25" stroke="var(--browser-pointer-fill)" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
        <span className="jarvis-browser-pointer-label"><span className="jarvis-browser-pointer-dot" />Jarvis</span>
      </span>
    </>}
  </div>;
}
