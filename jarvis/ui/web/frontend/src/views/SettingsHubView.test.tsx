import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { SectionHealth } from "@/hooks/useProviders";

// Mutable mock store state — hoisted so the vi.mock factories below can close
// over it. Each test sets `activeSection` before rendering and inspects the
// `setActiveSection` spy.
const { mockState } = vi.hoisted(() => ({
  mockState: {
    activeSection: "settings" as string,
    setActiveSection: vi.fn(),
  },
}));

const { mockHealth } = vi.hoisted(() => ({
  mockHealth: {} as Record<string, SectionHealth>,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

vi.mock("@/i18n", () => ({
  // Identity translator: labels resolve to their keys (or the English
  // fallback where the nav item defines one), so assertions match keys.
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
}));

vi.mock("@/hooks/useProviders", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useProviders")>();
  return {
    ...actual,
    useSectionHealth: () => ({ health: mockHealth, reload: vi.fn() }),
  };
});

// ViewHeader lives in ChatsView, which drags in the whole chat surface. The
// hub only needs the header's shape, so stub it. `right` must pass through:
// the hub's search box lives in that slot.
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({
    title,
    subtitle,
    right,
  }: {
    title: string;
    subtitle?: string;
    right?: ReactNode;
  }) => (
    <header data-testid="view-header">
      <span data-testid="view-header-title">{title}</span>
      <span data-testid="view-header-subtitle">{subtitle}</span>
      {right}
    </header>
  ),
}));

// Stub every tab — the hub is a thin shell; what matters is WHICH tab it
// renders for an active id, not the tabs' own behaviour.
function stub(testid: string) {
  return () => <div data-testid={testid}>{testid}</div>;
}

vi.mock("@/views/SettingsView", () => ({
  SettingsView: ({ searchTarget }: { searchTarget?: string }) => (
    <div data-testid="TAB_SETTINGS" data-search-target={searchTarget} />
  ),
}));
vi.mock("@/views/ProfileView", () => ({ ProfileView: stub("TAB_PROFILE") }));
vi.mock("@/views/AgentInstructionsView", () => ({
  AgentInstructionsView: stub("TAB_INSTRUCTIONS"),
}));
vi.mock("@/views/contacts/ContactsView", () => ({
  ContactsView: stub("TAB_CONTACTS"),
}));
vi.mock("@/views/socials/SocialsView", () => ({ SocialsView: stub("TAB_SOCIALS") }));
vi.mock("@/views/ApiKeysView", () => ({ ApiKeysView: stub("TAB_APIKEYS") }));
vi.mock("@/views/TelephonyView", () => ({
  TelephonySetupView: stub("TAB_TELEPHONY_SETUP"),
}));
vi.mock("@/views/LocalModelsView", () => ({
  LocalModelsView: stub("TAB_LOCAL_MODELS"),
}));
vi.mock("@/views/WallpaperView", () => ({ WallpaperView: stub("TAB_WALLPAPER") }));
vi.mock("@/views/CostsView", () => ({ CostsView: stub("TAB_COSTS") }));
vi.mock("@/views/feedback/FeedbackView", () => ({
  FeedbackView: stub("TAB_FEEDBACK"),
}));

import { SettingsHubView } from "@/views/SettingsHubView";

const NAV_IDS = [
  "settings",
  "profile",
  "agent-instructions",
  "contacts",
  "socials",
  "apikeys",
  "local-models",
  "wallpaper",
  "costs",
  "feedback",
] as const;

beforeEach(() => {
  mockState.activeSection = "settings";
  mockState.setActiveSection = vi.fn();
  for (const key of Object.keys(mockHealth)) delete mockHealth[key];
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SettingsHubView header and navigation", () => {
  it("separates the navigation from the content and returns to the app", async () => {
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");
    expect(screen.getByTestId("settings-hub-sidebar").className).toContain("bg-sidebar");
    fireEvent.click(screen.getByRole("button", { name: "settings_hub.back_to_app" }));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("chats");
  });

  it("renders exactly one header titled from nav.settings", async () => {
    render(<SettingsHubView />);

    expect(screen.getAllByTestId("view-header")).toHaveLength(1);
    expect(screen.getByTestId("view-header-title").textContent).toBe("nav.settings");
    expect(screen.getByTestId("view-header-subtitle").textContent).toBe(
      "settings_hub.subtitle",
    );
    expect(
      screen.getByPlaceholderText("settings_hub.search_placeholder"),
    ).toBeTruthy();
    // The default tab's chunk still has to arrive.
    await screen.findByTestId("TAB_SETTINGS");
  });

  it("lists all ten entries in the left navigation", async () => {
    render(<SettingsHubView />);

    for (const id of NAV_IDS) {
      expect(screen.getByTestId(`settings-hub-nav-${id}`)).toBeTruthy();
    }
    await screen.findByTestId("TAB_SETTINGS");
  });

  it("marks the active entry current", async () => {
    mockState.activeSection = "costs";
    render(<SettingsHubView />);

    await screen.findByTestId("TAB_COSTS");
    expect(
      screen.getByTestId("settings-hub-nav-costs").getAttribute("aria-current"),
    ).toBe("page");
    expect(
      screen.getByTestId("settings-hub-nav-settings").getAttribute("aria-current"),
    ).toBeNull();
  });

  it("navigates when a nav entry is clicked", async () => {
    mockState.activeSection = "settings";
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");

    fireEvent.click(screen.getByTestId("settings-hub-nav-wallpaper"));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("wallpaper");
  });
});

