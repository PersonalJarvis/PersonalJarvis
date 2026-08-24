import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockState, mockProviders } = vi.hoisted(() => ({
  mockState: {
    activeSection: "local-models" as string,
    setActiveSection: vi.fn(),
  },
  mockProviders: {
    providers: [] as Array<Record<string, unknown>>,
    loading: false,
    error: null as string | null,
    refetch: vi.fn(async () => undefined),
  },
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

vi.mock("@/i18n", () => ({
  // Identity translator: assertions match the keys themselves.
  useT: () => (key: string) => key,
  useLocaleChunk: () => true,
}));

vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => mockProviders,
}));

// The provider-card panels have their own tests; here only their mount matters.
vi.mock("@/components/providers/ProviderTierSection", () => ({
  OllamaRuntimePanel: ({ alwaysVisible }: { alwaysVisible?: boolean }) => (
    <div data-testid="runtime-panel" data-always={alwaysVisible ? "true" : "false"} />
  ),
  BaseUrlField: () => <div data-testid="base-url-field" />,
  LocalModelDownloadPanel: () => <div data-testid="download-panel" />,
}));

import { LOCAL_MODELS_MODE_KEY, LocalModelsView } from "@/views/LocalModelsView";

const OLLAMA = {
  id: "ollama",
  label: "Ollama",
  supports_model_pull: true,
  supports_base_url: true,
};

beforeEach(() => {
  window.localStorage.removeItem(LOCAL_MODELS_MODE_KEY);
  mockState.setActiveSection = vi.fn();
  mockProviders.providers = [OLLAMA];
  mockProviders.loading = false;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LocalModelsView", () => {
  it("renders the header, the Simple rail and the overview by default", () => {
    render(<LocalModelsView />);

    expect(screen.getByText("local_models.title")).toBeDefined();
    expect(screen.getByRole("tab", { name: "local_models.tab_overview" })).toBeDefined();
    expect(screen.getByRole("tab", { name: "local_models.tab_catalogue" })).toBeDefined();
    expect(screen.getByRole("tab", { name: "local_models.tab_server" })).toBeDefined();
    // Simple hides the Advanced-only tabs.
    expect(screen.queryByRole("tab", { name: "local_models.tab_models" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "local_models.tab_huggingface" })).toBeNull();
    expect(screen.getByTestId("local-models-overview")).toBeDefined();
  });

  it("switches tabs and mounts the existing panels on Catalogue and Server", () => {
    render(<LocalModelsView />);

    fireEvent.click(screen.getByRole("tab", { name: "local_models.tab_catalogue" }));
    expect(screen.getByTestId("download-panel")).toBeDefined();
    expect(screen.queryByTestId("local-models-overview")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "local_models.tab_server" }));
    expect(screen.getByTestId("base-url-field")).toBeDefined();
    expect(screen.getByTestId("runtime-panel").getAttribute("data-always")).toBe("true");
  });

  it("reveals Models in Advanced and remembers the choice", () => {
    render(<LocalModelsView />);

    fireEvent.click(screen.getByRole("tab", { name: "local_models.mode_advanced" }));
    expect(screen.getByRole("tab", { name: "local_models.tab_models" })).toBeDefined();
    expect(screen.getByRole("tab", { name: "local_models.tab_huggingface" })).toBeDefined();
    expect(window.localStorage.getItem(LOCAL_MODELS_MODE_KEY)).toBe("advanced");

    fireEvent.click(screen.getByRole("tab", { name: "local_models.tab_models" }));
    expect(screen.getByTestId("local-models-models")).toBeDefined();

    // Back to Simple: the rail drops Models and the view falls back to Overview.
    fireEvent.click(screen.getByRole("tab", { name: "local_models.mode_simple" }));
    expect(screen.queryByRole("tab", { name: "local_models.tab_models" })).toBeNull();
    expect(screen.getByTestId("local-models-overview")).toBeDefined();
  });

  it("goes back to API Keys and says so when no server can pull models", () => {
    mockProviders.providers = [{ id: "openai", label: "OpenAI" }];
    render(<LocalModelsView />);

    expect(screen.getByText("local_models.no_provider")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "local_models.back" }));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("apikeys");
  });
});
