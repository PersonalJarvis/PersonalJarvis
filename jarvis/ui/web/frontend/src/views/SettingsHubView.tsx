import { lazy, Suspense, useMemo, useState, type ComponentType, type LazyExoticComponent } from "react";
import { Loader2, Settings as SettingsIcon } from "lucide-react";
import {
  NAV_FOOTER_ITEMS,
  NAV_GROUPS,
  resolveNavLabel,
  type NavItem,
} from "@/components/layout/navGroups";
import { InlineSearch } from "@/components/extensions/primitives";
import { useEventStore } from "@/store/events";
import { useSectionHealth } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { ViewHeader } from "@/views/ChatsView";
import { cn } from "@/lib/utils";

/**
 * The Settings hub — every personal/system section behind one page, with a
 * searchable left navigation (Personal · System · Activity) and the selected
 * section on the right:
 *
 *   General: Settings, Profile, {name}.md, Contacts, Socials
 *   System: API Keys, Local models, Wallpaper
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
const WallpaperTab = lazy(() =>
  import("@/views/WallpaperView").then((m) => ({ default: m.WallpaperView })),
);
const CostsTab = lazy(() =>
  import("@/views/CostsView").then((m) => ({ default: m.CostsView })),
);
const FeedbackTab = lazy(() =>
  import("@/views/feedback/FeedbackView").then((m) => ({
    default: m.FeedbackView,
  })),
);

/** The ten entries of the left navigation, in display order. */
type HubNavId =
  | "settings"
  | "profile"
  | "agent-instructions"
  | "contacts"
  | "socials"
  | "apikeys"
  | "local-models"
  | "wallpaper"
  | "costs"
  | "feedback";

const HUB_NAV_GROUPS: readonly { labelKey: string; ids: readonly HubNavId[] }[] = [
  {
    labelKey: "settings_hub.group_general",
    ids: ["settings", "profile", "agent-instructions", "contacts", "socials"],
  },
  {
    labelKey: "settings_hub.group_system",
    ids: ["apikeys", "local-models", "wallpaper"],
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
  wallpaper: WallpaperTab,
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
    case "wallpaper":
      return { content: "wallpaper", highlight: "wallpaper" };
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
const ALL_NAV_ITEMS: readonly NavItem[] = [...NAV_GROUPS.flat(), ...NAV_FOOTER_ITEMS];

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

export function SettingsHubView() {
  const t = useT();
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);
  const [query, setQuery] = useState("");
  const { health: sectionHealth } = useSectionHealth();

  const { content, highlight } = resolveHubTab(active);
  const Content = TAB_CONTENT[content];

  const needle = query.trim().toLowerCase();
  const matches = (item: NavItem) =>
    needle === "" || resolveNavLabel(t, item).toLowerCase().includes(needle);

  // Ten entries — filtered inline; no memo needed at this size.
  const visibleGroups = HUB_NAV_GROUPS.map((group) => ({
    ...group,
    items: group.ids.map(findNavItem).filter(matches),
  })).filter((group) => group.items.length > 0);

  // The same two health signals the sidebar rows used to carry, now on the
  // hub's own nav: a hard provider error on API Keys, a failing or
  // half-configured local setup on Local models. Badge only, never a toast.
  const apikeysHasError = useMemo(
    () => Object.values(sectionHealth).some((h) => h?.status === "error"),
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
          onClick={() => setActive(item.id)}
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
    <div data-testid="settings-hub" className="flex h-full flex-col">
      <ViewHeader
        icon={<SettingsIcon className="h-4 w-4 text-foreground" />}
        title={t("nav.settings")}
        subtitle={t("settings_hub.subtitle")}
        right={
          <div className="w-64 max-w-full">
            <InlineSearch
              value={query}
              onChange={setQuery}
              placeholder={t("settings_hub.search_placeholder")}
            />
          </div>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col gap-4 px-8 pb-8 md:flex-row">
        <nav
          aria-label={t("nav.settings")}
          className="shrink-0 overflow-x-auto scrollbar-jarvis md:w-60 md:overflow-y-auto lg:w-64"
        >
          {/* Narrow screens stack the nav above the content as one horizontal
              strip; from `md` up it is the fixed left column of the page. */}
          <ul className="flex gap-1 md:flex-col md:gap-0">
            {visibleGroups.map((group) => (
              <li key={group.labelKey} className="flex shrink-0 gap-1 md:block md:shrink md:gap-0">
                <p className="hidden px-3 pb-1 pt-4 text-sm font-medium uppercase tracking-wide text-foreground-faint md:block">
                  {t(group.labelKey)}
                </p>
                <ul className="flex gap-1 md:block md:space-y-0.5 md:gap-0">
                  {group.items.map(renderNavItem)}
                </ul>
              </li>
            ))}
          </ul>
        </nav>
        <div
          data-testid="settings-hub-content"
          className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis"
        >
          <Suspense fallback={<HubLoadingFallback />}>{<Content />}</Suspense>
        </div>
      </div>
    </div>
  );
}
