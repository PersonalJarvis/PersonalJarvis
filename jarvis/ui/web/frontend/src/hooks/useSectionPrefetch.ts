import { useEffect, useRef } from "react";
import { sectionPrefetch } from "@/lib/sectionPrefetch";
import type { SectionId } from "@/store/events";

export const SECTION_PREFETCH_DELAY_MS = 150;

/** A brief hover/focus warms one destination; passing over the rail warms none. */
export function useSectionPrefetch(section: SectionId) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancel = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  };
  useEffect(() => cancel, [section]);

  const schedule = () => {
    cancel();
    timer.current = setTimeout(() => {
      timer.current = null;
      void sectionPrefetch.prefetch(section);
    }, SECTION_PREFETCH_DELAY_MS);
  };

  return {
    onMouseEnter: schedule,
    onMouseLeave: cancel,
    onFocus: schedule,
    onBlur: cancel,
  };
}
