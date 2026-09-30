import { describe, expect, it, vi } from "vitest";
import { holdsSizeLead, mayLeadSize, onSizeLeadReleased, releaseSizeLead, sizeLeadKey, takeSizeLead } from "./paneSizeLead";

const flush = () => Promise.resolve();

describe("pane size lead", () => {
  it("lets every viewer size a pane nobody leads", () => {
    const key = sizeLeadKey("ws-free", "Dana");
    expect(mayLeadSize(key, {})).toBe(true);
    expect(mayLeadSize(key, {})).toBe(true);
  });

  it("keeps the size with the lead and away from the pane's other viewers", () => {
    const key = sizeLeadKey("ws-led", "Dana");
    const office = {}, grid = {};
    expect(holdsSizeLead(key, office)).toBe(false);
    takeSizeLead(key, office);
    expect(holdsSizeLead(key, office)).toBe(true);
    expect(mayLeadSize(key, office)).toBe(true);
    expect(mayLeadSize(key, grid)).toBe(false);
    // Another pane, or the same name in another workspace, is not affected.
    expect(mayLeadSize(sizeLeadKey("ws-led", "Eli"), grid)).toBe(true);
    expect(mayLeadSize(sizeLeadKey("ws-other", "Dana"), grid)).toBe(true);
    releaseSizeLead(key, office);
  });

  it("tells the other viewers once the lead is released", async () => {
    const key = sizeLeadKey("ws-release", "Dana");
    const office = {}, grid = {};
    const heard = vi.fn(() => mayLeadSize(key, grid));
    const stop = onSizeLeadReleased(key, heard);
    takeSizeLead(key, office);

    releaseSizeLead(key, office);
    expect(heard).not.toHaveBeenCalled();
    await flush();

    expect(heard).toHaveBeenCalledOnce();
    expect(heard).toHaveReturnedWith(true);
    stop();
  });

  it("says nothing when the lead is taken again before anyone heard (a rebuild)", async () => {
    const key = sizeLeadKey("ws-rebuild", "Dana");
    const heard = vi.fn();
    const stop = onSizeLeadReleased(key, heard);
    const before = {}, after = {};
    takeSizeLead(key, before);

    releaseSizeLead(key, before);
    takeSizeLead(key, after);
    await flush();

    expect(heard).not.toHaveBeenCalled();
    stop();
    releaseSizeLead(key, after);
  });

  it("releases nothing for a viewer that already lost the lead", async () => {
    const key = sizeLeadKey("ws-lost", "Dana");
    const heard = vi.fn();
    const stop = onSizeLeadReleased(key, heard);
    const office = {}, grid = {};
    takeSizeLead(key, office);
    takeSizeLead(key, grid);

    releaseSizeLead(key, office);
    await flush();

    expect(heard).not.toHaveBeenCalled();
    expect(mayLeadSize(key, office)).toBe(false);
    stop();
    releaseSizeLead(key, grid);
  });

  it("stops telling a viewer that unsubscribed", async () => {
    const key = sizeLeadKey("ws-stop", "Dana");
    const heard = vi.fn();
    const office = {};
    onSizeLeadReleased(key, heard)();
    takeSizeLead(key, office);

    releaseSizeLead(key, office);
    await flush();

    expect(heard).not.toHaveBeenCalled();
  });
});
