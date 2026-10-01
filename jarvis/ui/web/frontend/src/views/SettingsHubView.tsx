import * as Dialog from "@radix-ui/react-dialog";
import { isTourEvent } from "@/components/onboarding/tourEvents";
import { lazy, Suspense, useMemo, useRef, useState, type ComponentType, type LazyExoticComponent } from "react";
import { Loader2, Search, X } from "lucide-react";
import {
  NAV_FOOTER_ITEMS,
  NAV_GROUPS,
  SETTINGS_HUB_ONLY_ITEMS,
  resolveNavLabel,
  type NavItem,
} from "@/components/layout/navGroups";
import { useEventStore } from "@/store/events";
import { useSectionHealth } from "@/hooks/useProviders";
import { useT, useUiLanguage } from "@/i18n";
import { isComboboxPanelEvent } from "@/components/ui/combobox";
import { searchSettingsOptions, searchSettingsPages } from "@/views/settings/settingsSearch";
import { cn } from "@/lib/utils";

/**
 * The Settings hub — every personal/system section behind one dialog that
 * floats over the user's current section, with a searchable left navigation
 * (General · System · Activity) and the selected section on the right:
 *
 *   General: Settings, Appshots, Profile, {name}.md, Contacts, Socials
 *   System: Computers, API Keys, Local models, Wallpaper
 *   Activity: Spend, Feedback
 *
 * Same merged-section pattern as VoiceHubView / ClisHubView: the active
 * section id IS the tab state, so deep links, voice commands ("open the API
 * keys"), the deck and detached windows keep landing on the right tab with no
 * extra routing. The merged-in ids ("telephony", "taskbar", "languages",
 * "telephony-setup") resolve to the tab that hosts their content today,
 * exactly like MainView used to map them to standalone views.
 *
 * Labels, icons and grouping resolve from `NAV_GROUPS` (via `resolveNavLabel`,
 * so the `{name}.md` token and all three locales behave exactly like the
 * sidebar rows did) — no second hand-written list to drift (AP-4).
 *
 * Tab contents stay code-split one `lazy` boundary per view, so opening the
 * hub still only pays for the shell plus the visible tab.
 */

const SettingsTab = lazy(() =>
  import("@/views/SettingsView").then((m) => ({ default: m.SettingsView })),
);
const ProfileTab = lazy(() =>
  import("@/views/ProfileView").then((m) => ({ default: m.ProfileView })),
);
const InstructionsTab = lazy(() =>
  import("@/views/AgentInstructionsView").then((m) => ({
    default: m.AgentInstructionsView,
  })),
);
const ContactsTab = lazy(() =>
  import("@/views/contacts/ContactsView").then((m) => ({
    default: m.ContactsView,
  })),
);
const SocialsTab = lazy(() =>
  import("@/views/socials/SocialsView").then((m) => ({
    default: m.SocialsView,
  })),
);
const ApiKeysTab = lazy(() =>
  import("@/views/ApiKeysView").then((m) => ({ default: m.ApiKeysView })),
);
const TelephonySetupTab = lazy(() =>
  import("@/views/TelephonyView").then((m) => ({
    default: m.TelephonySetupView,
  })),
);
const LocalModelsTab = lazy(() =>
  import("@/views/LocalModelsView").then((m) => ({
    default: m.LocalModelsView,
  })),
);
const ComputersTab = lazy(() =>
  import("@/views/ComputersView").then((m) => ({ default: m.ComputersView })),
);
const WallpaperTab = lazy(() =>
  import("@/views/WallpaperView").then((m) => ({ default: m.WallpaperView })),
);
const JarvisActionsTab = lazy(() =>
  import("@/views/JarvisActionsView").then((m) => ({ default: m.JarvisActionsView })),
);
const AppshotsTab = lazy(() =>
  import("@/views/AppshotsView").then((m) => ({ default: m.AppshotsView })),
);
const CostsTab = lazy(() =>
  import("@/views/CostsView").then((m) => ({ default: m.CostsView })),
);
const FeedbackTab = lazy(() =>
  import("@/views/feedback/FeedbackView").then((m) => ({
    default: m.FeedbackView,
  })),
);

/** The eleven entries of the left navigation, in display order. */
type HubNavId =
  | "settings"
  | "appshots"
  | "profile"
  | "agent-instructions"
  | "contacts"
  | "socials"
  | "apikeys"
  | "local-models"
  | "computers"
  | "jarvis-actions"
  | "wallpaper"
  | "costs"
  | "feedback";

