import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AnimatePresence,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
  type MotionValue,
} from "framer-motion";
import { useEventStore, type SectionId } from "@/store/events";
import { NAV_GROUPS, resolveNavLabel, type NavItem } from "@/components/layout/navGroups";
import { useSectionHealth } from "@/hooks/useProviders";
import { usePluginAttention } from "@/hooks/usePluginAttention";
import {
  DOCK_MAX_SCALE,
  DOCK_RADIUS_UNITS,
  dockSlotAt,
  layoutDock,
  type DockLayout,
} from "@/lib/dockMagnify";
import { playDockTick } from "@/lib/sound";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The deck's section dock — every section of the app as one icon, on the
 * left edge, with the magnification desktop docks made familiar.
 *
 * It replaces the sidebar ONLY while the deck is on screen (App.tsx): the
 * sections appear once, not twice, and the rigid list becomes the smooth
 * one. Clicking an icon jumps to that section, where the ordinary sidebar is
 * waiting; the deck's own dock is not a second navigation, it IS the
 * navigation for this one surface.
 *
 * The list is the sidebar's own `NAV_GROUPS` — one source, so a section added
 * there appears here without anyone remembering to add it twice (AP-4). The
 * attention signals are the sidebar's too: a provider error lights API Keys,
 * a plugin that needs a reconnect lights Plugins.
 *
 * How the motion is built — the same recipe as the well-known Framer Motion
 * docks, with one deliberate difference:
 *
 * - The column is RIGID. No icon ever leaves its place: the hill is a hill of
 *   sizes only, each icon growing around its own rest centre, neighbours a
 *   little, the hovered one most. The desktop docks push neighbours apart to
 *   make room; the maintainer found that shuffle distracting, so it is gone.
 *   Boxes may overlap by a few px at the peak — only the hovered box paints a
 *   surface, and it is drawn on top, so nothing shows.
 * - Nothing about the pointer goes through React state. The pointer writes a
 *   motion value; the whole layout is ONE derived motion value; every icon
 *   binds its box to that. Re-rendering twenty-odd buttons on every mouse
 *   event, and then letting a CSS transition chase the result, is what made
 *   the first version stutter and trail the mouse.
 * - The hill follows the pointer instantly; only its HEIGHT is sprung. Enter
 *   the rail and the hill rises in place under the pointer; leave it and it
 *   settles back where it was — no pop on entry, no jump on exit, no lag.
 * - Geometry is pure math in `lib/dockMagnify.ts`.
 * - Exactly one label: the hovered icon's, gliding from icon to icon at a
 *   fixed distance from the rail, fading in and out. Three labels at once,
 *   popping in and out on a size threshold, read as noise.
 * - A soft detent tick each time the pointer crosses onto another icon, and a
 *   firmer one on the pick — the ratchet of a picker wheel, quiet.
 *
 * Users who asked for less motion get the label and the tick but no hill.
 */
const BASE = 30; // px — icon box at rest
const GAP = 8; // px — between icon boxes at rest
const ICON = 16; // px — glyph at rest; scales with the box
const RAIL_WIDTH = 64; // px — Tailwind w-16, the column the icons centre in
/**
 * Space above the first and below the last icon at rest. Also what a
 * magnified end icon grows into — half its extra size, well under 12 px.
 */
const PAD_TOP = 12;
const PAD_BOTTOM = 12;
/** The rest geometry, for tests that need to aim a pointer at an icon. */
export const DECK_DOCK_GEOMETRY = { BASE, GAP, PAD_TOP } as const;
/** Enter/leave: quick, a hair under critical damping so it never wobbles. */
const HILL_SPRING = { stiffness: 600, damping: 45, mass: 1 };
/**
 * Where the label's left edge sits: a fixed distance beyond the fully grown
 * icon, so it does not creep sideways while the icon grows under it.
 */
const LABEL_LEFT = RAIL_WIDTH / 2 + (BASE * DOCK_MAX_SCALE) / 2 + 12;
/**
 * The label glides from icon to icon instead of hopping — critically damped,
 * so it settles in ~40 ms and never overshoots the icon it names.
 */
const LABEL_SPRING = { stiffness: 900, damping: 60, mass: 1 };

interface DockFrame {
  layout: DockLayout;
  hovered: number;
}

