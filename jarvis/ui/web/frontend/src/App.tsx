import { Suspense, lazy, useCallback, useEffect, useState } from "react";

import { isSectionId, useEventStore } from "@/store/events";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useBrainStatus } from "@/hooks/useBrainStatus";
import { useVoiceStatus } from "@/hooks/useVoiceStatus";
import { useAssistantNameSeed } from "@/hooks/useAssistantNameSeed";
import { useCodingMode } from "@/hooks/useCodingMode";
import { useSectionUrlMemory } from "@/hooks/useSectionUrlMemory";
import {
  Sidebar,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_RAIL_WIDTH,
} from "@/components/layout/Sidebar";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { useResizablePane } from "@/hooks/useResizablePane";
import { TopBar } from "@/components/layout/TopBar";
import { PermissionsAlertBanner } from "@/components/layout/PermissionsAlertBanner";
import { ReadyCelebration } from "@/components/ReadyCelebration";
import { InputIsolationBanner } from "@/components/layout/InputIsolationBanner";
import { VoiceWarmingBanner } from "@/components/layout/VoiceWarmingBanner";
import { MainView } from "@/components/layout/MainView";
import { ToastLayer } from "@/components/ToastLayer";
import { CommandActivityLayer } from "@/components/CommandActivityLayer";
import { EditContextMenu } from "@/components/EditContextMenu";
/*
  Lazy on purpose. The overlay pulls in the dialog primitives, the keyboard
  layout table and the keybind hook — none of which anything needs before
  someone presses `?`. Imported statically it put the startup chunk over the
  bundle budget, and every byte there is parsed before the UI paints. Only the
  key guard below stays eager, and it is a pure function over the event.
*/
const ShortcutOverlay = lazy(() =>
  import("@/components/ShortcutOverlay").then((m) => ({ default: m.ShortcutOverlay })),
);
import { shouldOpenShortcutOverlay } from "@/lib/shortcutOverlayTrigger";
import { JarvisDock } from "@/components/JarvisDock";
import { CliConnectPoller } from "@/components/CliConnectPoller";
import { OnboardingGate } from "@/components/onboarding/OnboardingGate";
import { installDictationFocusTracker } from "@/lib/dictationTarget";
import { SubscriptionRealtimeTransportBroker } from "@/components/voice/SubscriptionRealtimeTransportBroker";
import { MascotGigi } from "@/components/MascotGigi";
import { useDesktopWallpaper } from "@/hooks/useDesktopWallpaper";
import { installWallpaperSync, useWallpaperStore } from "@/store/wallpaper";
import { cn } from "@/lib/utils";

/** Where the collapsed/expanded choice for the nav sidebar is remembered. */
const NAV_COLLAPSED_KEY = "jarvis.sidebar.collapsed.v1";

/**
 * The shared visual ground for every app section.
 *
 * The image and its theme veil are separate layers: the artwork can stay crisp
 * while light and dark mode independently tune contrast through semantic theme
 * channels in index.css. Focused workspaces keep their content surfaces on top.
 *
 * Which artwork is shown belongs to the user — see the Wallpaper section. The
 * picture that ships with the app remains the default, and the one every
 * failure path returns to. Gigi is a separate live layer on that ground, not
 * painted into the picture — and he only sits on Chats (`hideMascot` everywhere
 * else). Work sections were showing a second ghost in the empty corner, and
 * the mission deck's ring already owns that same spot (maintainer, 2026-08-19).
 */
