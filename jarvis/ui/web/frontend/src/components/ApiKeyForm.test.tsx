/**
 * Component tests for ApiKeyForm's new help text + live key-format hint.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ApiKeyForm } from "@/components/ApiKeyForm";

afterEach(cleanup);

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