const HUB_NAV_GROUPS: readonly { labelKey: string; ids: readonly HubNavId[] }[] = [
  {
    labelKey: "settings_hub.group_general",
    ids: ["settings", "appshots", "profile", "agent-instructions", "contacts", "socials"],
  },
  {
    labelKey: "settings_hub.group_system",
    ids: ["computers", "apikeys", "local-models", "jarvis-actions", "wallpaper"],
  },
  {
    labelKey: "settings_hub.group_activity",
    ids: ["costs", "feedback"],
  },
];

const TAB_CONTENT: Record<HubNavId | "telephony-setup", LazyExoticComponent<ComponentType>> = {
  settings: SettingsTab,
  profile: ProfileTab,
  "agent-instructions": InstructionsTab,
  contacts: ContactsTab,
  socials: SocialsTab,
  apikeys: ApiKeysTab,
  "telephony-setup": TelephonySetupTab,
  "local-models": LocalModelsTab,
  computers: ComputersTab,
  wallpaper: WallpaperTab,
  appshots: AppshotsTab,
  "jarvis-actions": JarvisActionsTab,
  costs: CostsTab,
  feedback: FeedbackTab,
};

/**
 * Which tab content — and which nav entry is highlighted — for an active
 * section id. Plain ids name their own tab; the merged-in ids resolve to the
 * tab hosting their content; anything else falls back to Settings.
 */
function resolveHubTab(active: string): { content: HubNavId | "telephony-setup"; highlight: HubNavId } {
  switch (active) {
    case "profile":
      return { content: "profile", highlight: "profile" };
    case "agent-instructions":
      return { content: "agent-instructions", highlight: "agent-instructions" };
    case "contacts":
      return { content: "contacts", highlight: "contacts" };
    case "socials":
      return { content: "socials", highlight: "socials" };
    case "apikeys":
    case "telephony":
      return { content: "apikeys", highlight: "apikeys" };
    case "telephony-setup":
      return { content: "telephony-setup", highlight: "apikeys" };
    case "local-models":
      return { content: "local-models", highlight: "local-models" };
    case "computers":
      return { content: "computers", highlight: "computers" };
    case "wallpaper":
      return { content: "wallpaper", highlight: "wallpaper" };
    case "appshots":
      return { content: "appshots", highlight: "appshots" };
    case "jarvis-actions":
      return { content: "jarvis-actions", highlight: "jarvis-actions" };
    case "costs":
      return { content: "costs", highlight: "costs" };
    case "feedback":
      return { content: "feedback", highlight: "feedback" };
    case "settings":
    case "taskbar":
    case "languages":
    default:
      return { content: "settings", highlight: "settings" };
  }
}

// `NAV_GROUPS` plus the footer: "feedback" lives in `NAV_FOOTER_ITEMS`, not in
// a group (same lookup as TopBar/DockRail) — without it the hub cannot resolve
// its own tenth entry.
const ALL_NAV_ITEMS: readonly NavItem[] = [
  ...NAV_GROUPS.flat(),
  ...NAV_FOOTER_ITEMS,
  ...SETTINGS_HUB_ONLY_ITEMS,
];

function findNavItem(id: HubNavId): NavItem {
  const item = ALL_NAV_ITEMS.find((row) => row.id === id);
  if (!item) throw new Error(`Settings hub nav item missing: ${id}`);
  return item;
}

function HubLoadingFallback() {
  return (
    <div
      className="flex h-full w-full items-center justify-center"
      role="status"
      aria-busy="true"
      data-testid="settings-hub-loading"
    >
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
    </div>
  );
}

