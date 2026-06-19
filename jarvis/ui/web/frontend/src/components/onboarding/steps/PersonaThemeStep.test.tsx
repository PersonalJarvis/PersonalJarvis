import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { PersonaThemeStep } from "./PersonaThemeStep";
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("saves the assistant name on blur (PUT, no persist:false)", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal("fetch", vi.fn().mockImplementation((u: string, i?: RequestInit) => {
    calls.push([u, i]);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  }));
  render(<PersonaThemeStep onb={{} as never} goNext={vi.fn()} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  const input = screen.getByLabelText("onboarding.persona.name_label");
  fireEvent.change(input, { target: { value: "Nova" } });
  fireEvent.blur(input);
  await waitFor(() =>
    expect(calls.some(([u, i]) => u === "/api/settings/assistant-name" && i?.method === "PUT")).toBe(true),
  );
  const body = JSON.parse(calls.find(([u]) => u === "/api/settings/assistant-name")![1]!.body as string);
  expect(body.name).toBe("Nova");
  expect(body.persist).toBeUndefined(); // never send persist:false
});
