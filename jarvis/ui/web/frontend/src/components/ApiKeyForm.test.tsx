/**
 * Component tests for ApiKeyForm: help text, live key-format hint, and the
 * fallback-aware credential states (field report 2026-07-21) — a slot the
 * runtime already serves via the shared family key renders a "covered" note
 * instead of an empty input, and deleting a slot other surfaces read needs a
 * second, named confirmation.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiKeyForm } from "@/components/ApiKeyForm";
import { deleteSecret } from "@/hooks/useProviders";

vi.mock("@/hooks/useProviders", () => ({
  deleteSecret: vi.fn(async () => undefined),
  postSecret: vi.fn(async () => undefined),
}));

afterEach(cleanup);
beforeEach(() => {
  vi.mocked(deleteSecret).mockClear();
});

describe("ApiKeyForm credential help", () => {
  it("renders the plain-English credential help text", () => {
    render(
      <ApiKeyForm
        secretKey="anthropic_api_key"
        dashboardUrl="https://console.anthropic.com/settings/keys"
        configured={false}
        credentialHelp="Anthropic API key (starts with sk-ant-). Billed per token."
      />,
    );
    expect(screen.getByText(/starts with sk-ant-/i)).toBeTruthy();
  });
});

describe("ApiKeyForm 'get your key' link", () => {
  it("links to the official dashboard while entering a key", () => {
    render(
      <ApiKeyForm
        secretKey="anthropic_api_key"
        dashboardUrl="https://console.anthropic.com/settings/keys"
        configured={false}
      />,
    );
    const link = screen.getByRole("link") as HTMLAnchorElement;
    expect(link.href).toContain("console.anthropic.com");
  });

  it("keeps the dashboard link visible even when a key is already configured", () => {
    render(
      <ApiKeyForm
        secretKey="anthropic_api_key"
        dashboardUrl="https://console.anthropic.com/settings/keys"
        configured={true}
      />,
    );
    const link = screen.getByRole("link") as HTMLAnchorElement;
    expect(link.href).toContain("console.anthropic.com");
  });
});

describe("ApiKeyForm live key-format hint", () => {
  it("warns when an Anthropic key is pasted into the OpenAI field", () => {
    render(
      <ApiKeyForm secretKey="openai_api_key" dashboardUrl={null} configured={false} />,
    );
    const input = screen.getByPlaceholderText(/openai_api_key/i);
    fireEvent.change(input, { target: { value: "sk-ant-api03-wrong" } });
    expect(screen.getByText(/anthropic/i)).toBeTruthy();
  });

  it("flags a Vertex service-account JSON in the AI-Studio Gemini field", () => {
    render(
      <ApiKeyForm secretKey="gemini_api_key" dashboardUrl={null} configured={false} />,
    );
    const input = screen.getByPlaceholderText(/gemini_api_key/i);
    fireEvent.change(input, {
      target: { value: '{"type":"service_account","project_id":"x"}' },
    });
    expect(screen.getByText(/vertex/i)).toBeTruthy();
  });

  it("does not warn for a correctly-formatted key", () => {
    render(
      <ApiKeyForm secretKey="openai_api_key" dashboardUrl={null} configured={false} />,
    );
    const input = screen.getByPlaceholderText(/openai_api_key/i);
    fireEvent.change(input, { target: { value: "sk-proj-correct123" } });
    expect(screen.queryByText(/expects a different key/i)).toBeNull();
  });
});

describe("ApiKeyForm shared-key covered state", () => {
  it("renders the covered note instead of an input when a fallback exists", () => {
    render(
      <ApiKeyForm
        secretKey="realtime_openai_api_key"
        dashboardUrl={null}
        configured={false}
        effectiveConfigured={true}
        sharedWith={["OpenAI"]}
      />,
    );
    expect(screen.queryByLabelText(/enter realtime_openai_api_key/i)).toBeNull();
    expect(screen.getByText(/shared key/i)).toBeTruthy();

    // The dedicated key stays available as an explicit optional upgrade.
    fireEvent.click(screen.getByRole("button", { name: /add dedicated key/i }));
    expect(screen.getByLabelText(/enter realtime_openai_api_key/i)).toBeTruthy();
  });

  it("still renders the open input when no fallback covers the slot", () => {
    render(
      <ApiKeyForm
        secretKey="nvidia_api_key"
        dashboardUrl={null}
        configured={false}
        effectiveConfigured={false}
      />,
    );
    expect(screen.getByLabelText(/enter nvidia_api_key/i)).toBeTruthy();
  });
});

describe("ApiKeyForm shared-key delete confirmation", () => {
  it("requires a second, named confirmation before deleting a shared slot", async () => {
    render(
      <ApiKeyForm
        secretKey="openai_api_key"
        dashboardUrl={null}
        configured={true}
        sharedWith={["OpenAI Whisper STT", "OpenAI TTS", "OpenAI Codex"]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /delete openai_api_key/i }));
    expect(deleteSecret).not.toHaveBeenCalled();
    expect(screen.getByText(/OpenAI Whisper STT/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /delete anyway/i }));
    await waitFor(() => expect(deleteSecret).toHaveBeenCalledWith("openai_api_key"));
  });

  it("cancel keeps the key and hides the warning", () => {
    render(
      <ApiKeyForm
        secretKey="openai_api_key"
        dashboardUrl={null}
        configured={true}
        sharedWith={["OpenAI TTS"]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /delete openai_api_key/i }));
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(deleteSecret).not.toHaveBeenCalled();
    expect(screen.queryByText(/OpenAI TTS/)).toBeNull();
  });

  it("deletes an unshared slot on the first click", async () => {
    render(
      <ApiKeyForm
        secretKey="openrouter_api_key"
        dashboardUrl={null}
        configured={true}
        sharedWith={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /delete openrouter_api_key/i }));
    await waitFor(() =>
      expect(deleteSecret).toHaveBeenCalledWith("openrouter_api_key"),
    );
  });
});
