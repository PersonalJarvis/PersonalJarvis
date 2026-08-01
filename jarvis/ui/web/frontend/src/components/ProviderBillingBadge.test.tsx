import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const i18n = vi.hoisted(() => ({ language: "en" }));
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => {
    const values: Record<string, Record<string, string>> = {
      en: {
        "provider_billing.api": "API, billed per token",
        "provider_billing.subscription": "Subscription login",
        "provider_billing.subscription_or_api": "Subscription login or API key",
        "provider_billing.local": "Local, no key needed",
      },
      de: {
        "provider_billing.subscription": "Subscription-Login",
      },
    };
    return values[i18n.language]?.[key] ?? key;
  },
}));

import { ProviderBillingBadge } from "@/components/ProviderBillingBadge";

afterEach(cleanup);
beforeEach(() => {
  i18n.language = "en";
});

describe("ProviderBillingBadge", () => {
  it("labels an API provider as pay-per-token", () => {
    render(<ProviderBillingBadge billing="api" />);
    expect(screen.getByText(/per token/i)).toBeTruthy();
  });

  it("labels a subscription provider", () => {
    render(<ProviderBillingBadge billing="subscription" />);
    expect(screen.getByText(/subscription/i)).toBeTruthy();
  });

  it("labels the dual codex path as subscription or API key", () => {
    render(<ProviderBillingBadge billing="subscription_or_api" />);
    expect(screen.getByText(/subscription login or api key/i)).toBeTruthy();
  });

  it("labels a local provider", () => {
    render(<ProviderBillingBadge billing="local" />);
    expect(screen.getByText(/local/i)).toBeTruthy();
  });

  it("uses the active UI language instead of a hardcoded badge", () => {
    i18n.language = "de";
    render(<ProviderBillingBadge billing="subscription" />);
    expect(screen.getByText("Subscription-Login")).toBeTruthy();
  });
});
