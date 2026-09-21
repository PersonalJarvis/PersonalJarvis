import { useLayoutEffect, useRef, useState } from "react";
import "./agentCursor.css";
import {
  aimCursor,
  beginCursor,
  projectFramePoint,
  stepCursor,
  type CursorClick,
  type CursorMotion,
} from "./agentCursorMotion";
import type { BrowserPointerState } from "./browserPointerState";

/** Hotspot of the arrow tip, in the SVG's own pixels. */
const TIP_X = 7.2;
const TIP_Y = 2.4;

interface ViewBox {
  hostW: number;
  hostH: number;
  frameW: number;
  frameH: number;
}

interface Mark {
  id: number;
  x: number;
  y: number;
}

function reducedMotion(): boolean {
  return typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function px(value: number): string {
  return String(Math.round(value * 100) / 100);
}

/**
 * Arrow drawn over the live browser. The agent reports the point it clicked;
 * the arrow eases there from wherever it already is, then presses.
 */
export function AgentCursor({ pointer }: { pointer?: BrowserPointerState }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const cursorRef = useRef<HTMLDivElement>(null);
  const motionRef = useRef<CursorMotion | null>(null);
  const viewRef = useRef<ViewBox>({ hostW: 0, hostH: 0, frameW: 0, frameH: 0 });
  const loopRef = useRef(0);
  const loopingRef = useRef(false);
  const aliveRef = useRef(true);
  const timersRef = useRef<number[]>([]);
  const tickRef = useRef<(now: number) => void>(() => {});
  const [marks, setMarks] = useState<Mark[]>([]);
  const [pressId, setPressId] = useState(0);

  const writeCursor = () => {
    const node = cursorRef.current;
    const motion = motionRef.current;
    if (!node || !motion) return;
    const view = viewRef.current;
    const point = projectFramePoint(motion.x, motion.y, view.frameW, view.frameH, view.hostW, view.hostH);
    node.style.transform = `translate3d(${px(point.x)}px, ${px(point.y)}px, 0)`;
    node.dataset.x = String(motion.x);
    node.dataset.y = String(motion.y);
  };

  const showClick = (click: CursorClick) => {
    if (!aliveRef.current || click.id <= 0) return;
    const view = viewRef.current;
    const point = projectFramePoint(click.x, click.y, view.frameW, view.frameH, view.hostW, view.hostH);
    setPressId(click.id);
    setMarks((current) => (
      current.some((mark) => mark.id === click.id)
        ? current
        : [...current.slice(-3), { id: click.id, x: point.x, y: point.y }]
    ));
    const drop = window.setTimeout(() => {
      if (!aliveRef.current) return;
      setMarks((current) => current.filter((mark) => mark.id !== click.id));
    }, 480);
    timersRef.current.push(drop);
  };

  tickRef.current = (now: number) => {
    const motion = motionRef.current;
    if (!aliveRef.current || !motion) {
      loopingRef.current = false;
      return;
    }
    const stepped = stepCursor(motion, now);
    motionRef.current = stepped.motion;
    writeCursor();
    if (stepped.arrivedClick) showClick(stepped.arrivedClick);
    if (stepped.moving && aliveRef.current) {
      loopRef.current = requestAnimationFrame((time) => tickRef.current(time));
    } else {
      loopingRef.current = false;
    }
  };

  useLayoutEffect(() => {
    aliveRef.current = true;
    const host = hostRef.current;
    if (!host) return;
    const measure = () => {
      viewRef.current.hostW = host.clientWidth;
      viewRef.current.hostH = host.clientHeight;
      writeCursor();
    };
    measure();
    if (typeof ResizeObserver === "undefined") {
      return () => { aliveRef.current = false; };
    }
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    return () => {
      aliveRef.current = false;
      observer.disconnect();
      cancelAnimationFrame(loopRef.current);
      loopingRef.current = false;
      for (const timer of timersRef.current) window.clearTimeout(timer);
    };
  }, []);

  useLayoutEffect(() => {
    if (!pointer) {
      motionRef.current = null;
      cancelAnimationFrame(loopRef.current);
      loopingRef.current = false;
      return;
    }
    viewRef.current.frameW = pointer.width;
    viewRef.current.frameH = pointer.height;
    const now = performance.now();
    if (!motionRef.current) motionRef.current = beginCursor(pointer.x, pointer.y, now);
    const view = viewRef.current;
    const scale = projectFramePoint(0, 0, view.frameW, view.frameH, view.hostW, view.hostH).scale;
    const distance = Math.hypot(
      (pointer.x - motionRef.current.x) * scale,
      (pointer.y - motionRef.current.y) * scale,
    );
    const aimed = aimCursor(motionRef.current, {
      x: pointer.x,
      y: pointer.y,
      clickId: pointer.click_id,
      clickX: pointer.click_x,
      clickY: pointer.click_y,
    }, distance, now, reducedMotion());
    motionRef.current = aimed.motion;
    if (aimed.releaseClick) showClick(aimed.releaseClick);
    if (aimed.changed) {
      const stepped = stepCursor(aimed.motion, now);
      motionRef.current = stepped.motion;
      if (stepped.arrivedClick) showClick(stepped.arrivedClick);
      if (stepped.moving && !loopingRef.current) {
        loopingRef.current = true;
        loopRef.current = requestAnimationFrame((time) => tickRef.current(time));
      }
    }
    writeCursor();
  }, [pointer]);

  return (
    <div ref={hostRef} className="agent-cursor-layer" aria-hidden="true" data-testid="browser-pointer-layer">
      {marks.map((mark) => (
        <span
          key={mark.id}
          className="agent-cursor-click"
          style={{ transform: `translate3d(${px(mark.x)}px, ${px(mark.y)}px, 0)` }}
          data-testid="browser-click-mark"
        >
          <span className="agent-cursor-dot" />
          <span className="agent-cursor-ring" />
        </span>
      ))}
      {pointer && (
        <div ref={cursorRef} className="agent-cursor" data-testid="browser-agent-pointer">
          <div className="agent-cursor-shift" style={{ transform: `translate(${-TIP_X}px, ${-TIP_Y}px)` }}>
            <div
              key={pressId}
              className="agent-cursor-art"
              data-pressing={pressId > 0 ? "true" : "false"}
              style={{ transformOrigin: `${TIP_X}px ${TIP_Y}px` }}
            >
              <svg className="agent-cursor-arrow" width="34" height="42" viewBox="0 0 34 42" fill="none">
                <path
                  d="M7.2 2.4 L4.6 28.8 L11.2 24.2 L15.4 39.2 L20.2 36.6 L16.2 22.2 L31.2 21.2 Z"
                  fill="#FFFFFF"
                  stroke="#0A0A0A"
                  strokeWidth="1.9"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </svg>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
