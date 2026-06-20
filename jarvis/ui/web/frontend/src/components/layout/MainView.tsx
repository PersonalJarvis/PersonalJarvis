import { useEventStore } from "@/store/events";
import { ViewErrorBoundary } from "@/components/ViewErrorBoundary";
import { BoardView } from "@/views/BoardView";
import { ChatsView } from "@/views/ChatsView";
import { SubAgentsView } from "@/views/SubAgentsView";
import { ExtensionsView } from "@/views/ExtensionsView";
import { DocsView } from "@/views/DocsView";
import { TasksView } from "@/views/TasksView";
import { ProfileView } from "@/views/ProfileView";
import { WikiView } from "@/views/WikiView";
import { ApiKeysView } from "@/views/ApiKeysView";
import { SettingsView } from "@/views/SettingsView";
import { ClisHubView } from "@/views/ClisHubView";
import { OutputsView } from "@/views/OutputsView";
import { RunInspectorView } from "@/views/RunInspectorView";
import { SessionsView } from "@/views/SessionsView";
import { SocialsView } from "@/views/socials/SocialsView";
import { ContactsView } from "@/views/contacts/ContactsView";
import BrowserVoiceView from "@/views/BrowserVoiceView";
import { AgentInstructionsView } from "@/views/AgentInstructionsView";
import { TelephonySetupView } from "@/views/TelephonyView";

/**
 * Main area to the right of the sidebar. All views are switched the classic
 * way (one view active, the others unmounted) — this saves render load and is
 * semantically fine because they rehydrate their state from React Query / the
 * store.
 */
export function MainView() {
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);

  return (
    <ViewErrorBoundary
      viewName={active}
      resetKey={active}
      onRecover={() => setActive("chats")}
    >
      <SwitchOnActiveSection active={active} />
    </ViewErrorBoundary>
  );
}

function SwitchOnActiveSection({ active }: { active: string }) {
  switch (active) {
    case "chats":
      return <ChatsView />;
    case "agents":
      return <SubAgentsView />;
    // Skills + Plugins + MCPs are merged behind the "Skills & Tools" entry with
    // an in-view tab switcher; the active id doubles as the tab state.
    case "skills":
    case "plugins":
    case "mcps":
      return <ExtensionsView />;
    // CLIs list + CLI Test Hub are merged behind the "CLIs" entry.
    case "clis":
    case "cli-test-hub":
      return <ClisHubView />;
    case "docs":
      return <DocsView />;
    case "tasks":
      return <TasksView />;
    case "sessions":
      return <SessionsView />;
    case "run_inspector":
      return <RunInspectorView />;
    case "board":
      return <BoardView />;
    case "profile":
      return <ProfileView />;
    case "memory":
      return <WikiView />;
    // "telephony" no longer has its own screen — the telephony status /
    // credentials / scripts / calls now live as a section inside the API-Keys
    // view. The id stays valid so the existing "geh zur Telefonie" voice alias i18n-allow
    // keeps working and lands on API Keys (mirrors taskbar/languages → Settings).
    case "apikeys":
    case "telephony":
      return <ApiKeysView />;
    // Dedicated telephony setup page (scripts + step-by-step guide). Not a
    // sidebar entry — reached only via the "Setup script" button in the
    // telephony credentials card.
    case "telephony-setup":
      return <TelephonySetupView />;
    // "taskbar" and "languages" no longer have their own screens — the former
    // Taskbar controls live in Settings (OverlayTaskbarGroup) and the language
    // selectors live in Settings (LanguagesGroup) now. The ids stay valid so the
    // existing "geh zur Taskleiste" / "zeig die Sprachen" voice aliases keep i18n-allow
    // working and land on Settings.
    case "settings":
    case "taskbar":
    case "languages":
      return <SettingsView />;
    case "outputs":
      return <OutputsView />;
    case "socials":
      return <SocialsView />;
    case "contacts":
      return <ContactsView />;
    case "browser-voice":
      return <BrowserVoiceView />;
    case "agent-instructions":
      return <AgentInstructionsView />;
    default:
      return <ChatsView />;
  }
}
