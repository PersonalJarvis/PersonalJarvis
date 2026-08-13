import {
  lazy,
  Suspense,
  useEffect,
  useState,
  type ComponentType,
  type LazyExoticComponent,
} from "react";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { ViewErrorBoundary } from "@/components/ViewErrorBoundary";
import { DetachedViewPlaceholder } from "@/components/layout/DetachedViewPlaceholder";
// Type-only, so the section's chunk stays split out of the entry bundle.
import type { AgenticIdeViewProps } from "@/views/AgenticIdeView";
// The default section is the one view that must be on screen the moment React
// mounts, so it stays statically linked into the entry chunk. Every other view
// is code-split below.
import { ChatsView } from "@/views/ChatsView";

/**
 * Section views are CODE-SPLIT, then prefetched while the app is idle.
 *
 * Why (measured 2026-07-26): importing all 21 views statically linked every
 * section — plus the terminal emulator and the charting library — into a single
 * 2.83 MB entry chunk. A WebView must download, parse AND execute all of it
 * before React paints, which is the "the app feels sluggish on start" report.
 * The boot splash in index.html only hid that wait.
 *
 * Splitting alone would move the cost rather than remove it: the first visit to
 * a section would then pay for its chunk. `useIdleViewPrefetch` closes that gap
 * by pulling the remaining chunks in during idle time after the first paint, so
 * switching sections stays instant while startup only pays for what it shows.
 */
type ViewModule = { default: ComponentType };
type ViewLoader = () => Promise<ViewModule>;

/**
 * Loaders for every split view, in the order the idle prefetch warms them.
 *
 * Typed as returning `unknown` because warming a chunk only cares that the
 * import RAN — the module's shape is the caller's business, and pinning the
 * props-free shape here would keep `lazyPropView` out of the warm-up.
 */
const prefetchQueue: (() => Promise<unknown>)[] = [];

function lazyView(loader: ViewLoader): LazyExoticComponent<ComponentType> {
  prefetchQueue.push(loader);
  return lazy(loader);
}

/**
 * The same, for the one view the shell has to tell something.
 *
 * Sections are otherwise self-contained and take no props — a section knows
 * which one it is and reads the rest from the store. The Agentic IDE is the
 * exception because it is the one section that stays mounted while hidden, and
 * "are you the section on screen right now?" is a question only the shell can
 * answer for it (see `MainView`).
 */
function lazyPropView<P>(
  loader: () => Promise<{ default: ComponentType<P> }>,
): LazyExoticComponent<ComponentType<P>> {
  prefetchQueue.push(loader);
  return lazy(loader);
}

// Ordered roughly by how likely a section is to be opened, so the warm-up
// front-loads what the user reaches for first. Views are named exports, hence
// the explicit unwrap into the { default } shape React.lazy expects.
const SettingsView = lazyView(() =>
  import("@/views/SettingsView").then((m) => ({ default: m.SettingsView })),
);
const JarvisAgentsView = lazyView(() =>
  import("@/views/JarvisAgentsView").then((m) => ({
    default: m.JarvisAgentsView,
  })),
);
const WikiView = lazyView(() =>
  import("@/views/WikiView").then((m) => ({ default: m.WikiView })),
);
const ModesView = lazyView(() =>
  import("@/views/ModesView").then((m) => ({ default: m.ModesView })),
);
const ApiKeysView = lazyView(() =>
  import("@/views/ApiKeysView").then((m) => ({ default: m.ApiKeysView })),
);
const ExtensionsView = lazyView(() =>
  import("@/views/ExtensionsView").then((m) => ({ default: m.ExtensionsView })),
);
// The prop type is named rather than inferred: inferring it from the loader's
// return value is circular (the loader's contextual type is what depends on it),
// and TypeScript resolves that by falling back to `never`.
const AgenticIdeView = lazyPropView<AgenticIdeViewProps>(() =>
  import("@/views/AgenticIdeView").then((m) => ({ default: m.AgenticIdeView })),
);
const TasksView = lazyView(() =>
  import("@/views/TasksView").then((m) => ({ default: m.TasksView })),
);
const SessionsView = lazyView(() =>
  import("@/views/SessionsView").then((m) => ({ default: m.SessionsView })),
);
const OutputsView = lazyView(() =>
  import("@/views/OutputsView").then((m) => ({ default: m.OutputsView })),
);
const ClisHubView = lazyView(() =>
  import("@/views/ClisHubView").then((m) => ({ default: m.ClisHubView })),
);
const ProfileView = lazyView(() =>
  import("@/views/ProfileView").then((m) => ({ default: m.ProfileView })),
);
const DocsView = lazyView(() =>
  import("@/views/DocsView").then((m) => ({ default: m.DocsView })),
);
const BoardView = lazyView(() =>
  import("@/views/BoardView").then((m) => ({ default: m.BoardView })),
);
const RunInspectorView = lazyView(() =>
  import("@/views/RunInspectorView").then((m) => ({
    default: m.RunInspectorView,
  })),
);
// Dictation + Dictionary + Shortcuts + Language + Voice API keys are merged
// behind the one "{name} Voice" sidebar entry. Only the hub is split out here —
// it statically imports its five tabs, so they travel in its chunk instead of
// being prefetched as separate ones.
const VoiceHubView = lazyView(() =>
  import("@/views/VoiceHubView").then((m) => ({ default: m.VoiceHubView })),
);
const AgentInstructionsView = lazyView(() =>
  import("@/views/AgentInstructionsView").then((m) => ({
    default: m.AgentInstructionsView,
  })),
);
const SocialsView = lazyView(() =>
  import("@/views/socials/SocialsView").then((m) => ({
    default: m.SocialsView,
  })),
);
const ContactsView = lazyView(() =>
  import("@/views/contacts/ContactsView").then((m) => ({
    default: m.ContactsView,
  })),
);
const FeedbackView = lazyView(() =>
  import("@/views/feedback/FeedbackView").then((m) => ({
    default: m.FeedbackView,
  })),
);
const TelephonySetupView = lazyView(() =>
  import("@/views/TelephonyView").then((m) => ({
    default: m.TelephonySetupView,
  })),
);
const WallpaperView = lazyView(() =>
  import("@/views/WallpaperView").then((m) => ({ default: m.WallpaperView })),
);
const VisualizationView = lazyView(() =>
  import("@/views/VisualizationView").then((m) => ({
    default: m.VisualizationView,
  })),
);

