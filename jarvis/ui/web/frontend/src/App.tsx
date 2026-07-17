import { useWebSocket } from "@/hooks/useWebSocket";
import { useBrainStatus } from "@/hooks/useBrainStatus";
import { useVoiceStatus } from "@/hooks/useVoiceStatus";
import { useAssistantNameSeed } from "@/hooks/useAssistantNameSeed";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { PermissionsAlertBanner } from "@/components/layout/PermissionsAlertBanner";
import { VoiceWarmingBanner } from "@/components/layout/VoiceWarmingBanner";
import { MainView } from "@/components/layout/MainView";
import { ToastLayer } from "@/components/ToastLayer";
import { JarvisDock } from "@/components/JarvisDock";
import { CliConnectPoller } from "@/components/CliConnectPoller";
import { OnboardingGate } from "@/components/onboarding/OnboardingGate";

export default function App() {
  useWebSocket();
  useBrainStatus();
  useVoiceStatus();
  useAssistantNameSeed();

  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <div className="pointer-events-none fixed inset-0 jarvis-grid opacity-40" aria-hidden />
      <div
        className="pointer-events-none fixed right-[-10%] top-[-20%] h-[600px] w-[600px] jarvis-glow"
        aria-hidden
      />

      <Sidebar />

      <main className="relative z-10 flex min-w-0 flex-1 flex-col">
        {/* App-wide macOS permission alert — topmost so a missing grant is
            impossible to miss on any view. No-op on other platforms. */}
        <PermissionsAlertBanner />
        <TopBar />
        <VoiceWarmingBanner />
        <div className="min-h-0 flex-1">
          <MainView />
        </div>
      </main>

      <ToastLayer />
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
