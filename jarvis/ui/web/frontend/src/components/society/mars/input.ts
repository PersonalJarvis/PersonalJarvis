import type { PlayerInput } from "./controller";

const MOVEMENT_KEYS = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space", "ShiftLeft", "ShiftRight"]);
export const NO_INPUT: PlayerInput = { forward: 0, right: 0, run: false, jump: false };
export function ownsTextOrUi(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest("input, textarea, select, button, a, [contenteditable]:not([contenteditable='false']), [role='textbox'], [role='dialog'], [role='menu'], [data-mars-ui], .xterm"));
}

/** All listeners belong to one mounted world. Call dispose on world or input-mode changes. */
export function bindPlayerInput(host: HTMLElement, invalidate: () => void, escape: () => void = () => undefined, look: (dx: number, dy: number) => void = () => undefined, interact: () => void = () => undefined) {
  const keys = new Set<string>();
  const pending = new Set<string>();
  let disposed = false;
  let pointerId: number | null = null, pointerX = 0, pointerY = 0;
  const release = () => {
    const captured = pointerId;
    pointerId = null;
    if (captured !== null && host.hasPointerCapture?.(captured)) host.releasePointerCapture(captured);
  };
  const clear = () => { keys.clear(); pending.clear(); release(); invalidate(); };
  const canOwn = (target: EventTarget | null) => !ownsTextOrUi(target) && host.contains(document.activeElement) && !ownsTextOrUi(document.activeElement);
  const down = (event: KeyboardEvent) => {
    if (event.code === "Escape") { clear(); escape(); return; }
    if (!canOwn(event.target) || event.altKey || event.ctrlKey || event.metaKey) { clear(); return; }
    if (event.code === "KeyE" && !event.repeat) { event.preventDefault(); interact(); return; }
    if (!MOVEMENT_KEYS.has(event.code)) return;
    event.preventDefault(); keys.add(event.code); pending.add(event.code); invalidate();
  };
  const up = (event: KeyboardEvent) => { keys.delete(event.code); invalidate(); };
  const focus = (event: FocusEvent) => { if (!canOwn(event.target)) clear(); };
  const pointer = (event: PointerEvent) => {
    if (ownsTextOrUi(event.target)) return;
    host.focus({ preventScroll: true });
    if (event.button !== 0) return;
    pointerId = event.pointerId; pointerX = event.clientX; pointerY = event.clientY;
    host.setPointerCapture?.(event.pointerId);
  };
  const move = (event: PointerEvent) => {
    if (event.pointerId !== pointerId) return;
    look(event.clientX - pointerX, event.clientY - pointerY);
    pointerX = event.clientX; pointerY = event.clientY; invalidate();
  };
  const lostCapture = () => { release(); invalidate(); };
  window.addEventListener("keydown", down);
  window.addEventListener("keyup", up);
  window.addEventListener("blur", clear);
  document.addEventListener("focusin", focus);
  host.addEventListener("pointerdown", pointer);
  host.addEventListener("pointermove", move);
  host.addEventListener("pointerup", release);
  host.addEventListener("pointercancel", clear);
  host.addEventListener("lostpointercapture", lostCapture);
  return {
    clear,
    consume() { pending.clear(); },
    read(): PlayerInput {
      if (disposed || !canOwn(document.activeElement)) { keys.clear(); pending.clear(); return NO_INPUT; }
      // A press/release between render frames still reaches one physics step.
      const held = (...codes: string[]) => codes.some((code) => keys.has(code) || pending.has(code));
      return {
        forward: Number(held("KeyW", "ArrowUp")) - Number(held("KeyS", "ArrowDown")),
        right: Number(held("KeyD", "ArrowRight")) - Number(held("KeyA", "ArrowLeft")),
        run: held("ShiftLeft", "ShiftRight"), jump: held("Space"),
      };
    },
    dispose() {
      disposed = true; clear();
      window.removeEventListener("keydown", down); window.removeEventListener("keyup", up);
      window.removeEventListener("blur", clear); document.removeEventListener("focusin", focus);
      host.removeEventListener("pointerdown", pointer);
      host.removeEventListener("pointermove", move); host.removeEventListener("pointerup", release);
      host.removeEventListener("pointercancel", clear); host.removeEventListener("lostpointercapture", lostCapture);
    },
  };
}
