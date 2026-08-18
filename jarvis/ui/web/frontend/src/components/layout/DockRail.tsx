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
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The icon rail — every section of the app as one icon, on the left edge,
 * with a gentle magnification under the pointer.
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
 * How the motion is built — the recipe of the well-known Framer Motion docks,
 * with two deliberate differences:
 *
 * - The column is RIGID. No icon ever leaves its place: the hill is a hill of
 *   sizes only, each icon growing around its own rest centre, neighbours a
 *   little, the hovered one most. The desktop docks push neighbours apart to
 *   make room; the maintainer found that shuffle distracting, so it is gone.
 *   Boxes may overlap by a few px at the peak — only the hovered box paints a
 *   surface, and it is drawn on top, so nothing shows.
 * - The hill is STEADY under a moving pointer. It sits on the hovered icon's
 *   centre, not on the pointer itself: moving within an icon changes nothing
 *   (a pointer that wanders by a pixel used to make every neighbour re-render
 *   at a fractional size, and thin line icons re-rasterised at fractional
 *   sizes read as vibration). Crossing to the next icon glides the hill over
 *   on a critically damped spring, in step with the label.
 * - Nothing about the pointer goes through React state. The pointer writes
 *   motion values; the whole layout is ONE derived motion value; every icon
 *   binds its box to that.
 * - The hill's HEIGHT is sprung too: enter the rail and it rises in place
 *   under the pointer; leave it and it settles back where it was.
 * - Geometry is pure math in `lib/dockMagnify.ts`.
 * - Exactly one label: the hovered icon's, gliding from icon to icon at a
 *   fixed distance from the rail, fading in and out.
 *
 * Users who asked for less motion get the label but no hill.
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
export const DOCK_RAIL_GEOMETRY = { BASE, GAP, PAD_TOP } as const;
/** Rise/settle of the hill: quick, a hair under critical damping. */
const HILL_SPRING = { stiffness: 600, damping: 45, mass: 1 };
/**
 * The glide from icon to icon — hill and label alike: critically damped, so
 * it settles in ~40 ms and never overshoots the icon it lands on.
 */
const GLIDE_SPRING = { stiffness: 900, damping: 60, mass: 1 };
/**
 * Where the label's left edge sits: a fixed distance beyond the fully grown
 * icon, so it does not creep sideways while the icon grows under it.
 */
const LABEL_LEFT = RAIL_WIDTH / 2 + (BASE * DOCK_MAX_SCALE) / 2 + 12;

interface DockFrame {
  layout: DockLayout;
  hovered: number;
}

export function DockRail({ className }: { className?: string }) {
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

  /** Centre of the hill along the rail, in the rest layout's coordinates —
   *  the hovered icon's centre, glided to on `GLIDE_SPRING`. */
  const hillYTarget = useMotionValue(0);
  const hillY = useSpring(hillYTarget, GLIDE_SPRING);
  /** 0 = rail at rest, 1 = hill fully up. */
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
  const labelTopSmooth = useSpring(labelTopRaw, GLIDE_SPRING);
  const labelTop = reduced ? labelTopRaw : labelTopSmooth;

  const setHoveredSlot = useCallback(
    (slot: number) => {
      const prev = hoveredRef.current;
      if (slot === prev) return;
      hoveredRef.current = slot;
      hoveredMV.set(slot);
      setHovered(slot);
      if (slot < 0) return;
      const center = rest.items[slot].center;
      if (prev < 0) {
        // A fresh hover appears AT its icon; only a hill (and label) that is
        // already up glides. Without this they would fly in from wherever the
        // last hover faded out.
        hillY.jump(center);
        labelTopSmooth.jump(PAD_TOP + center - (scrollerRef.current?.scrollTop ?? 0));
      }
      hillYTarget.set(center);
    },
    [hillY, hillYTarget, hoveredMV, labelTopSmooth, rest],
  );

  const track = useCallback(
    (clientY: number) => {
      const el = scrollerRef.current;
      if (!el) return;
      const y = clientY - el.getBoundingClientRect().top + el.scrollTop - PAD_TOP;
      const slot = dockSlotAt(y, items.length, BASE, GAP);
      setHoveredSlot(slot);
      // Off the row (the padding, or past the last icon) counts as away.
      hillTarget.set(slot >= 0 && !reduced ? 1 : 0);
    },
    [hillTarget, items.length, reduced, setHoveredSlot],
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
    setHoveredSlot(-1);
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
  const hoveredHint =
    hoveredItem?.id === "apikeys" && apikeysError
      ? t("sidebar.apikeys_alert")
      : hoveredItem?.id === "skills" && pluginsNeedReconnect
        ? pluginWarnHint
        : null;

  return (
    <nav
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
              index={i}
              restCenter={rest.items[i].center}
              frame={frame}
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

      {/* The label rides beside the hovered icon, outside the scroller so the
          scroller's clipping cannot cut it off. Decorative: every button
          already carries its name as aria-label. */}
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
            style={{ top: labelTop, left: LABEL_LEFT, y: "-50%" }}
            className="pointer-events-none absolute flex items-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-background/95 px-2 py-1 text-xs text-foreground shadow-md backdrop-blur"
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
  index: number;
  /** The icon's fixed centre along the rail, px from the row's start. */
  restCenter: number;
  frame: MotionValue<DockFrame>;
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
        data-testid={`nav-row-${item.id}`}
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
          // The active control is the app's glass surface with the accent on
          // the glyph — the same language as the expanded sidebar's row.
          active
            ? "jarvis-message-surface border-primary/40 text-primary"
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
            className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-amber-500 ring-2 ring-background"
          />
        ) : live ? (
          <span
            aria-hidden
            className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-primary ring-2 ring-background"
          />
        ) : null}
      </motion.button>
    </>
  );
}

export type { SectionId };
