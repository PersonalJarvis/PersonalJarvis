import { useEffect, useRef, useState, type RefObject } from "react";

/** A context failure only affects this team world, never the ordinary island. */
export function useSwarmSurface(hostRef: RefObject<HTMLElement>, onUnavailable: () => void) {
  const [generation, setGeneration] = useState(0);
  const failures = useRef(0);
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let canvas: HTMLCanvasElement | null = null;
    let disposed = false;
    let rebuilt = false;
    let frame = 0;
    let attaches = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const rebuild = () => { if (!disposed && !rebuilt) { rebuilt = true; setGeneration(n => n + 1); } };
    const lost = (event: Event) => {
      event.preventDefault();
      if (disposed) return;
      if (++failures.current > 2) { onUnavailable(); return; }
      timer = setTimeout(rebuild, 250);
    };
    const restored = () => { clearTimeout(timer); rebuild(); };
    const attach = () => {
      canvas = host.querySelector("canvas");
      if (!canvas) { if (++attaches < 120) frame = requestAnimationFrame(attach); return; }
      canvas.addEventListener("webglcontextlost", lost);
      canvas.addEventListener("webglcontextrestored", restored);
    };
    attach();
    const stable = setTimeout(() => { failures.current = 0; }, 60000);
    return () => {
      disposed = true; cancelAnimationFrame(frame); clearTimeout(timer); clearTimeout(stable);
      if (!canvas) return;
      canvas.removeEventListener("webglcontextlost", lost);
      canvas.removeEventListener("webglcontextrestored", restored);
      try {
        const context = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
        context?.getExtension("WEBGL_lose_context")?.loseContext();
      } catch { /* A context already destroyed by the renderer needs no release. */ }
    };
  }, [generation, hostRef, onUnavailable]);
  return generation;
}
