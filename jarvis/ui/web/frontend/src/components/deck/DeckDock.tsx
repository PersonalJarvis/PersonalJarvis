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
  maxAnchorLift,
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
 * docks, tuned to how the desktop docks actually behave:
 *
 * - Nothing about the pointer goes through React state. The pointer writes a
 *   motion value; the whole layout is ONE derived motion value; every icon
 *   binds its box to that. Re-rendering twenty-odd buttons on every mouse
 *   event, and then letting a CSS transition chase the result, is what made
 *   the first version stutter and trail the mouse.
 * - The hill follows the pointer instantly, like the real thing; only its
 *   HEIGHT is sprung. Enter the rail and the hill rises in place under the
 *   pointer; leave it and it settles back where it was — no pop on entry,
 *   no jump on exit, and no lag while inside.
 * - Geometry (the hill, the push-apart, the anchoring that keeps the icon
 *   under the pointer under the pointer) is pure math in `lib/dockMagnify.ts`.
 * - Exactly one label: the hovered icon's, sliding along the rail with it and
 *   fading in and out. Three labels at once, popping in and out on a size
 *   threshold, read as noise.
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
 * Space above the first icon at rest. Also the headroom the anchoring lifts
 * the row into while magnified — computed from the geometry so a change to
 * the hill can never quietly leave the anchoring clipped.
 */
const PAD_TOP = Math.max(12, maxAnchorLift(BASE, GAP));
const PAD_BOTTOM = 12;
/** The rest geometry, for tests that need to aim a pointer at an icon. */
export const DECK_DOCK_GEOMETRY = { BASE, GAP, PAD_TOP } as const;
/** Enter/leave: quick, a hair under critical damping so it never wobbles. */
const HILL_SPRING = { stiffness: 600, damping: 45, mass: 1 };
/** How far the label sits from the icon's edge. */
const LABEL_GAP = 12;
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
        PAD_TOP,
      ),
      hovered: h,
    };
  });
  const blockHeight = useTransform(frame, (f) => PAD_TOP + f.layout.extent + PAD_BOTTOM);

  // --- the one label ------------------------------------------------------
  // The last position is kept so the label fades out where it was rather than
  // jumping to a default the instant the pointer leaves.
  const lastLabelPos = useRef({
    top: PAD_TOP + GAP + BASE / 2,
    left: RAIL_WIDTH / 2 + BASE / 2 + LABEL_GAP,
  });
  const labelTopRaw = useTransform(frame, (f) => {
    if (f.hovered < 0) return lastLabelPos.current.top;
    const it = f.layout.items[f.hovered];
    const top = PAD_TOP + it.center - (scrollerRef.current?.scrollTop ?? 0);
    lastLabelPos.current.top = top;
    return top;
  });
  const labelTopSmooth = useSpring(labelTopRaw, LABEL_SPRING);
  const labelTop = reduced ? labelTopRaw : labelTopSmooth;
  const labelLeft = useTransform(frame, (f) => {
    if (f.hovered < 0) return lastLabelPos.current.left;
    const left = RAIL_WIDTH / 2 + f.layout.items[f.hovered].size / 2 + LABEL_GAP;
    lastLabelPos.current.left = left;
    return left;
  });

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
        // one faded out. Derived values update on the next frame, so the rest
        // centre is used — the glide closes the last few px itself.
        labelTopSmooth.jump(
          PAD_TOP + GAP + slot * (BASE + GAP) + BASE / 2 - (scrollerRef.current?.scrollTop ?? 0),
        );
      }
      if (tick && slot >= 0) playDockTick("hover");
    },
    [hoveredMV, labelTopSmooth],
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
        {/* Height follows the magnified extent so the row never clips. */}
        <motion.div className="relative w-full" style={{ height: blockHeight }}>
          {items.map((item, i) => (
            <DockIcon
              key={item.id}
              item={item}
              index={i}
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
        </motion.div>
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
            style={{ top: labelTop, left: labelLeft, y: "-50%" }}
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
  // element by the motion runtime, never through a React render.
  const top = useTransform(frame, (f) => {
    const it = f.layout.items[index];
    return PAD_TOP + it.center - it.size / 2;
  });
  const size = useTransform(frame, (f) => f.layout.items[index].size);
  const glyph = useTransform(frame, (f) => ICON * f.layout.items[index].scale);
  const ruleTop = useTransform(frame, (f) => {
    const it = f.layout.items[index];
    return PAD_TOP + it.center - it.size / 2 - GAP / 2 - 0.5;
  });
  const Icon = item.icon;

  return (
    <>
      {groupBreak && (
        <motion.span
          aria-hidden
          className="absolute left-4 right-4 h-px bg-border"
          style={{ top: ruleTop }}
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