function DesktopWallpaper({ hideMascot = false }: { hideMascot?: boolean }) {
  const wallpaperUrl = useDesktopWallpaper();
  const mascotOn = useWallpaperStore((state) => state.mascotOn);
  const background = useWallpaperStore((state) => state.background);
  // A flat theme colour is the default ground (lib/backgroundMode.ts): the
  // picture, its veil and the mascot layer exist only once a wallpaper is
  // switched on. index.css keys the readability floor on the same choice.
  if (background !== "wallpaper") return null;
  return (
    <div
      aria-hidden
      data-testid="jarvis-desktop-wallpaper"
      className="pointer-events-none absolute inset-0 -z-10 overflow-hidden"
    >
      <div
        className="jarvis-desktop-wallpaper absolute inset-0"
        style={{ backgroundImage: `url(${wallpaperUrl})` }}
      />
      {mascotOn && !hideMascot ? (
        <div
          className="jarvis-desktop-wallpaper-mascot"
          data-testid="jarvis-desktop-wallpaper-mascot"
        >
          <MascotGigi size={200} reactToVoice enableComments={false} />
        </div>
      ) : null}
      <div className="jarvis-desktop-wallpaper-veil absolute inset-0" />
    </div>
  );
}

/**
 * The surface the active section is drawn on.
 *
 * Two grounds, because the app has two kinds of screen. Everything the app
 * writes itself sits on the wallpaper and gets the readability floor that comes
 * with it (`.jarvis-section-stage` in index.css). The Visualization section
 * shows pictures somebody ELSE produced — a rendered chart, a diagram, a framed
 * page — and the same floor damages those: an inherited text halo lands on the
 * labels inside an SVG, and busy artwork behind a transparent PNG makes it
 * impossible to tell what the picture actually looks like.
 *
 * The choice belongs to the shell rather than to the view. The stage is the
 * surface a section is laid out ON, and it has to hold in BOTH shapes this
 * component renders: the full window with nav and top bar, and the chrome-less
 * detached window where the section is the whole screen.
 *
 * Exported for its own test: rendering the whole shell to assert one class
 * would drag in the WebSocket, the wallpaper sync and the onboarding gate.
 */
export function SectionStage({
  visualization,
  children,
}: {
  visualization: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "min-h-0 flex-1",
        visualization ? "jarvis-visualization-stage" : "jarvis-section-stage",
      )}
      data-testid={visualization ? "jarvis-visualization-stage" : "jarvis-section-stage"}
    >
      {children}
    </div>
  );
}

