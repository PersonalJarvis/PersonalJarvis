import { describe, expect, it, vi } from "vitest";
import { createSectionPrefetcher } from "./sectionPrefetch";

describe("section prefetch", () => {
  it("does no work at registration and only warms the requested section", async () => {
    const registry = createSectionPrefetcher();
    const agents = vi.fn().mockResolvedValue({});
    const docs = vi.fn().mockResolvedValue({});
    registry.register(["agents"], agents);
    registry.register(["docs"], docs);
    expect(agents).not.toHaveBeenCalled();
    expect(docs).not.toHaveBeenCalled();
    await registry.prefetch("agents");
    await registry.prefetch("chats");
    expect(agents).toHaveBeenCalledTimes(1);
    expect(docs).not.toHaveBeenCalled();
  });

  it("deduplicates in-flight and completed requests across section aliases", async () => {
    const registry = createSectionPrefetcher();
    let resolve!: () => void;
    const loader = vi.fn(() => new Promise<void>((done) => { resolve = done; }));
    registry.register(["agentic-ide", "agentic-ide-classic"], loader);
    const first = registry.prefetch("agentic-ide");
    expect(registry.prefetch("agentic-ide-classic")).toBe(first);
    await Promise.resolve();
    resolve();
    await first;
    await registry.prefetch("agentic-ide");
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("permits retry after a rejected or synchronously throwing loader", async () => {
    const registry = createSectionPrefetcher();
    const loader = vi.fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockImplementationOnce(() => { throw new Error("unavailable"); })
      .mockResolvedValue({});
    registry.register(["docs"], loader);
    await registry.prefetch("docs");
    await registry.prefetch("docs");
    await registry.prefetch("docs");
    await registry.prefetch("docs");
    expect(loader).toHaveBeenCalledTimes(3);
  });
});
