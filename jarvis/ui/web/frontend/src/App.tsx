import { useCallback, useEffect, useState } from "react";

import { useEventStore } from "@/store/events";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useBrainStatus } from "@/hooks/useBrainStatus";
import { useVoiceStatus } from "@/hooks/useVoiceStatus";
import { useAssistantNameSeed } from "@/hooks/useAssistantNameSeed";
import { useCodingMode } from "@/hooks/useCodingMode";
import {
  Sidebar,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_RAIL_WIDTH,
} from "@/components/layout/Sidebar";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { useResizablePane } from "@/hooks/useResizablePane";
import { TopBar } from "@/components/layout/TopBar";
import { PermissionsAlertBanner } from "@/components/layout/PermissionsAlertBanner";
import { InputIsolationBanner } from "@/components/layout/InputIsolationBanner";
import { VoiceWarmingBanner } from "@/components/layout/VoiceWarmingBanner";
import { MainView } from "@/components/layout/MainView";
import { ToastLayer } from "@/components/ToastLayer";
import { EditContextMenu } from "@/components/EditContextMenu";
import { JarvisDock } from "@/components/JarvisDock";
import { CliConnectPoller } from "@/components/CliConnectPoller";
import { OnboardingGate } from "@/components/onboarding/OnboardingGate";
import { installDictationFocusTracker } from "@/lib/dictationTarget";
import { SubscriptionRealtimeTransportBroker } from "@/components/voice/SubscriptionRealtimeTransportBroker";

/** Where the collapsed/expanded choice for the nav sidebar is remembered. */
const NAV_COLLAPSED_KEY = "jarvis.sidebar.collapsed.v1";

/**
 * The sections that open WITHOUT the app's module rail — and without the
 * decorative backdrop behind them.
 *
 * These screens are a workspace, not a page of the app: what is on them is the
 * user's own project and a coding agent working in it. A column of twenty
 * section icons beside that is the app talking about itself, and the grid and
 * glow washing over it are decoration competing with content that changes every
 * second. Both step aside here and stay exactly as they are everywhere else.
 */
const CODING_SECTIONS = new Set(["agentic-ide", "chat-workspace", "agentic-ide-classic"]);

export default function App() {
  useWebSocket();
  useBrainStatus();
  useVoiceStatus();
  useAssistantNameSeed();
  useCodingMode();

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
   * The sidebar starts COLLAPSED.
   *
   * Navigation is how you get to the thing, never the thing itself, and the
   * window is at its most useful when the work has the width. The icons stay on
   * screen, each with its label on hover, so nothing becomes unreachable — this
   * is a narrower sidebar, not a hidden one.
   *
   * Persisted separately from the drag width on purpose: expanding restores the
   * column the user sized, not the designed default. A storage read that throws
   * (private-mode WebView, storage disabled) degrades to the collapsed default
   * rather than taking the shell down with it.
   */
  const [navCollapsed, setNavCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(NAV_COLLAPSED_KEY) !== "false";
    } catch {
      return true;
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

  /*
   * On a coding surface the rail is gone until it is asked for.
   *
   * Not merely narrower — absent, along with the seam beside it. The button
   * that brings it back sits inside the coding surface itself (see
   * `NavRevealButton`), where the eye already is; parking it on the app chrome
   * would mean looking away from the work to find the way back to it.
   */
  const activeSection = useEventStore((s) => s.activeSection);
  const navRevealed = useEventStore((s) => s.navRevealed);
  const codingSurface = CODING_SECTIONS.has(activeSection);
  const railHidden = codingSurface && !navRevealed;

  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <SubscriptionRealtimeTransportBroker />
      {!codingSurface && (
        <>
          <div className="pointer-events-none fixed inset-0 jarvis-grid opacity-40" aria-hidden />
          <div
            className="pointer-events-none fixed right-[-10%] top-[-20%] h-[600px] w-[600px] jarvis-glow"
            aria-hidden
          />
        </>
      )}

      {!railHidden && (
        <>
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
        </>
      )}

      <main className="relative z-10 flex min-w-0 flex-1 flex-col">
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
        <div className="min-h-0 flex-1">
          <MainView />
        </div>
      </main>

      <ToastLayer />
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
    </div>
  );
}
