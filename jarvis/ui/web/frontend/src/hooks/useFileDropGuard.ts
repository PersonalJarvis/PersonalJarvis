import { useEffect } from "react";

/**
 * Stop a missed file drop from replacing the app with the file.
 *
 * A browser's default action for a file dropped anywhere in a page is to
 * NAVIGATE to it. Inside the desktop shell there is no address bar and no Back
 * button, so a screenshot dropped two centimetres beside the composer does not
 * merely fail — it takes the whole window: every open chat, every terminal in
 * the grid, gone until the app is restarted. The person who did it has no way
 * to read that as "you missed", because nothing about a picture filling the
 * window says a drop was refused.
 *
 * So every drop that no drop zone claimed is swallowed here, and nothing else
 * happens: no toast, no highlight. A drop on empty chrome SHOULD do nothing —
 * this only makes "nothing" true.
 *
 * ## Why this cannot break a real drop target
 *
 * The listeners run in the BUBBLE phase on `window`, which is after React has
 * delivered the event to its own tree (React 18 attaches at the root
 * container, below `window`). A target that took the drop has already called
 * `preventDefault()` by then, and `defaultPrevented` is how this tells the two
 * apart. A handler that did NOT call it was not going to receive the file
 * anyway — that is what the default action being navigation means.
 *
 * `dragover` needs the same treatment: without a `preventDefault()` there the
 * browser refuses the drop before `drop` ever fires, so guarding only `drop`
 * would leave the cursor showing 🚫 over the whole app. Guarding both keeps
 * the page's own targets in charge of the cursor — they preventDefault first,
 * and this one only claims what they left.
 */
export function useFileDropGuard(): void {
  useEffect(() => {
    const carriesFiles = (event: DragEvent): boolean => {
      const types = event.dataTransfer?.types;
      return types ? Array.from(types).includes("Files") : false;
    };
    const swallow = (event: DragEvent) => {
      if (event.defaultPrevented) return; // a real target took it
      if (!carriesFiles(event)) return; // dragged text/links are the browser's
      event.preventDefault();
    };
    window.addEventListener("dragover", swallow);
    window.addEventListener("drop", swallow);
    return () => {
      window.removeEventListener("dragover", swallow);
      window.removeEventListener("drop", swallow);
    };
  }, []);
}
