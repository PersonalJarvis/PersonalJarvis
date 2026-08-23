import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SilenceWindowGroup } from "./SilenceWindowGroup";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function mockGet(ms = 0) {
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      ms,
      default: 0,
      min: 0,
      max: 5000,
      manual_min: 500,
      automatic: ms === 0,
    }),
  });
}

function mockPut(ms: number) {
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      ok: true,
      ms,
      default: 0,
      manual_min: 500,
      automatic: ms === 0,
      persisted: true,
      applied_live: true,
      restart_required: false,
    }),
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

  it("labels the default as automatic rather than a duration", async () => {
    // 0 is the shipped default: every voice engine keeps its factory timing,
    // so the slider must not imply a 0.0 s window (maintainer 2026-08-23).
    mockGet(0);
    render(<SilenceWindowGroup />);
    const slider = (await screen.findByRole("slider")) as HTMLInputElement;
    expect(slider.value).toBe("0");
    expect(screen.queryByText("0.0 s")).toBeNull();
    // The word appears in the value badge and again in the caption below it.
    expect(screen.getAllByText(/automat/i).length).toBeGreaterThan(0); // i18n-allow: multilingual label stem
  });

  it("sends one PUT on commit, not per tick", async () => {
    mockGet(1500);
    mockPut(2500);
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

  it("snaps the dead gap below the floor to the nearer end", async () => {
    // Between automatic (0) and the lowest real window (500) there is nothing
    // valid, so a drag through the gap must land on one side or the other —
    // never on a value the backend would have to correct behind the user.
    mockGet(1500);
    mockPut(500);
    render(<SilenceWindowGroup />);
    const slider = (await screen.findByRole("slider")) as HTMLInputElement;
    fireEvent.change(slider, { target: { value: "100" } });
    expect(slider.value).toBe("0"); // nearer to automatic
    fireEvent.change(slider, { target: { value: "400" } });
    expect(slider.value).toBe("500"); // nearer to the floor
    fireEvent.mouseUp(slider);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ ms: 500 });
  });

  it("reset commits the automatic default", async () => {
    mockGet(3000);
    mockPut(0);
    render(<SilenceWindowGroup />);
    await screen.findByRole("slider");
    fireEvent.click(screen.getByRole("button", { name: /reset|zurück|restablecer/i })); // i18n-allow: multilingual button-name regex
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ ms: 0 });
  });
});
