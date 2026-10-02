import { afterEach, describe, expect, it } from "vitest";
import { usePermissionsStore } from "@/store/permissions";
import { searchSettingsOptions, searchSettingsPages } from "./settingsSearch";

const translate = (key: string) => key;

describe("searchSettingsOptions", () => {
  it("finds labels inside a Settings group", () => {
    const matches = searchSettingsOptions("en", "microphone", translate);
    expect(matches.some((match) => match.id === "audio-devices" && match.detail === "Microphone"))
      .toBe(true);
  });

  it("finds localized options in the selected UI language", () => {
    const matches = searchSettingsOptions("de", "Denkpause", translate);
    expect(matches.some((match) => match.id === "silence-window")).toBe(true);
  });

  it("finds a field in another Settings page", () => {
    const matches = searchSettingsPages("en", "What is it?", translate);
    expect(matches.some((match) => match.id === "feedback")).toBe(true);
  });

  it("ignores empty queries", () => {
    expect(searchSettingsOptions("en", "   ", translate)).toEqual([]);
  });
});

describe("the Privacy group (macOS only)", () => {
  afterEach(() => usePermissionsStore.setState({ snapshot: null }));

  const onMac = () => usePermissionsStore.setState({ snapshot: { platform: "darwin" } as never });

  it("is found by its title and by each permission row, in every language", () => {
    onMac();

    for (const [language, query] of [
      ["en", "permissions"],
      ["en", "accessibility"],
      ["en", "input monitoring"],
      ["de", "Bedienungshilfen"],
      ["de", "Berechtigungen"],
      ["es", "Accesibilidad"],
      ["es", "Permisos"],
    ] as const) {
      const matches = searchSettingsOptions(language, query, translate);
      expect(matches.some((match) => match.id === "permissions"), `${language} ${query}`).toBe(true);
    }
  });

  it("is found by the nav label 'Privacy'", () => {
    onMac();

    expect(searchSettingsOptions("en", "privacy", (key) => (key === "settings_view.nav.permissions" ? "Privacy" : key)))
      .toEqual(expect.arrayContaining([expect.objectContaining({ id: "permissions" })]));
  });

  it("is never offered where the page has no such group (Windows, Linux)", () => {
    usePermissionsStore.setState({ snapshot: { platform: "win32" } as never });

    expect(searchSettingsOptions("en", "accessibility", translate).some((match) => match.id === "permissions")).toBe(false);
  });
});
