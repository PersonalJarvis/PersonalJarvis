import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SilenceWindowGroup } from "./SilenceWindowGroup";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function mockGet(ms = 1500) {
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ ms, default: 1500, min: 500, max: 5000 }),
  });
}

describe("SilenceWindowGroup", () => {
  it("renders the slider at the fetched value", async () => {
    mockGet(1500);
    render(<SilenceWindowGroup />);
    const slider = (await screen.findByRole("slider")) as HTMLInputElement;
    expect(slider.value).toBe("1500");
    // getByText throws if absent, so reaching the truthy assert means it rendered.
    expect(screen.getByText("1.5 s")).toBeTruthy();
  });

  it("sends one PUT on commit, not per tick", async () => {
    mockGet(1500);
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ok: true, ms: 2500, default: 1500,
        persisted: true, applied_live: true, restart_required: false,
      }),
    });
    render(<SilenceWindowGroup />);
    const slider = (await screen.findByRole("slider")) as HTMLInputElement;
    // drag (onChange) updates the label but does not PUT yet
    fireEvent.change(slider, { target: { value: "2500" } });
    expect(fetchMock).toHaveBeenCalledTimes(1); // only the GET so far
    // release (commit) fires the PUT
    fireEvent.mouseUp(slider);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const putCall = fetchMock.mock.calls[1];
    expect(putCall[0]).toBe("/api/settings/silence-window");
    expect(JSON.parse(putCall[1].body)).toMatchObject({ ms: 2500 });
  });

  it("reset commits 1500", async () => {
    mockGet(3000);
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ok: true, ms: 1500, default: 1500,
        persisted: true, applied_live: true, restart_required: false,
      }),
    });
    render(<SilenceWindowGroup />);
    await screen.findByRole("slider");
    fireEvent.click(screen.getByRole("button", { name: /reset|zurück|restablecer/i })); // i18n-allow: multilingual button-name regex
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ ms: 1500 });
  });
});
