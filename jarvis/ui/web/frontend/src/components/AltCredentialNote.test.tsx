import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AltCredentialNote } from "@/components/AltCredentialNote";
import type { AltCredential } from "@/hooks/useProviders";

afterEach(cleanup);

const VERTEX: AltCredential = {
  label: "Vertex AI (service account)",
  billing: "api",
  credential_help:
    "Bill Gemini through a Google Cloud Vertex AI project instead of an AI Studio key.",
  dashboard_url: "https://console.cloud.google.com/iam-admin/serviceaccounts",
  credential_path_hint: "~/.config/jarvis/vertex-sa.json",
};

describe("AltCredentialNote", () => {
  it("shows the alternative path's label and help", () => {
    render(<AltCredentialNote alt={VERTEX} />);
    expect(screen.getByText(/Alternative: Vertex AI/i)).toBeTruthy();
    expect(screen.getByText(/instead of an AI Studio key/i)).toBeTruthy();
  });

  it("links to the alternative dashboard", () => {
    render(<AltCredentialNote alt={VERTEX} />);
    const link = screen.getByRole("link") as HTMLAnchorElement;
    expect(link.href).toContain("cloud.google.com");
  });
});