export function DeckDock({ className }: { className?: string }) {
  const t = useT();
  const activeSection = useEventStore((s) => s.activeSection);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const conversations = useEventStore((s) => s.conversations);
  const { health } = useSectionHealth();
  const pluginAttention = usePluginAttention();
  const reduced = useReducedMotion() ?? false;

  const items = useMemo(() => NAV_GROUPS.flat(), []);
  const groupBreaks = useMemo(() => {
    // Index of the first item of every group after the first — a hairline is
    // drawn above these so the dock keeps the sidebar's grouping.
    const breaks = new Set<number>();
    let n = 0;
    for (let g = 0; g < NAV_GROUPS.length; g++) {
      if (g > 0) breaks.add(n);
      n += NAV_GROUPS[g].length;
    }
    return breaks;
  }, []);

  const apikeysError = useMemo(
    () => Object.values(health).some((h) => h?.status === "error"),
    [health],
  );

  // --- pointer → motion values (no React state on the hot path) -----------
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const lastClientY = useRef<number | null>(null);
  const hoveredRef = useRef(-1);
  const [hovered, setHovered] = useState(-1);

  /** Pointer position along the rail, in the REST layout's coordinates. */
  const hillY = useMotionValue(0);
  /** 0 = rail at rest, 1 = hill fully up. Sprung; the position is not. */
  const hillTarget = useMotionValue(0);
  const hill = useSpring(hillTarget, HILL_SPRING);
  const hoveredMV = useMotionValue(-1);

  const frame = useTransform([hillY, hill, hoveredMV], (latest: number[]): DockFrame => {
    const [y, a, h] = latest;
    const amount = Math.min(1, Math.max(0, a));
    return {
      layout: layoutDock(
        items.length,
        BASE,
        GAP,
        amount > 0.002 ? y : null,
        1 + (DOCK_MAX_SCALE - 1) * amount,
        DOCK_RADIUS_UNITS,
      ),
      hovered: h,
    };
  });
  // Rest geometry — positions never move, so this is a plain number.
  const rest = useMemo(() => layoutDock(items.length, BASE, GAP, null), [items.length]);
  const blockHeight = PAD_TOP + rest.extent + PAD_BOTTOM;

  // --- the one label ------------------------------------------------------
  // The last position is kept so the label fades out where it was rather than
  // jumping to a default the instant the pointer leaves. Derived from `frame`
  // (rather than from the hovered index alone) so a scroll under a still
  // pointer, which re-aims the hill, moves the label along.
  const lastLabelTop = useRef(PAD_TOP + GAP + BASE / 2);
  const labelTopRaw = useTransform(frame, (f) => {
    if (f.hovered < 0) return lastLabelTop.current;
    const top = PAD_TOP + f.layout.items[f.hovered].center - (scrollerRef.current?.scrollTop ?? 0);
    lastLabelTop.current = top;
    return top;
  });
  const labelTopSmooth = useSpring(labelTopRaw, LABEL_SPRING);
  const labelTop = reduced ? labelTopRaw : labelTopSmooth;

  const setHoveredSlot = useCallback(
    (slot: number, tick: boolean) => {
      const prev = hoveredRef.current;
      if (slot === prev) return;
      hoveredRef.current = slot;
      hoveredMV.set(slot);
      setHovered(slot);
      if (prev < 0 && slot >= 0) {
        // A fresh label appears AT its icon; only a label that is already on
        // screen glides. Without this it would fly in from wherever the last
        // one faded out.
        labelTopSmooth.jump(
          PAD_TOP + rest.items[slot].center - (scrollerRef.current?.scrollTop ?? 0),
        );
      }
      if (tick && slot >= 0) playDockTick("hover");
    },
    [hoveredMV, labelTopSmooth, rest],
  );

  const track = useCallback(
    (clientY: number) => {
      const el = scrollerRef.current;
      if (!el) return;
      const y = clientY - el.getBoundingClientRect().top + el.scrollTop - PAD_TOP;
      hillY.set(y);
      hillTarget.set(reduced ? 0 : 1);
      setHoveredSlot(dockSlotAt(y, items.length, BASE, GAP), true);
    },
    [hillY, hillTarget, items.length, reduced, setHoveredSlot],
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
    hillTarget.set(0);
    setHoveredSlot(-1, false);
  }, [hillTarget, setHoveredSlot]);
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

  return (
    <nav
      aria-label={t("deck.sections")}
      className={cn("relative z-10 flex h-full w-16 shrink-0 flex-col", className)}
    >
      <div
        ref={scrollerRef}
        data-testid="deck-dock-rail"
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
        onScroll={onScroll}
        className="deck-dock-scroller relative h-full w-full overflow-y-auto overflow-x-hidden"
      >
        <div className="relative w-full" style={{ height: blockHeight }}>
          {items.map((item, i) => (
            <DockIcon
              key={item.id}
              item={item}
              index={i}
              restCenter={rest.items[i].center}
              frame={frame}
              label={resolveNavLabel(t, item)}
              active={activeSection === item.id || !!item.matchIds?.includes(activeSection)}
              hovered={hovered === i}
              live={item.id === "chats" && conversations.length > 0}
              attention={
                (item.id === "apikeys" && apikeysError) ||
                (item.id === "skills" && pluginAttention.count > 0)
              }
              groupBreak={groupBreaks.has(i)}
              onSelect={() => {
                playDockTick("select");
                setActiveSection(item.id);
              }}
              onFocus={() => setHoveredSlot(i, false)}
              onBlur={() => {
                if (hoveredRef.current === i && lastClientY.current === null) {
                  setHoveredSlot(-1, false);
                }
              }}
            />
          ))}
        </div>
      </div>

      {/* The label rides beside the hovered icon, outside the scroller so the
          scroller's clipping cannot cut it off. Decorative: every button
          already carries its name as aria-label. */}
      <AnimatePresence>
        {hoveredItem && (
          <motion.div
            key="label"
            aria-hidden
            data-testid="deck-dock-label"
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -4 }}
            transition={reduced ? { duration: 0 } : { duration: 0.12, ease: "easeOut" }}
            style={{ top: labelTop, left: LABEL_LEFT, y: "-50%" }}
            className="pointer-events-none absolute whitespace-nowrap rounded-md border border-border bg-background/95 px-2 py-1 text-xs text-foreground shadow-md backdrop-blur"
          >
            {resolveNavLabel(t, hoveredItem)}
            {hoveredCount > 0 && (
              <span className="ml-1.5 font-mono text-[10px] text-primary">{hoveredCount}</span>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </nav>
  );
}

function DockIcon({
  item,
  index,
  restCenter,
  frame,
  label,
  active,
  hovered,
  live,
  attention,
  groupBreak,
  onSelect,
  onFocus,
  onBlur,
}: {
  item: NavItem;
  index: number;
  /** The icon's fixed centre along the rail, px from the row's start. */
  restCenter: number;
  frame: MotionValue<DockFrame>;
  label: string;
  active: boolean;
  hovered: boolean;
  /** Something is going on in this section (a pip in the accent colour). */
  live: boolean;
  /** Something needs the user in this section (a pip in the alert colour). */
  attention: boolean;
  groupBreak: boolean;
  onSelect: () => void;
  onFocus: () => void;
  onBlur: () => void;
}) {
  // Each icon binds its box to the shared layout — written straight to the
  // element by the motion runtime, never through a React render. The centre
  // is fixed; the box grows around it, so `top` moves up exactly as `size`
  // grows.
  const top = useTransform(frame, (f) => PAD_TOP + restCenter - f.layout.items[index].size / 2);
  const size = useTransform(frame, (f) => f.layout.items[index].size);
  const glyph = useTransform(frame, (f) => ICON * f.layout.items[index].scale);
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
      <motion.button
        type="button"
        data-testid={`deck-dock-${item.id}`}
        onClick={onSelect}
        onFocus={onFocus}
        onBlur={onBlur}
        aria-label={label}
        aria-current={active ? "page" : undefined}
        className={cn(
          "absolute left-1/2 flex items-center justify-center rounded-xl border transition-colors duration-150",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
          // On top while hovered: at the peak its box overlaps the neighbours'
          // (transparent) boxes by a few px, and it must win that overlap.
          hovered && "z-10",
          active
            ? "border-primary/50 bg-primary/15 text-primary"
            : hovered
              ? "border-border/60 bg-card/40 text-foreground"
              : "border-transparent text-muted-foreground",
        )}
        style={{ top, width: size, height: size, x: "-50%" }}
      >
        <motion.span
          aria-hidden
          className="flex shrink-0 items-center justify-center"
          style={{ width: glyph, height: glyph }}
        >
          <Icon className="h-full w-full" />
        </motion.span>

        {(live || attention) && (
          <span
            aria-hidden
            className={cn(
              "absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full ring-2 ring-background",
              attention ? "bg-destructive" : "bg-primary",
            )}
          />
        )}
      </motion.button>
    </>
  );
}

export type { SectionId };