export default function App() {
  /*
    `?` opens the shortcut overlay. Registered here rather than per view so it
    works everywhere, and guarded by shouldOpenShortcutOverlay so it never eats
    a question mark typed into a message, a search box or a terminal pane.
    Escape and focus restoration are the dialog's own.
  */
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!shouldOpenShortcutOverlay(event)) return;
      event.preventDefault();
      setShortcutsOpen(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useWebSocket();
  useBrainStatus();
  useVoiceStatus();
  useAssistantNameSeed();
  useCodingMode();
  /*
   * A reload puts the user back on the section they were on.
   *
   * The window reloads itself these days — a rebuilt frontend is picked up by
   * lib/bundleWatch instead of by restarting the application — and every one of
   * those reloads used to land on the default section, because the active one
   * lived only in the store. It is written into the address instead, which the
   * reload carries over. See hooks/useSectionUrlMemory.
   */
  useSectionUrlMemory();

  /*
   * Remember which field the user is typing in, app-wide.
   *
   * A dictation into Jarvis's own window is delivered inside the page rather
   * than by synthetic keystrokes, and the target is decided when the transcript
   * arrives — by which time a dictation started from a button has already moved
   * focus onto that button. Tracking focus here is what lets the text still
   * land where the caret was. Mounted at the shell so it spans every section,
   * including the terminals of the Agentic IDE.
   */
  useEffect(() => installDictationFocusTracker(), []);

  /*
   * Follow the wallpaper choice across windows. Picking one in the main window
   * should not leave a detached window wearing the previous picture until it
   * happens to reload.
   */
  useEffect(() => installWallpaperSync(), []);

  /*
   * Resync the detached-window registry once per document. The WS events keep
   * it live afterwards; this fetch covers the window that (re)loaded after the
   * events fired — preload recovery reloads every window after a frontend
   * rebuild, and a fresh solo window mounts long after its own "opened" event.
   */
  useEffect(() => {
    let cancelled = false;
    void fetch("/api/window/detached")
      .then(async (res) => {
        if (!res.ok) return;
        const data = (await res.json().catch(() => null)) as {
          views?: unknown[];
        } | null;
        if (cancelled || !Array.isArray(data?.views)) return;
        useEventStore.getState().setDetachedViews(data.views.filter(isSectionId));
      })
      .catch(() => {
        /* headless or warming backend — the WS events will catch us up */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /*
   * The sidebar is draggable, app-wide.
   *
   * It matters most in the Agentic IDE — every pixel taken from the nav is a
   * pixel of agent output — but the seam belongs to the shell rather than to
   * one view: a width that snapped back the moment you left the IDE would be a
   * different bug.
   *
   * The lower bound is the icon RAIL, not the narrowest width labels survive at.
   * Stopping at 200 px meant a workspace of a dozen terminals still gave a fifth
   * of the window to a nav list nobody was reading; the sidebar now collapses to
   * its icons instead (see `SIDEBAR_RAIL_AT_WIDTH`), and a double-click on the
   * seam brings the full 280 px back.
   */
  const sidebar = useResizablePane({
    storageKey: "jarvis.sidebar.width.v1",
    defaultSize: SIDEBAR_DEFAULT_WIDTH,
    min: SIDEBAR_RAIL_WIDTH,
    max: 520,
  });

  /*
   * The sidebar starts EXPANDED (2026-08-23 — it used to start as the icon
   * rail). The front page's own controls live in it now: the Voice | Chat
   * switch, "New chat", the recent runs and chats. A rail would hide the one
   * switch the page is built around. Collapsing is still one click away and
   * is remembered.
   *
   * Persisted separately from the drag width on purpose: expanding restores the
   * column the user sized, not the designed default. A storage read that throws
   * (private-mode WebView, storage disabled) degrades to the expanded default
   * rather than taking the shell down with it.
   */
  const [navCollapsed, setNavCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(NAV_COLLAPSED_KEY) === "true";
    } catch {
      return false;
    }
  });
  const toggleNav = useCallback(() => {
    setNavCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(NAV_COLLAPSED_KEY, next ? "true" : "false");
      } catch {
        /* storage unavailable — the choice simply does not survive a restart */
      }
      return next;
    });
  }, []);
  // Dragging the seam is itself an "I want the sidebar" gesture: a drag that
  // left the rail state behind would snap straight back to icons and read as a
  // broken handle.
  const startSidebarResize = useCallback(
    (event: React.PointerEvent) => {
      if (navCollapsed) {
        setNavCollapsed(false);
        try {
          window.localStorage.setItem(NAV_COLLAPSED_KEY, "false");
        } catch {
          /* see above */
        }
      }
      sidebar.startResize(event);
    },
    [navCollapsed, sidebar],
  );

  const activeSection = useEventStore((s) => s.activeSection);
  const solo = useEventStore((s) => s.solo);
  const detachedViews = useEventStore((s) => s.detachedViews);

  /*
   * The realtime broker must exist exactly ONCE across all windows: it
   * registers WebRTC offers for the native voice session, and two live brokers
   * publish competing offers. Ownership follows the voice surface — a detached
   * Voice window brings its own broker (the desktop shell injects the broker
   * token only there), and the main window stands down while that window
   * exists. Every other solo window never mounts one.
   */
  const brokerMounted = solo
    ? activeSection === "chats"
    : !detachedViews.includes("chats");

  // Which ground the section is drawn on — see `SectionStage`. Read here rather
  // than inside it so both window shapes below get the same answer from one
  // place.
  const visualizationActive = activeSection === "visualization";

  /*
   * A detached solo window is one section in its own desktop window: the
   * section IS the window, so the app chrome around it — nav, top bar,
   * onboarding, dock — has no job here and is omitted rather than hidden.
   * What stays is what any surface needs to function: toasts (many components
   * report through them) and the right-click edit menu (the desktop WebView
   * has no native context menu, so this is the only mouse-driven paste).
   */
  /*
   * Gigi belongs on the Chats ground only. Every other section was painting
   * the same live ghost into empty corners (Tasks, Wiki, Profile, …). The
   * wallpaper picture stays; only the mascot layer steps off.
   */
  const hideMascot = activeSection !== "chats";

  if (solo) {
    return (
      <div className="relative isolate flex h-screen w-screen overflow-hidden bg-background text-foreground">
        <DesktopWallpaper hideMascot={hideMascot} />
        {brokerMounted && <SubscriptionRealtimeTransportBroker />}
        {/* No z-index on the stage column — see the shell below. */}
        <main className="relative flex min-w-0 flex-1 flex-col">
          <SectionStage visualization={visualizationActive}>
            <MainView />
          </SectionStage>
        </main>
        <ToastLayer />
        <CommandActivityLayer />
        <EditContextMenu />
        {shortcutsOpen && (
          <Suspense fallback={null}>
            <ShortcutOverlay open onOpenChange={setShortcutsOpen} />
          </Suspense>
        )}
      </div>
    );
  }

  return (
    <div className="relative isolate flex h-screen w-screen overflow-hidden bg-background text-foreground">
      {brokerMounted && <SubscriptionRealtimeTransportBroker />}
      <DesktopWallpaper hideMascot={hideMascot} />

      <Sidebar
        width={sidebar.size}
        collapsed={navCollapsed}
        onToggleCollapsed={toggleNav}
      />

      <PaneResizer
        orientation="vertical"
        onPointerDown={startSidebarResize}
        onDoubleClick={sidebar.reset}
        onNudge={sidebar.nudge}
        active={sidebar.isResizing}
        title="Drag to resize the sidebar — double-click to reset"
      />

      {/*
        The stage column carries NO z-index, and must not get one back.

        A positive z-index here makes <main> its own stacking context, and
        then every full-screen overlay a section renders inside it — the
        wallpaper preview, the view dialogs, their scrims — is trapped at
        THAT level however high its own z-index climbs. The nav column
        beside it (z-20) then paints over the overlay: the sidebar logo,
        the assistant name and the voice state landed on top of the
        wallpaper preview's own header, one line of text over the other.

        `relative` alone is all the column needs: it stays above the
        wallpaper layer because that one is negative (-z-10), and the
        overlays inside reach the app's real dialog levels. Guarded by
        components/layout/overlay-stacking.test.ts.
      */}
      <main className="relative flex min-w-0 flex-1 flex-col">
        {/* App-wide macOS permission alert — topmost so a missing grant is
            impossible to miss on any view. No-op on other platforms. */}
        <PermissionsAlertBanner />
        {/* Outside input software (dictation, text expanders, auto-type) cannot
            reach an elevated window. Sits next to the permission alert because
            it is the same class of problem: an OS-level gate the user must be
            told about, since nothing else reports it. */}
        <InputIsolationBanner />
        <TopBar />
        <VoiceWarmingBanner />
        {/* The one-time "all lights green" note — the first time every
            section of the active voice mode answers. Never again after. */}
        <ReadyCelebration />
        <SectionStage visualization={visualizationActive}>
          <MainView />
        </SectionStage>
      </main>

      <ToastLayer />
      <CommandActivityLayer />
      {/* Right-click Cut/Copy/Paste. The desktop WebView ships with its own
          context menu disabled, so without this there is no mouse-driven paste
          anywhere in the app — including the IDE terminals. */}
      <EditContextMenu />
      {/* Always-present "Jarvis presence" drop dock — drag a mission/output
          card here to pull it into the live conversation context. */}
      <JarvisDock />
      {/* Background polling for CLI OAuth logins — polls /check every 3s
          as long as a cliConnectCoach is set in the store. */}
      <CliConnectPoller />
      {/* Blocking onboarding gate — overlays everything until first-run setup is complete. */}
      <OnboardingGate />
      {/* `?` anywhere in the app opens this; the chunk loads on first use. */}
      {shortcutsOpen && (
        <Suspense fallback={null}>
          <ShortcutOverlay open onOpenChange={setShortcutsOpen} />
        </Suspense>
      )}
    </div>
  );
}
