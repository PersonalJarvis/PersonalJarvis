import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DistributedSettings, type DistributedConfig } from "./DistributedSettings";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const config: DistributedConfig = { enabled: false, postgres_configured: true, redis_configured: false, object_credentials_configured: false, s3_endpoint: "https://objects.example.invalid", s3_bucket: "team-data", s3_region: "us-east-1", max_concurrency: 1000, available: false, reason: "Configure Redis and object credentials" };
describe("optional distributed setup", () => {
  it("loads only when expanded and never renders stored secrets", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(config)));
    vi.stubGlobal("fetch", fetcher);
    render(<DistributedSettings onSaved={() => {}} />);
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Optional distributed execution", { selector: "summary" }));
    await screen.findByRole("form", { name: "Optional distributed execution" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const field = screen.getByLabelText("PostgreSQL connection", { exact: false }) as HTMLInputElement;
    expect(field.type).toBe("password"); expect(field.value).toBe("");
    expect(field.placeholder).toBe("Stored; leave blank to keep");
  });
  it("writes only supplied credentials and clears them after successful save", async () => {
    let payload: Record<string, unknown> = {};
    vi.stubGlobal("fetch", async (_path: string, init?: RequestInit) => {
      if (init?.method === "PUT") payload = JSON.parse(String(init.body));
      return new Response(JSON.stringify(config));
    });
    const saved = vi.fn();
    render(<DistributedSettings onSaved={saved} />);
    fireEvent.click(screen.getByText("Optional distributed execution", { selector: "summary" }));
    const field = await screen.findByLabelText("Redis connection", { exact: false });
    fireEvent.change(field, { target: { value: "fixture-redis-connection" } });
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await screen.findByText("Distributed configuration saved");
    expect(payload.redis_url).toBe("fixture-redis-connection");
    expect(payload).not.toHaveProperty("postgres_dsn");
    expect(payload).not.toHaveProperty("s3_secret_access_key");
    expect(payload.enabled).toBe(false);
    expect((screen.getByLabelText("Redis connection", { exact: false }) as HTMLInputElement).value).toBe("");
    expect(saved).toHaveBeenCalledOnce();
  });
  it("reports setup failure locally and allows retry", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", async () => { calls++; return calls === 1 ? new Response(JSON.stringify({ detail: "Optional services are offline" }), { status: 503 }) : new Response(JSON.stringify(config)); });
    render(<DistributedSettings onSaved={() => {}} />);
    fireEvent.click(screen.getByText("Optional distributed execution", { selector: "summary" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getByRole("form", { name: "Optional distributed execution" })).toBeTruthy());
    expect(calls).toBe(2);
  });
});
