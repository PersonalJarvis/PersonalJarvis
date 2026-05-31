import { useEventStore } from "@/store/events";
import { ViewErrorBoundary } from "@/components/ViewErrorBoundary";
import { BoardView } from "@/views/BoardView";
import { ChatsView } from "@/views/ChatsView";
import { SubAgentsView } from "@/views/SubAgentsView";
import { SkillsView } from "@/views/SkillsView";
import { PluginsView } from "@/views/PluginsView";
import { DocsView } from "@/views/DocsView";
import { McpsView } from "@/views/McpsView";
import { TasksView } from "@/views/TasksView";
import { LanguagesView } from "@/views/LanguagesView";
import { ProfileView } from "@/views/ProfileView";
import { WikiView } from "@/views/WikiView";
import { ApiKeysView } from "@/views/ApiKeysView";
import { SettingsView } from "@/views/SettingsView";
import { DebugView } from "@/views/DebugView";
import { TerminalView } from "@/views/TerminalView";
import { ClisView } from "@/views/ClisView";
import { CliTestHubView } from "@/views/CliTestHubView";
import { OutputsView } from "@/views/OutputsView";
import { ReviewView } from "@/views/ReviewView";
import { SessionsView } from "@/views/SessionsView";
import { TelephonyView } from "@/views/TelephonyView";
import { useEffect, useState } from "react";

/**
 * Hauptbereich rechts neben der Sidebar. Renderlogik:
 *
 * - Die meisten Views werden klassisch geswitcht (eine View aktiv, andere
 *   unmounted) — das spart Render-Last und ist semantisch in Ordnung,
 *   weil sie ihren State aus React Query/Store rehydrieren koennen.
 *
 * - **TerminalView wird nach dem ersten Oeffnen mounted gehalten** und dann
 *   nur ueber CSS-Display ein/ausgeblendet. Begruendung: die Component haelt
 *   eine echte xterm.js-Instanz + PTY-Session (Backend-Process) im
 *   Frontend-State.
 *   Bei klassischem Unmount wuerde der Cleanup-Effect `terminal.close`
 *   senden und den ganzen Output + die Session wegwerfen — der User landet
 *   bei jedem Wechsel "CLIs → Terminal" auf einer frischen, leeren Shell.
 *
 *   Das war der Bug, den der User als "wird rausgeworfen aus der
 *   Terminal-Sitzung" wahrgenommen hat: die Sektion fuehlte sich
 *   temporaer an, weil sie tatsaechlich bei jedem Section-Wechsel
 *   neu erzeugt wurde. Mit Lazy-then-Persistent-Mount bleibt der Install-
 *   Output und die Login-Folge sichtbar, ohne xterm in einem unsichtbaren
 *   Container schon beim App-Start zu initialisieren.
 */
export function MainView() {
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);
  const [terminalMounted, setTerminalMounted] = useState(active === "terminal");

  useEffect(() => {
    if (active === "terminal") setTerminalMounted(true);
  }, [active]);

  return (
    <>
      {terminalMounted && (
        <div
          className="h-full"
          style={{ display: active === "terminal" ? "block" : "none" }}
        >
          <ViewErrorBoundary
            viewName="Terminal"
            resetKey={active}
            onRecover={() => setActive("chats")}
          >
            <TerminalView />
          </ViewErrorBoundary>
        </div>
      )}

      {/* Switching: alle anderen Views — eine pro Tick. */}
      {active !== "terminal" && (
        <ViewErrorBoundary
          viewName={active}
          resetKey={active}
          onRecover={() => setActive("chats")}
        >
          <SwitchOnActiveSection active={active} />
        </ViewErrorBoundary>
      )}
    </>
  );
}

function SwitchOnActiveSection({ active }: { active: string }) {
  switch (active) {
    case "chats":
      return <ChatsView />;
    case "agents":
      return <SubAgentsView />;
    case "skills":
      return <SkillsView />;
    case "plugins":
      return <PluginsView />;
    case "docs":
      return <DocsView />;
    case "mcps":
      return <McpsView />;
    case "tasks":
      return <TasksView />;
    case "sessions":
      return <SessionsView />;
    case "clis":
      return <ClisView />;
    case "cli-test-hub":
      return <CliTestHubView />;
    case "board":
      return <BoardView />;
    case "languages":
      return <LanguagesView />;
    case "profile":
      return <ProfileView />;
    case "memory":
      return <WikiView />;
    case "apikeys":
      return <ApiKeysView />;
    case "settings":
      return <SettingsView />;
    case "telephony":
      return <TelephonyView />;
    case "debug":
      return <DebugView />;
    case "outputs":
      return <OutputsView />;
    case "review":
      return <ReviewView />;
    default:
      return <ChatsView />;
  }
}
