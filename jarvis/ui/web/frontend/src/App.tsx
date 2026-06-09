import { useWebSocket } from "@/hooks/useWebSocket";
import { useBrainStatus } from "@/hooks/useBrainStatus";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { MainView } from "@/components/layout/MainView";
import { ToastLayer } from "@/components/ToastLayer";
import { CliConnectPoller } from "@/components/CliConnectPoller";
import { WakeWordOnboardingGate } from "@/components/onboarding/WakeWordOnboardingGate";

export default function App() {
  useWebSocket();
  useBrainStatus();

  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <div className="pointer-events-none fixed inset-0 jarvis-grid opacity-40" aria-hidden />
      <div
        className="pointer-events-none fixed right-[-10%] top-[-20%] h-[600px] w-[600px] jarvis-glow"
        aria-hidden
      />

      <Sidebar />

      <main className="relative z-10 flex min-w-0 flex-1 flex-col">
        <TopBar />
        <div className="min-h-0 flex-1">
          <MainView />
        </div>
      </main>

      <ToastLayer />
      {/* Hintergrund-Polling fuer CLI-OAuth-Logins — pollt /check alle 3s
          solange ein cliConnectCoach im Store gesetzt ist. */}
      <CliConnectPoller />
      {/* Blocking onboarding gate — overlays everything until wake word is set. */}
      <WakeWordOnboardingGate />
    </div>
  );
}
