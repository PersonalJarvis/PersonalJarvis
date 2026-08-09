import { useEffect } from "react";

import { useEventStore } from "@/store/events";
import { hrefWithSection } from "@/lib/sectionUrl";

/**
 * Write the active section into the window's address, so a reload returns to it.
 *
 * Mounted at the shell, once per document, and deliberately driven by the STORE
 * rather than by the nav: a section also changes when Jarvis is told "open the
 * workspace" by voice, when a view sends the user somewhere on error, and when a
 * deep link arrives over the WebSocket. Watching the value covers all of them,
 * where hooking the click would cover one.
 *
 * See lib/sectionUrl for why the URL is the right place to keep this.
 */
export function useSectionUrlMemory(): void {
  const activeSection = useEventStore((s) => s.activeSection);

  useEffect(() => {
    const next = hrefWithSection(window.location.href, activeSection);
    if (!next) return;
    try {
      // Keep whatever history state is there; this hook owns the address, not
      // the entry. A WebView that refuses the call (file:// origins, an
      // exhausted rate limit) costs the memory of one section change and
      // nothing else, so it must not take the shell down with it.
      window.history.replaceState(window.history.state, "", next);
    } catch {
      /* the section simply does not survive the next reload */
    }
  }, [activeSection]);
}
