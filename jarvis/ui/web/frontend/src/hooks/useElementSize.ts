import { useLayoutEffect, useState, type RefObject } from "react";

export interface ElementSize {
  width: number;
  height: number;
}

/**
 * The live content box of an element, in CSS pixels — `{0, 0}` until it has
 * been laid out (and in environments without layout, such as jsdom).
 *
 * Measures once on mount and again on every resize; the state only changes
 * when the numbers do, so a steady box causes no re-renders.
 */
export function useElementSize<T extends HTMLElement>(ref: RefObject<T | null>): ElementSize {
  const [size, setSize] = useState<ElementSize>({ width: 0, height: 0 });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      setSize((prev) =>
        prev.width === rect.width && prev.height === rect.height
          ? prev
          : { width: rect.width, height: rect.height },
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);

  return size;
}
