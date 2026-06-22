import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ProviderBillingBadge } from "@/components/ProviderBillingBadge";

afterEach(cleanup);

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
    expect(screen.getByText(/subscription or api/i)).toBeTruthy();
  });

  it("labels a local provider", () => {
    render(<ProviderBillingBadge billing="local" />);
    expect(screen.getByText(/local/i)).toBeTruthy();
  });
});
