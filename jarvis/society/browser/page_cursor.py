"""The arrow drawn inside the real Chrome window.

Browser clicks are sent straight to the page. They never move the mouse the
person sees, so the window itself has to draw the arrow and glide it to the
click.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# The page listens for the clicks it actually receives. The dataset is the
# switch: on while the agent is driving, off while the person has the window.
CURSOR_SCRIPT = """
(() => {
  if (globalThis.__jarvisCursorInstalled) return;
  globalThis.__jarvisCursorInstalled = true;
  const root = document.documentElement;
  if (!root) return;
  const host = document.createElement("div");
  host.id = "jarvis-agent-cursor";
  host.setAttribute("aria-hidden", "true");
  const paint = (name, value) => host.style.setProperty(name, value, "important");
  paint("position", "fixed");
  paint("margin", "0");
  paint("padding", "0");
  paint("border", "0");
  paint("background", "transparent");
  paint("width", "0");
  paint("height", "0");
  paint("overflow", "visible");
  paint("pointer-events", "none");
  paint("z-index", "2147483647");
  paint("inset", "auto");
  paint("left", "0");
  paint("top", "0");
  root.appendChild(host);
  try {
    host.setAttribute("popover", "manual");
    host.showPopover();
    paint("left", "0");
    paint("top", "0");
  } catch (error) {
    /* A document without popovers keeps the fixed layer. */
  }
  const shadow = host.attachShadow({ mode: "closed" });
  shadow.innerHTML = [
    "<style>",
    ":host{all:initial}",
    ".arrow{position:fixed;left:0;top:0;width:0;height:0;opacity:0;",
    "transform-origin:7.2px 2.4px;filter:drop-shadow(0 1px 1px rgba(0,0,0,.45))}",
    ".shift{transform:translate(-7.2px,-2.4px)}",
    ".ring{position:fixed;left:0;top:0;width:26px;height:26px;margin:-13px 0 0 -13px;",
    "border:2.5px solid #0a0a0a;border-radius:50%;box-shadow:0 0 0 1.5px #fff;",
    "opacity:0;pointer-events:none}",
    "</style>",
    '<div class="arrow"><div class="shift">',
    '<svg width="52" height="64" viewBox="0 0 34 42" fill="none">',
    '<path d="M7.2 2.4 L4.6 28.8 L11.2 24.2 L15.4 39.2 L20.2 36.6 L16.2 22.2 L31.2 21.2 Z"',
    ' fill="#fff" stroke="#0a0a0a" stroke-width="1.9" stroke-linejoin="round"',
    ' stroke-linecap="round"/></svg></div></div><div class="ring"></div>',
  ].join("");
  const arrow = shadow.querySelector(".arrow");
  const ring = shadow.querySelector(".ring");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let x = null;
  let y = null;
  let fromX = 0;
  let fromY = 0;
  let toX = 0;
  let toY = 0;
  let started = 0;
  let duration = 0;
  let pending = null;
  let handle = 0;
  const armed = () => root.dataset.jarvisCursor === "1";
  const ease = (t) => {
    const left = 1 - t;
    return 1 - left * left * left;
  };
  const travel = (distance) => (
    distance < 1 ? 0 : Math.min(340, Math.max(130, 110 + distance * 0.28))
  );
  const show = (px, py) => {
    arrow.style.transform = "translate3d(" + px + "px," + py + "px,0)";
    arrow.style.opacity = "1";
  };
  const press = (px, py) => {
    const at = "translate3d(" + px + "px," + py + "px,0)";
    ring.style.opacity = "1";
    ring.animate(
      [
        { transform: at + " scale(0.4)", opacity: 0.95 },
        { transform: at + " scale(1.8)", opacity: 0 },
      ],
      { duration: reduce ? 1 : 420, easing: "cubic-bezier(0.16, 1, 0.3, 1)", fill: "forwards" },
    );
    arrow.animate(
      [
        { transform: at + " scale(1)" },
        { transform: at + " scale(0.9)", offset: 0.45 },
        { transform: at + " scale(1)" },
      ],
      { duration: reduce ? 1 : 110, easing: "ease-out" },
    );
  };
  const arrived = (px, py) => {
    if (!pending) return;
    const dx = px - pending.x;
    const dy = py - pending.y;
    if (dx * dx + dy * dy > 6.25) return;
    const point = pending;
    pending = null;
    press(point.x, point.y);
  };
  const step = (now) => {
    const t = duration <= 0 ? 1 : Math.min(1, Math.max(0, (now - started) / duration));
    const eased = ease(t);
    x = fromX + (toX - fromX) * eased;
    y = fromY + (toY - fromY) * eased;
    show(x, y);
    arrived(x, y);
    handle = t < 1 ? requestAnimationFrame(step) : 0;
  };
  const aim = (nx, ny, down) => {
    if (!armed()) {
      arrow.style.opacity = "0";
      pending = null;
      return;
    }
    if (!host.isConnected) root.appendChild(host);
    if (x === null) {
      x = nx;
      y = ny;
      fromX = nx;
      fromY = ny;
      toX = nx;
      toY = ny;
      duration = 0;
      show(nx, ny);
    } else if (Math.abs(toX - nx) > 0.5 || Math.abs(toY - ny) > 0.5) {
      fromX = x;
      fromY = y;
      toX = nx;
      toY = ny;
      started = performance.now();
      duration = reduce ? 0 : travel(Math.hypot(nx - x, ny - y));
      if (duration === 0) {
        x = nx;
        y = ny;
        show(nx, ny);
      } else if (!handle) {
        handle = requestAnimationFrame(step);
      }
    }
    if (down) pending = { x: nx, y: ny };
    if (x !== null) arrived(x, y);
  };
  window.addEventListener("mousemove", (event) => aim(event.clientX, event.clientY, false), true);
  window.addEventListener("mousedown", (event) => aim(event.clientX, event.clientY, true), true);
  new MutationObserver(() => {
    if (!armed()) {
      arrow.style.opacity = "0";
      pending = null;
    }
  }).observe(root, { attributes: true, attributeFilter: ["data-jarvis-cursor"] });
})();
"""

ARM_SOURCE = (
    "(on) => { const root = document.documentElement;"
    " if (root) root.dataset.jarvisCursor = on ? '1' : ''; }"
)


async def install_cursor(context: Any) -> None:
    """Put the arrow on every later document in this browser."""
    await context.add_init_script(CURSOR_SCRIPT)


async def arm_cursor(context: Any, armed: bool) -> None:
    """Show or hide the arrow on documents that are already open."""
    if context is None:
        return
    for page in list(getattr(context, "pages", ()) or ()):
        closed = getattr(page, "is_closed", None)
        if callable(closed) and closed():
            continue
        frames = list(getattr(page, "frames", ()) or ()) or [page]
        for frame in frames:
            try:
                await frame.evaluate(CURSOR_SCRIPT)
                await frame.evaluate(ARM_SOURCE, armed)
            except Exception:
                log.debug("Agent cursor could not attach to this frame", exc_info=True)
