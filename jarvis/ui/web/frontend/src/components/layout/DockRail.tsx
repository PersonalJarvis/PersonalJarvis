import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AnimatePresence,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "framer-motion";
import { useEventStore, type SectionId } from "@/store/events";
import {
  NAV_GROUPS,
  presentNavItem,
  resolveNavLabel,
  type NavItem,
} from "@/components/layout/navGroups";
import { useHomeStore } from "@/store/home";
import { useSectionHealth } from "@/hooks/useProviders";
import { usePluginAttention } from "@/hooks/usePluginAttention";
import { dockSlotAt, layoutDock } from "@/lib/dockMagnify";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The icon rail — every section of the app as one icon, on the left edge.
 *
 * ONE rail for the whole app: the mission deck's dock and the collapsed
 * sidebar are the same component, so a section reads the same wherever the
 * user is — the maintainer found it jarring that leaving the deck dropped the
 * icons back to a plain list. The list is the sidebar's own `NAV_GROUPS` — one
 * source, so a section added there appears here without anyone remembering to
 * add it twice (AP-4). The attention signals are shared too: a provider error
 * lights API Keys red, a plugin that needs a reconnect lights Skills amber, and
 * clicking Skills while a plugin needs attention lands on the Plugins tab, the
 * same shortcut the expanded sidebar takes.
 *
 * There is NO magnification. The dock went through the desktop-dock hill (icons
 * growing under the pointer, neighbours pushed apart, then a rigid version
 * with a hill of sizes only) and the maintainer took the whole idea back on
 * 2026-08-18: the icons as they sit are right, the growing under the pointer
 * was not. So the icons hold their size and place, always. What hover does:
 * the hovered icon gets the glass surface, and exactly one label glides from
 * icon to icon at a fixed distance from the rail, fading in and out. The
 * pointer never goes through React state on the way to the label — it writes
 * a motion value and the label position derives from it. Geometry (where each
 * icon rests) is pure math in `lib/dockMagnify.ts`.
 *
 * Users who asked for less motion get the label without the glide.
 */
const BASE = 30; // px — icon box at rest
const GAP = 8; // px — between icon boxes at rest
const ICON = 16; // px — glyph
const RAIL_WIDTH = 64; // px — Tailwind w-16, the column the icons centre in
/** Space above the first and below the last icon. */
const PAD_TOP = 12;
const PAD_BOTTOM = 12;
/** The rest geometry, for tests that need to aim a pointer at an icon. */
export const DOCK_RAIL_GEOMETRY = { BASE, GAP, PAD_TOP } as const;
/**
 * The label's glide from icon to icon: critically damped, so it settles in
 * ~40 ms and never overshoots the icon it lands on.
 */
const GLIDE_SPRING = { stiffness: 900, damping: 60, mass: 1 };
/** Where the label's left edge sits: a fixed distance beyond the icon. */
const LABEL_LEFT = RAIL_WIDTH / 2 + BASE / 2 + 12;

