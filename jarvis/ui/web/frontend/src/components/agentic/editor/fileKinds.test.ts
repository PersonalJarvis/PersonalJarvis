import { describe, expect, it } from "vitest";

import { renderKindOf, viewKindOf } from "./fileKinds";

describe("editor file kinds", () => {
  it("shows media and documents with a viewer, case-insensitively", () => {
    expect(viewKindOf("docs/Guide.PDF")).toBe("pdf");
    expect(viewKindOf("a/b/logo.png")).toBe("image");
    expect(viewKindOf("clip.webm")).toBe("video");
    expect(viewKindOf("voice.mp3")).toBe("audio");
    expect(viewKindOf("report.docx")).toBe("document");
  });

  it("opens everything else as text", () => {
    expect(viewKindOf("src/main.py")).toBeNull();
    expect(viewKindOf("Makefile")).toBeNull();
    expect(viewKindOf(".env.example")).toBeNull();
    expect(viewKindOf("icon.svg")).toBeNull();
  });

  it("offers a rendered view for Markdown, HTML and SVG", () => {
    expect(renderKindOf("README.md")).toBe("markdown");
    expect(renderKindOf("site/index.HTML")).toBe("html");
    expect(renderKindOf("icon.svg")).toBe("svg");
    expect(renderKindOf("main.py")).toBeNull();
  });
});