type IdleWindow = Window & {
  requestIdleCallback?: (
    cb: () => void,
    opts?: { timeout: number },
  ) => number;
  cancelIdleCallback?: (handle: number) => void;
};

/**
 * Run `task` when the browser is idle, falling back to a timer.
 *
 * `requestIdleCallback` is absent on older Safari/WebKit, which is exactly the
 * macOS WebView this app also ships in, so the timer fallback is load-bearing
 * rather than cosmetic. The returned function cancels a pending slot.
 */
function scheduleIdle(task: () => void): () => void {
  const w = window as IdleWindow;
  if (typeof w.requestIdleCallback === "function") {
    // The timeout caps how long a busy main thread may starve the warm-up.
    const handle = w.requestIdleCallback(task, { timeout: 2_000 });
    return () => w.cancelIdleCallback?.(handle);
  }
  const handle = window.setTimeout(task, 300);
  return () => window.clearTimeout(handle);
}

/**
 * Warm the split view chunks one at a time, each in its own idle slot.
 *
 * Sequential rather than parallel on purpose: firing 20 imports at once would
 * compete with the requests the visible section is making, which is the very
 * stall this is meant to remove. A failed prefetch is ignored — the chunk is
 * simply fetched again on navigation, where Suspense handles it.
 */
function useIdleViewPrefetch(): void {
  useEffect(() => {
    let cancelled = false;
    let cancelSlot: (() => void) | null = null;
    let index = 0;

    const pump = () => {
      if (cancelled || index >= prefetchQueue.length) return;
      const loader = prefetchQueue[index++];
      void loader()
        .catch(() => undefined)
        .then(() => {
          if (cancelled) return;
          cancelSlot = scheduleIdle(pump);
        });
    };

    cancelSlot = scheduleIdle(pump);
    return () => {
      cancelled = true;
      cancelSlot?.();
    };
  }, []);
}

/**
 * Placeholder shown while a section chunk is in flight.
 *
 * Deliberately empty: the idle prefetch means this is almost never seen, and a
 * spinner that appears for 30 ms reads as a flicker — worse than nothing. It
 * only holds the layout so the surrounding chrome does not jump. Being
 * text-free, it also needs no translation.
 */
function ViewLoadingFallback() {
  return <div className="h-full w-full" aria-busy="true" />;
}

/**
 * The section ids that all mean "the coding workspace".
 *
 * There is ONE coding surface again: the terminal workspace. A separate chat
 * surface was tried in front of it and taken back out — it rendered a picture of
 * the agent while the agent itself ran behind it, which is one thing too many
 * for a screen whose whole job is showing what the agent is doing. The extra
 * ids stay valid because deep links, voice commands and stored preferences
 * already point at them, and an id that stops resolving is a dead end for
 * somebody who never chose either name.
 */
const CODING_SECTION_IDS = ["agentic-ide", "agentic-ide-classic", "chat-workspace"] as const;

/**
 * The one section that is hidden rather than unmounted — see `MainView`.
 *
 * What makes a section too destructive to unmount is LIVE PANES, and this is
 * where those live.
 */
const STICKY_SECTION = "agentic-ide";