export function DockRail({ className }: { className?: string }) {
  const t = useT();
  const activeSection = useEventStore((s) => s.activeSection);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const conversations = useEventStore((s) => s.conversations);
  const { health } = useSectionHealth();
  const pluginAttention = usePluginAttention();
  const reduced = useReducedMotion() ?? false;

  // The front page's icon follows the Voice | Chat switch like the sidebar
  // row does (`presentNavItem`); the rail must not say "Chats" while the
  // expanded sidebar says "Voice".
  const surface = useHomeStore((s) => s.surface);
  const items = useMemo(
    () => NAV_GROUPS.flat().map((item) => presentNavItem(item, surface)),
    [surface],
  );
  const groupBreaks = useMemo(() => {
    // Index of the first item of every group after the first — a hairline is
    // drawn above these so the rail keeps the sidebar's grouping.
    const breaks = new Set<number>();
    let n = 0;
    for (let g = 0; g < NAV_GROUPS.length; g++) {
      if (g > 0) breaks.add(n);
      n += NAV_GROUPS[g].length;
    }
    return breaks;
  }, []);

  // A provider that is set up but failing — surfaced app-wide as a red pip on
  // API Keys. The amber "needs setup" state is deliberately NOT shown: on a
  // fresh install every unconfigured section would light up.
  const apikeysError = useMemo(
    () => Object.values(health).some((h) => h?.status === "error"),
    [health],
  );
  const pluginsNeedReconnect = pluginAttention.count > 0;
  const pluginWarnHint = pluginAttention.names.length
    ? `${t("sidebar.plugins_reconnect_alert")}: ${pluginAttention.names.join(", ")}`
    : t("sidebar.plugins_reconnect_alert");

  // Rest geometry — positions never move, so this is a plain number.
  const rest = useMemo(() => layoutDock(items.length, BASE, GAP, null), [items.length]);
  const blockHeight = PAD_TOP + rest.extent + PAD_BOTTOM;

  // --- pointer → motion values (no React state on the hot path) -----------
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const lastClientY = useRef<number | null>(null);
  const hoveredRef = useRef(-1);
  const [hovered, setHovered] = useState(-1);
  const hoveredMV = useMotionValue(-1);

  // --- the one label ------------------------------------------------------
  // The last position is kept so the label fades out where it was rather than
  // jumping to a default the instant the pointer leaves. Derived from the
  // hovered slot as a motion value, so a scroll under a still pointer (which
  // re-aims the hover) moves the label along without a React render.
  const lastLabelTop = useRef(PAD_TOP + GAP + BASE / 2);
  const labelTopRaw = useTransform(hoveredMV, (h: number) => {
    if (h < 0) return lastLabelTop.current;
    const top = PAD_TOP + rest.items[h].center - (scrollerRef.current?.scrollTop ?? 0);
    lastLabelTop.current = top;
    return top;
  });
  const labelTopSmooth = useSpring(labelTopRaw, GLIDE_SPRING);
  const labelTop = reduced ? labelTopRaw : labelTopSmooth;

  // The label is rendered in a portal on <body>, fixed to the viewport, and
  // its rail-relative `top` is offset by where the rail sits on screen. The
  // offset is measured whenever the hovered slot changes — the only moment
  // the label's own position changes too, so the derived value is never stale
  // for a label that is moving. The rail itself does not move mid-hover.
  //
  // Why a portal: the sidebar column paints at z-20 with no stacking context
  // around the section stage (App.tsx), so a section's own `z-20` layer — the
  // IDE's pane chat is one — meets the column's z-20 in the SAME context and
  // wins on DOM order, covering a label that reaches past the rail's edge.
  // A higher z-index on the column would only move the tie to the next
  // section overlay; leaving the column's context entirely settles it, the
  // same way `QuickTooltip` does.
  const navRef = useRef<HTMLElement | null>(null);
  const navTop = useRef(0);
  const labelLeft = useMotionValue(LABEL_LEFT);
  const labelViewportTop = useTransform(labelTop, (top: number) => top + navTop.current);
  const measureOrigin = useCallback(() => {
    const rect = navRef.current?.getBoundingClientRect();
    if (!rect) return;
    navTop.current = rect.top;
    labelLeft.set(rect.left + LABEL_LEFT);
  }, [labelLeft]);

  const setHoveredSlot = useCallback(
    (slot: number) => {
      const prev = hoveredRef.current;
      if (slot === prev) return;
      hoveredRef.current = slot;
      if (slot >= 0) measureOrigin();
      hoveredMV.set(slot);
      setHovered(slot);
      if (slot < 0) return;
      if (prev < 0) {
        // A fresh hover appears AT its icon; only a label that is already up
        // glides. Without this it would fly in from wherever the last hover
        // faded out.
        labelTopSmooth.jump(
          PAD_TOP + rest.items[slot].center - (scrollerRef.current?.scrollTop ?? 0),
        );
      }
    },
    [hoveredMV, labelTopSmooth, rest],
  );

  const track = useCallback(
    (clientY: number) => {
      const el = scrollerRef.current;
      if (!el) return;
      const y = clientY - el.getBoundingClientRect().top + el.scrollTop - PAD_TOP;
      // Off the row (the padding, or past the last icon) counts as away.
      setHoveredSlot(dockSlotAt(y, items.length, BASE, GAP));
    },
    [items.length, setHoveredSlot],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      lastClientY.current = e.clientY;
      track(e.clientY);
    },
    [track],
  );
  const onPointerLeave = useCallback(() => {
    lastClientY.current = null;
    setHoveredSlot(-1);
  }, [setHoveredSlot]);
  // Wheel-scrolling under a still pointer moves the icons under it — re-aim.
  const onScroll = useCallback(() => {
    if (lastClientY.current !== null) track(lastClientY.current);
  }, [track]);

  // Unmounting mid-hover (the pick navigates away) must not leave a stale
  // hover behind for the next mount.
  useEffect(
    () => () => {
      hoveredRef.current = -1;
    },
    [],
  );

  const hoveredItem = hovered >= 0 ? items[hovered] : null;
  const hoveredCount = hoveredItem?.id === "chats" ? conversations.length : 0;
  const hoveredHint =
    hoveredItem?.id === "apikeys" && apikeysError
      ? t("sidebar.apikeys_alert")
      : hoveredItem?.id === "skills" && pluginsNeedReconnect
        ? pluginWarnHint
        : null;

  return (
    <nav
      ref={navRef}
      aria-label={t("deck.sections")}
      className={cn("relative z-10 flex min-h-0 w-16 shrink-0 flex-col", className)}
    >
      <div
        ref={scrollerRef}
        data-testid="dock-rail"
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
        onScroll={onScroll}
        className="dock-rail-scroller relative min-h-0 w-full flex-1 overflow-y-auto overflow-x-hidden"
      >
        <div className="relative w-full" style={{ height: blockHeight }}>
          {items.map((item, i) => (
            <DockIcon
              key={item.id}
              item={item}
              restCenter={rest.items[i].center}
              label={resolveNavLabel(t, item)}
              active={activeSection === item.id || !!item.matchIds?.includes(activeSection)}
              hovered={hovered === i}
              live={item.id === "chats" && conversations.length > 0}
              alert={item.id === "apikeys" && apikeysError}
              alertTitle={t("sidebar.apikeys_alert")}
              warn={item.id === "skills" && pluginsNeedReconnect}
              warnTitle={pluginWarnHint}
              groupBreak={groupBreaks.has(i)}
              // A plugin problem sends the Skills icon straight into the
              // Plugins tab (where the banner + jump button are), so one click
              // lands on the fix instead of the default Skills tab.
              onSelect={() =>
                setActiveSection(
                  item.id === "skills" && pluginsNeedReconnect ? "plugins" : item.id,
                )
              }
              onFocus={() => setHoveredSlot(i)}
              onBlur={() => {
                if (hoveredRef.current === i && lastClientY.current === null) {
                  setHoveredSlot(-1);
                }
              }}
            />
          ))}
        </div>
      </div>

      {/* The label rides beside the hovered icon. It lives in a portal on
          <body>, fixed to the viewport, so neither the scroller's clipping nor
          a section's own z-20 layer (see `measureOrigin`) can cut it off.
          z-[70] is the app's tooltip level (`QuickTooltip`). Decorative: every
          button already carries its name as aria-label. */}
      {createPortal(
        <AnimatePresence>
          {hoveredItem && (
            <motion.div
              key="label"
              aria-hidden
              data-testid="dock-label"
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -4 }}
              transition={reduced ? { duration: 0 } : { duration: 0.12, ease: "easeOut" }}
              style={{ top: labelViewportTop, left: labelLeft, y: "-50%" }}
              className="pointer-events-none fixed z-[70] flex items-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-background/95 px-2 py-1 text-xs text-foreground backdrop-blur"
            >
              {resolveNavLabel(t, hoveredItem)}
              {hoveredItem.beta && (
                <span className="rounded-full border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-primary">
                  {t("nav.agentic_ide_beta")}
                </span>
              )}
              {hoveredCount > 0 && (
                <span className="font-mono text-[10px] text-primary">{hoveredCount}</span>
              )}
              {hoveredHint && (
                <span className="max-w-[28ch] truncate text-muted-foreground">— {hoveredHint}</span>
              )}
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </nav>
  );
}

function DockIcon({
  item,
  restCenter,
  label,
  active,
  hovered,
  live,
  alert,
  alertTitle,
  warn,
  warnTitle,
  groupBreak,
  onSelect,
  onFocus,
  onBlur,
}: {
  item: NavItem;
  /** The icon's fixed centre along the rail, px from the row's start. */
  restCenter: number;
  label: string;
  active: boolean;
  hovered: boolean;
  /** Something is going on in this section (a pip in the accent colour). */
  live: boolean;
  /** A section this icon fronts has a provider that is set up but failing. */
  alert: boolean;
  alertTitle: string;
  /** Softer "needs attention" — e.g. a plugin whose token needs a reconnect. */
  warn: boolean;
  warnTitle: string;
  groupBreak: boolean;
  onSelect: () => void;
  onFocus: () => void;
  onBlur: () => void;
}) {
  const Icon = item.icon;

  return (
    <>
      {groupBreak && (
        <span
          aria-hidden
          className="absolute left-4 right-4 h-px bg-border"
          style={{ top: PAD_TOP + restCenter - BASE / 2 - GAP / 2 - 0.5 }}
        />
      )}
      <button
        type="button"
        data-testid={`nav-row-${item.id}`}
        onClick={onSelect}
        onFocus={onFocus}
        onBlur={onBlur}
        aria-label={label}
        aria-current={active ? "page" : undefined}
        className={cn(
          "absolute left-1/2 flex -translate-x-1/2 items-center justify-center rounded-xl border transition-colors duration-150",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
          // The active control is the app's glass surface with the accent on
          // the glyph — the same language as the expanded sidebar's row.
          active
            ? "jarvis-message-surface border-primary/40 text-primary"
            : hovered
              ? "border-border/60 bg-card/40 text-foreground"
              : "border-transparent text-muted-foreground",
        )}
        style={{ top: PAD_TOP + restCenter - BASE / 2, width: BASE, height: BASE }}
      >
        <span
          aria-hidden
          className="flex shrink-0 items-center justify-center"
          style={{ width: ICON, height: ICON }}
        >
          <Icon className="h-full w-full" />
        </span>

        {/* Pips ride on the icon's corner. The signal is the point, not the
            row — and there is no room for anything beside a 30 px box. */}
        {alert ? (
          <span
            data-testid={`nav-alert-${item.id}`}
            role="status"
            aria-label={alertTitle}
            className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-destructive ring-2 ring-background"
          />
        ) : warn ? (
          <span
            data-testid={`nav-warn-${item.id}`}
            role="status"
            aria-label={warnTitle}
            className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-foreground ring-2 ring-background"
          />
        ) : live ? (
          <span
            aria-hidden
            className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-primary ring-2 ring-background"
          />
        ) : null}
      </button>
    </>
  );
}

export type { SectionId };
