/**
 * Build a compact, branded drag image for a mission/output card.
 *
 * Without this, HTML5 drag-and-drop snapshots the entire dragged element — the
 * whole Outputs card, prompt text and RESTART/ERROR buttons and all — into a
 * large, opaque ghost that looks broken. `setDragImage` swaps that for a small
 * "📎 <title>" pill that reads as an intentional, liftable token.
 *
 * The chip is appended off-screen (the browser still snapshots it), used as the
 * drag image, then removed on the next frame — by which point the snapshot has
 * been taken.
 */

const ACCENT = "#FFD60A"; // --primary signal-yellow
const TITLE_MAX = 64;

function truncate(title: string): string {
  const clean = title.replace(/\s+/g, " ").trim();
  if (!clean) return "Mission";
  return clean.length > TITLE_MAX ? clean.slice(0, TITLE_MAX - 1) + "…" : clean;
}

/**
 * Replace the native drag ghost with a compact mission chip. No-op (never
 * throws) when `setDragImage` is unavailable (older browsers / tests).
 */
export function applyMissionDragImage(dt: DataTransfer, title: string): void {
  if (!dt || typeof dt.setDragImage !== "function") return;
  try {
    const chip = document.createElement("div");
    chip.setAttribute("data-mission-drag-chip", "");
    chip.textContent = `📎 ${truncate(title)}`;
    Object.assign(chip.style, {
      position: "fixed",
      top: "-9999px",
      left: "-9999px",
      display: "inline-flex",
      alignItems: "center",
      maxWidth: "320px",
      padding: "8px 14px",
      borderRadius: "9999px",
      border: `1px solid ${ACCENT}`,
      background: "rgba(20, 20, 16, 0.92)",
      color: "#f5f5f0",
      font: "600 13px/1.2 ui-sans-serif, system-ui, sans-serif",
      whiteSpace: "nowrap",
      boxShadow: `0 8px 24px rgba(0,0,0,0.45), 0 0 18px ${ACCENT}55`,
      pointerEvents: "none",
      zIndex: "2147483647",
    } as Partial<CSSStyleDeclaration>);
    document.body.appendChild(chip);

    // Anchor the grab point a little inside the chip so it sits under the cursor.
    dt.setDragImage(chip, 18, 18);

    // The snapshot is taken synchronously; clean up after this frame.
    const remove = () => chip.remove();
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(remove);
    } else {
      setTimeout(remove, 0);
    }
  } catch {
    // A drag-image hiccup must never break the drag itself.
  }
}