export function SettingsHubView({ onClose }: { onClose: () => void }) {
  const t = useT();
  const language = useUiLanguage();
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);
  const [query, setQuery] = useState("");
  const [searchTarget, setSearchTarget] = useState<string | null>(null);
  const { health: sectionHealth } = useSectionHealth();

  const { content, highlight } = resolveHubTab(active);
  const Content = TAB_CONTENT[content];

  const needle = query.trim().toLowerCase();
  const matches = (item: NavItem) =>
    needle === "" || resolveNavLabel(t, item).toLowerCase().includes(needle);

  // Eleven entries — filtered inline; no memo needed at this size.
  const visibleGroups = HUB_NAV_GROUPS.map((group) => ({
    ...group,
    items: group.ids.map(findNavItem).filter(matches),
  })).filter((group) => group.items.length > 0);
  const optionMatches = searchSettingsOptions(language, query, t);
  const pageMatches = searchSettingsPages(language, query, t)
    .filter((match) => !visibleGroups.some((group) =>
      group.items.some((item) => item.id === match.id)))
    .map((match) => ({ ...match, label: resolveNavLabel(t, findNavItem(match.id)) }));

  // The same two health signals the sidebar rows used to carry, now on the
  // hub's own nav: a hard provider error on API Keys, a failing or
  // half-configured local setup on Local models. Badge only, never a toast.
  const apikeysHasError = useMemo(
    () => Object.entries(sectionHealth).some(([section, health]) => section !== "computer-use" && health?.status === "error"),
    [sectionHealth],
  );
  const localModelsHealth = sectionHealth.local_models;
  const localModelsNeedAttention =
    localModelsHealth?.status === "error" || localModelsHealth?.status === "needs_setup";

  const renderNavItem = (item: NavItem) => {
    const Icon = item.icon;
    const isActive = item.id === highlight;
    const showAlert = item.id === "apikeys" && apikeysHasError;
    const showWarn = item.id === "local-models" && localModelsNeedAttention;
    const hint = showAlert
      ? t("sidebar.apikeys_alert")
      : showWarn
        ? localModelsHealth?.detail || localModelsHealth?.reason
        : undefined;
    return (
      <li key={item.id}>
        <button
          type="button"
          data-testid={`settings-hub-nav-${item.id}`}
          onClick={() => {
            setQuery("");
            setSearchTarget(null);
            setActive(item.id);
          }}
          title={hint}
          aria-current={isActive ? "page" : undefined}
          className={cn(
            "group flex h-9 w-full items-center gap-2.5 rounded-md px-3 text-base font-medium transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            isActive
              ? "jarvis-nav-active bg-secondary text-foreground"
              : "text-muted-foreground hover:bg-secondary hover:text-foreground",
          )}
        >
          <Icon
            aria-hidden
            className={cn(
              "h-4 w-4 shrink-0 transition-colors",
              isActive ? "text-foreground" : "text-muted-foreground group-hover:text-foreground",
            )}
          />
          <span className="min-w-0 flex-1 truncate text-left">{resolveNavLabel(t, item)}</span>
          {showAlert && (
            <span
              data-testid="settings-hub-alert-apikeys"
              role="status"
              aria-label={t("sidebar.apikeys_alert")}
              className="h-2 w-2 shrink-0 rounded-full bg-destructive"
            />
          )}
          {!showAlert && showWarn && (
            <span
              data-testid="settings-hub-warn-local-models"
              role="status"
              className="h-2 w-2 shrink-0 rounded-full bg-warning"
            />
          )}
        </button>
      </li>
    );
  };

  return (
    <div data-testid="settings-hub" className="flex h-full min-h-0 flex-col md:flex-row">
      <aside
        data-testid="settings-hub-sidebar"
        className="jarvis-nav-surface flex max-h-72 w-full shrink-0 flex-col border-b border-border md:max-h-none md:w-60 md:border-b-0 md:border-r"
      >
        <div className="px-3 pb-2 pt-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input type="text" role="searchbox" data-testid="settings-hub-search" value={query} onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Escape") setQuery(""); }}
              placeholder={t("settings_hub.search_placeholder")}
              aria-label={t("settings_hub.search_placeholder")}
              className="h-9 w-full rounded-md border border-border bg-input pl-9 pr-9 text-base text-foreground placeholder:text-foreground-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-ring"
            />
            {query && <button type="button" onClick={() => setQuery("")}
              aria-label={t("settings_hub.clear_search")}
              className="absolute right-1 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <X className="h-4 w-4" aria-hidden />
            </button>}
          </div>
        </div>
        <nav aria-label={t("nav.settings")}
          className="min-h-0 flex-1 overflow-y-auto px-3 pb-5 scrollbar-jarvis">
          <ul className="space-y-1">
            {visibleGroups.map((group) => (
              <li key={group.labelKey}>
                <p className="px-3 pb-1 pt-4 text-sm font-medium uppercase tracking-wide text-foreground-faint">
                  {t(group.labelKey)}
                </p>
                <ul className="space-y-0.5">{group.items.map(renderNavItem)}</ul>
              </li>
            ))}
          </ul>
          {pageMatches.length > 0 && (
            <div data-testid="settings-hub-page-results">
              <p className="px-3 pb-1 pt-5 text-sm font-medium uppercase tracking-wide text-foreground-faint">
                {t("settings_hub.search_pages")}
              </p>
              <ul className="space-y-0.5">
                {pageMatches.map((match) => (
                  <li key={match.id}>
                    <button type="button" data-testid={`settings-hub-page-${match.id}`}
                      onClick={() => {
                        setSearchTarget(null);
                        setQuery("");
                        setActive(match.id);
                      }}
                      className="flex w-full flex-col rounded-md px-3 py-2 text-left transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <span className="text-base font-medium text-foreground">{match.label}</span>
                      {match.detail && <span className="w-full truncate text-sm text-muted-foreground" title={match.detail}>{match.detail}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {optionMatches.length > 0 && (
            <div data-testid="settings-hub-option-results">
              <p className="px-3 pb-1 pt-5 text-sm font-medium uppercase tracking-wide text-foreground-faint">
                {t("settings_hub.search_results")}
              </p>
              <ul className="space-y-0.5">
                {optionMatches.map((match) => (
                  <li key={match.id}>
                    <button type="button" data-testid={`settings-hub-option-${match.id}`}
                      onClick={() => {
                        setSearchTarget(match.id);
                        setQuery("");
                        setActive("settings");
                      }}
                      className="flex w-full flex-col rounded-md px-3 py-2 text-left transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <span className="text-base font-medium text-foreground">{match.label}</span>
                      {match.detail && <span className="w-full truncate text-sm text-muted-foreground" title={match.detail}>{match.detail}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {needle && visibleGroups.length === 0 && pageMatches.length === 0 && optionMatches.length === 0 && (
            <p role="status" className="px-3 py-5 text-base text-muted-foreground">
              {t("settings_hub.no_results")}
            </p>
          )}
        </nav>
      </aside>
      <div className="jarvis-sheet relative flex min-h-0 min-w-0 flex-1 flex-col">
        <button type="button" onClick={onClose} aria-label={t("common.close")}
          data-testid="settings-hub-close"
          className="absolute right-3 top-3 z-10 grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <X className="h-4 w-4" aria-hidden />
        </button>
        <div data-testid="settings-hub-content" className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
          <div className="h-full w-full max-w-[2000px]">
            <Suspense fallback={<HubLoadingFallback />}>
              {content === "settings"
                ? <SettingsTab searchTarget={searchTarget} onSearchTargetHandled={() => setSearchTarget(null)} />
                : <Content />}
            </Suspense>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The hub as a centred window over the current section (the way desktop apps
 * open their preferences), not a page that replaces the whole app. Closing it
 * — the X, Escape or a click on the scrim — returns to the section behind it.
 *
 * Centred with `inset-0` + `m-auto` rather than a translate: a transform would
 * turn the dialog into the containing block of every `position: fixed` layer a
 * tab renders inline (the wallpaper preview, view-level dialogs) and trap them
 * inside the window instead of covering the screen.
 */
export function SettingsHubDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const content = useRef<HTMLDivElement>(null);
  const opener = useRef(document.activeElement);
  // A nested modal inside a tab (credential dialogs, pickers) owns outside
  // clicks and Escape while it is open; so does an open combobox panel.
  // The first-run guide dims the window over this dialog and points into it;
  // a click on its card or its dim must not read as "outside" and close the
  // very page it is pointing at.
  const nestedOwnsEvent = (event: Event) =>
    isComboboxPanelEvent(event) ||
    content.current?.querySelector('[aria-modal="true"]') != null ||
    isTourEvent(event);
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/65 backdrop-blur-[2px]" />
        <Dialog.Content
          data-testid="settings-hub-dialog"
          ref={content}
          aria-describedby={undefined}
          onCloseAutoFocus={(event) => {
            const previous = opener.current;
            const target = previous instanceof HTMLElement && previous.isConnected && previous !== document.body
              ? previous
              : document.querySelector<HTMLElement>("main");
            if (!target) return;
            // This route-driven dialog has no Radix Trigger to restore focus to.
            event.preventDefault();
            const needsTabIndex = target.tagName === "MAIN" && !target.hasAttribute("tabindex");
            if (needsTabIndex) target.setAttribute("tabindex", "-1");
            target.focus({ preventScroll: true });
            if (needsTabIndex) target.removeAttribute("tabindex");
          }}
          onPointerDownOutside={(event) => { if (nestedOwnsEvent(event)) event.preventDefault(); }}
          onFocusOutside={(event) => { if (nestedOwnsEvent(event)) event.preventDefault(); }}
          onInteractOutside={(event) => { if (nestedOwnsEvent(event)) event.preventDefault(); }}
          onEscapeKeyDown={(event) => {
            if (content.current?.querySelector('[aria-modal="true"]')) {
              event.preventDefault();
              return;
            }
            // Escape in a filled search box clears the search first.
            const target = event.target;
            if (target instanceof HTMLInputElement && target.dataset.testid === "settings-hub-search" && target.value) {
              event.preventDefault();
            }
          }}
          className="fixed inset-0 z-40 m-auto flex h-[min(86dvh,820px)] w-[min(1040px,calc(100vw-32px))] flex-col overflow-hidden rounded-2xl border border-border bg-popover text-foreground shadow-float outline-none"
        >
          <Dialog.Title className="sr-only">{t("nav.settings")}</Dialog.Title>
          <SettingsHubView onClose={onClose} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
