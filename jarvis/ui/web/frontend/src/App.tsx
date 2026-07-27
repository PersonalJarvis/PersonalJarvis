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

export default function App() {
  useWebSocket();
  useBrainStatus();
  useVoiceStatus();
  useAssistantNameSeed();
  useCodingMode();

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

  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <div className="pointer-events-none fixed inset-0 jarvis-grid opacity-40" aria-hidden />
      <div
        className="pointer-events-none fixed right-[-10%] top-[-20%] h-[600px] w-[600px] jarvis-glow"
        aria-hidden
      />

      <Sidebar width={sidebar.size} />

      <PaneResizer
        orientation="vertical"
        onPointerDown={sidebar.startResize}
        onDoubleClick={sidebar.reset}
        onNudge={sidebar.nudge}
        active={sidebar.isResizing}
        title="Drag to resize the sidebar — double-click to reset"
      />

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