/**
 * Main area to the right of the sidebar. Views are switched the classic way
 * (one view active, the others unmounted) — this saves render load and is
 * semantically fine because they rehydrate their state from React Query / the
 * store.
 *
 * The Agentic IDE is the ONE exception, and it is not an optimisation: its
 * panes are live terminals, and unmounting them is destructive in a way no
 * other section's state is. Leaving the section tore down a dozen xterm
 * instances and their sockets, and coming back rebuilt every one of them —
 * each re-attach replays up to `REPLAY_LIMIT_CHARS` of raw output per pane, so
 * a workspace of eleven terminals pushed well over a megabyte through the main
 * thread just to show what was already on screen three seconds earlier. Worse,
 * the remounted view starts out knowing of no workspace at all, so the first
 * thing the user saw on the way back was the onboarding wizard asking which
 * folder to open — in front of a workspace that had never stopped running
 * (maintainer report 2026-07-29).
 *
 * So once opened, it stays mounted and is hidden with CSS instead. That costs
 * nothing while it is away: the panes' `IntersectionObserver` already treats
 * `display: none` as off-screen and parks their output (see AgenticTerminal),
 * and their `ResizeObserver` re-fits them when they come back. Sticky rather
 * than always-mounted so a user who never opens the section never pays for it.
 */
export function MainView() {
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);
  const solo = useEventStore((s) => s.solo);
  const detachedViews = useEventStore((s) => s.detachedViews);

  useIdleViewPrefetch();

  /*
   * While a coding view lives in a detached solo window, THIS (non-solo)
   * window must not keep a mounted IDE instance around — not even hidden: the
   * detached window's panes claim the PTY streams, and a second connected
   * instance steals every pane's output (the exact defect the sticky-mount
   * comment below warns about). The solo window itself is exempt — it IS the
   * detached instance the registry entry refers to.
   */
  const codingDetached =
    !solo &&
    detachedViews.some((v) => (CODING_SECTION_IDS as readonly string[]).includes(v));

  const stickyActive =
    (CODING_SECTION_IDS as readonly string[]).includes(active) && !codingDetached;
  const [stickyMounted, setStickyMounted] = useState(stickyActive);
  useEffect(() => {
    if (stickyActive) setStickyMounted(true);
  }, [stickyActive]);
  useEffect(() => {
    // Genuine unmount on detach — and remount happens through the normal
    // sticky path once DetachedViewClosed clears the registry.
    if (codingDetached) setStickyMounted(false);
  }, [codingDetached]);

  if (codingDetached && (CODING_SECTION_IDS as readonly string[]).includes(active)) {
    return <DetachedViewPlaceholder view={active} />;
  }

  return (
    <>
      {stickyMounted && (
        <div
          className={cn("h-full w-full", !stickyActive && "hidden")}
          data-testid="sticky-agentic-ide"
          // Hidden from assistive technology too while it is off screen: the
          // panes stay in the DOM, and a screen reader walking a workspace's
          // worth of terminal output behind the visible section would be a
          // wall of noise nobody asked for.
          aria-hidden={!stickyActive}
        >
          {/* Its own boundary, un-keyed: this instance must survive every
              section change, which is the entire point of keeping it. */}
          <ViewErrorBoundary
            viewName={STICKY_SECTION}
            resetKey={STICKY_SECTION}
            onRecover={() => setActive("chats")}
          >
            <Suspense fallback={<ViewLoadingFallback />}>
              {/* Told rather than measured: a `display: none` subtree has no
                  geometry to read, and the view's background polling has to
                  know it is off screen so it can stop asking the backend what
                  a dozen panes are doing while nobody is watching them. */}
              <AgenticIdeView onScreen={stickyActive} />
            </Suspense>
          </ViewErrorBoundary>
        </div>
      )}
      {!stickyActive && (
        <ViewErrorBoundary
          viewName={active}
          resetKey={active}
          onRecover={() => setActive("chats")}
        >
          {/* Keyed on the active section so switching away from a still-loading
              view cannot leave the previous section's fallback on screen. */}
          <Suspense key={active} fallback={<ViewLoadingFallback />}>
            <SwitchOnActiveSection active={active} />
          </Suspense>
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
      return <JarvisAgentsView />;
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
    // The merged voice section: Dictation (default landing) + Dictionary +
    // Shortcuts + Language + the speech-to-text keys, behind one tab bar. The
    // active id doubles as the tab state, so a voice deep-link to any of them
    // still lands on the right tab.
    case "dictation":
    case "dictionary":
    case "voice-shortcuts":
    case "voice-language":
    case "voice-api-keys":
      return <VoiceHubView />;
    case "outputs":
      return <OutputsView />;
    case "socials":
      return <SocialsView />;
    case "contacts":
      return <ContactsView />;
    case "feedback":
      return <FeedbackView />;
    case "agent-instructions":
      return <AgentInstructionsView />;
    case "wallpaper":
      return <WallpaperView />;
    // The visual stage for what a run produced. Rendered through the same
    // switch as every other section — the shell only decides what the stage
    // AROUND it looks like (see App.tsx).
    case "visualization":
      return <VisualizationView />;
    // The assistant's character: which mode is active, and the workshop for
    // building another one by talking to it.
    case "modes":
      return <ModesView />;
    // Deliberately nothing: the coding workspace is rendered by the STICKY
    // branch in `MainView` above, which keeps it mounted across section
    // changes. This switch is not rendered at all while one of those ids is
    // active, so reaching here would mean two live copies of a workspace's
    // terminals — the second of which would steal every pane's output stream
    // from the first.
    case "agentic-ide":
    case "agentic-ide-classic":
    case "chat-workspace":
      return null;
    default:
      return <ChatsView />;
  }
}