describe("SettingsHubView tab resolution", () => {
  it.each([
    ["settings", "TAB_SETTINGS"],
    ["profile", "TAB_PROFILE"],
    ["agent-instructions", "TAB_INSTRUCTIONS"],
    ["contacts", "TAB_CONTACTS"],
    ["socials", "TAB_SOCIALS"],
    ["apikeys", "TAB_APIKEYS"],
    ["local-models", "TAB_LOCAL_MODELS"],
    ["wallpaper", "TAB_WALLPAPER"],
    ["costs", "TAB_COSTS"],
    ["feedback", "TAB_FEEDBACK"],
    // Merged-in ids land on the tab hosting their content.
    ["taskbar", "TAB_SETTINGS"],
    ["languages", "TAB_SETTINGS"],
    ["telephony", "TAB_APIKEYS"],
    ["telephony-setup", "TAB_TELEPHONY_SETUP"],
  ])("shows %s on the right tab", async (section, tab) => {
    mockState.activeSection = section;
    render(<SettingsHubView />);

    expect(await screen.findByTestId(tab)).toBeTruthy();
  });

  it("highlights API Keys while the telephony setup page is open", async () => {
    mockState.activeSection = "telephony-setup";
    render(<SettingsHubView />);

    await screen.findByTestId("TAB_TELEPHONY_SETUP");
    expect(
      screen.getByTestId("settings-hub-nav-apikeys").getAttribute("aria-current"),
    ).toBe("page");
  });

  it("falls back to Settings for an unexpected section id", async () => {
    mockState.activeSection = "chats";
    render(<SettingsHubView />);

    expect(await screen.findByTestId("TAB_SETTINGS")).toBeTruthy();
  });
});

describe("SettingsHubView search", () => {
  it("finds a field on another Settings page", async () => {
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");
    fireEvent.change(screen.getByPlaceholderText("settings_hub.search_placeholder"), {
      target: { value: "What is it?" },
    });
    fireEvent.click(screen.getByTestId("settings-hub-page-feedback"));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("feedback");
  });

  it("finds an option inside Settings and opens its group", async () => {
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");

    fireEvent.change(screen.getByPlaceholderText("settings_hub.search_placeholder"), {
      target: { value: "Microphone" },
    });
    fireEvent.click(screen.getByTestId("settings-hub-option-audio-devices"));

    expect(mockState.setActiveSection).toHaveBeenCalledWith("settings");
    expect(screen.getByTestId("TAB_SETTINGS").getAttribute("data-search-target"))
      .toBe("audio-devices");
  });

  it("reports when neither pages nor options match", async () => {
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");
    fireEvent.change(screen.getByPlaceholderText("settings_hub.search_placeholder"), {
      target: { value: "zzz-unmatched-setting" },
    });
    expect(screen.getByRole("status").textContent).toBe("settings_hub.no_results");
  });

  it("filters the nav entries by label", async () => {
    mockState.activeSection = "settings";
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");

    fireEvent.change(screen.getByPlaceholderText("settings_hub.search_placeholder"), {
      target: { value: "wall" },
    });

    expect(screen.getByTestId("settings-hub-nav-wallpaper")).toBeTruthy();
    expect(screen.queryByTestId("settings-hub-nav-profile")).toBeNull();
    expect(screen.queryByTestId("settings-hub-nav-apikeys")).toBeNull();
  });

  it("shows everything again once the search is cleared", async () => {
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");

    const search = screen.getByPlaceholderText("settings_hub.search_placeholder");
    fireEvent.change(search, { target: { value: "wall" } });
    expect(screen.queryByTestId("settings-hub-nav-profile")).toBeNull();
    fireEvent.change(search, { target: { value: "" } });
    for (const id of NAV_IDS) {
      expect(screen.getByTestId(`settings-hub-nav-${id}`)).toBeTruthy();
    }
  });
});

describe("SettingsHubView health signals", () => {
  it("carries the API-Keys alert dot on a provider error", async () => {
    mockHealth.brain = {
      status: "error",
      reason: "rate_limited",
      detail: "OpenRouter: rate limited",
      subject_id: "openrouter",
    };
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");

    expect(screen.getByTestId("settings-hub-alert-apikeys")).toBeTruthy();
    expect(screen.queryByTestId("settings-hub-warn-local-models")).toBeNull();
  });

  it("carries the Local-models warn dot while the setup needs care", async () => {
    mockHealth.local_models = {
      status: "needs_setup",
      reason: "not_configured",
      detail: "",
      subject_id: "ollama",
    };
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");

    expect(screen.getByTestId("settings-hub-warn-local-models")).toBeTruthy();
    expect(screen.queryByTestId("settings-hub-alert-apikeys")).toBeNull();
  });

  it("stays calm when nothing is broken", async () => {
    render(<SettingsHubView />);
    await screen.findByTestId("TAB_SETTINGS");

    expect(screen.queryByTestId("settings-hub-alert-apikeys")).toBeNull();
    expect(screen.queryByTestId("settings-hub-warn-local-models")).toBeNull();
  });
});
