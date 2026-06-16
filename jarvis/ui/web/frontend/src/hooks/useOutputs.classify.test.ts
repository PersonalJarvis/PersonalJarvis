import { describe, expect, it } from "vitest";

import {
  artifactDownloadUrl,
  artifactOpenUrl,
  classifyArtifact,
} from "./useOutputs";

describe("classifyArtifact", () => {
  it("classifies markdown/text/code as rendered", () => {
    expect(classifyArtifact("a/b/report.md")).toBe("rendered");
    expect(classifyArtifact("notes.txt")).toBe("rendered");
    expect(classifyArtifact("main.py")).toBe("rendered");
  });
  it("classifies pdf/html/images as inline", () => {
    expect(classifyArtifact("doc.pdf")).toBe("inline");
    expect(classifyArtifact("page.html")).toBe("inline");
    expect(classifyArtifact("pic.PNG")).toBe("inline");
  });
  it("classifies unknown binaries as opaque", () => {
    expect(classifyArtifact("archive.zip")).toBe("opaque");
    expect(classifyArtifact("blob.bin")).toBe("opaque");
  });
});

describe("artifact URLs", () => {
  const slug = "mission_abc";
  const path = "tasks/x/artifacts/files/report.md";
  it("builds an attachment download URL", () => {
    expect(artifactDownloadUrl(slug, path)).toBe(
      `/api/outputs/${slug}/files/${encodeURI(path)}/download?disposition=attachment`,
    );
  });
  it("routes markdown open to /view", () => {
    expect(artifactOpenUrl(slug, path)).toBe(
      `/api/outputs/${slug}/files/${encodeURI(path)}/view`,
    );
  });
  it("routes pdf open to inline download", () => {
    const p = "tasks/x/artifacts/files/doc.pdf";
    expect(artifactOpenUrl(slug, p)).toBe(
      `/api/outputs/${slug}/files/${encodeURI(p)}/download?disposition=inline`,
    );
  });
  it("returns null open URL for opaque files", () => {
    expect(artifactOpenUrl(slug, "tasks/x/artifacts/files/a.zip")).toBeNull();
  });
  it("encodes # in a filename instead of leaving it raw (no silent truncation)", () => {
    const p = "tasks/x/artifacts/files/report#2.md";
    const url = artifactDownloadUrl(slug, p);
    expect(url).toContain("%23");
    expect(url).not.toContain("#");
  });
});
