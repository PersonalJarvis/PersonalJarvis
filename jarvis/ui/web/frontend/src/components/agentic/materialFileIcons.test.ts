import { describe, expect, it } from "vitest";
import materialIconManifest from "material-icon-theme/dist/material-icons.json";

import {
  materialFileIcon,
  type MaterialIconManifest,
} from "./materialFileIcons";

const manifest = materialIconManifest as MaterialIconManifest;

describe("materialFileIcon", () => {
  it.each([
    ["README.md", "readme"],
    ["App.tsx", "react_ts"],
    ["package.json", "nodejs"],
    [".gitignore", "git"],
    ["Dockerfile", "docker"],
    ["report.pdf", "pdf"],
  ])("maps %s to the complete Material Icon Theme", (name, id) => {
    const icon = materialFileIcon({ name }, manifest);
    expect(icon).toEqual({
      id,
      src: expect.stringContaining("/assets/material-file-icons/"),
    });
  });

  it("uses compound extensions before their shorter suffix", () => {
    expect(materialFileIcon({ name: "Widget.test.tsx" }, manifest).id).toBe(
      "test-jsx",
    );
  });

  it("provides named folder icons and a safe fallback", () => {
    expect(
      materialFileIcon({ name: "src", directory: true }, manifest).id,
    ).toBe("folder-src");
    expect(
      materialFileIcon({ name: "unknown.custom-extension" }, manifest).id,
    ).toBe("file");
  });
});
