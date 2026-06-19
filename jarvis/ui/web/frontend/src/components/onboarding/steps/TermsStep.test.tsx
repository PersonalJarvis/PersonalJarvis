import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { TermsStep } from "./TermsStep";
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function stubTerms() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ version: "1.0", text: "THE TERMS BODY" }),
    }),
  );
}

it("renders the fetched terms body", async () => {
  stubTerms();
  render(<TermsStep onb={{ acceptTerms: vi.fn() } as never} goNext={vi.fn()} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  await waitFor(() => expect(screen.getByText("THE TERMS BODY")).toBeDefined());
});

it("blocks continue until accepted, then accepts + advances", async () => {
  stubTerms();
  const goNext = vi.fn();
  const acceptTerms = vi.fn().mockResolvedValue(undefined);
  render(<TermsStep onb={{ acceptTerms } as never} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  const cta = screen.getByRole("button", { name: "onboarding.terms.continue" });
  expect((cta as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox"));
  expect((cta as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(cta);
  await waitFor(() => expect(acceptTerms).toHaveBeenCalled());
  expect(goNext).toHaveBeenCalled();
});
