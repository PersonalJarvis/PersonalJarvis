import { describe, expect, it } from "vitest";
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
