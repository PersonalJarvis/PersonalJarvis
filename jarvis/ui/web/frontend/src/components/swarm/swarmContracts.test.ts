import { describe, expect, it, vi } from "vitest";
import { listTeams, validSnapshot, validTeamListItem } from "./api";
import { usdToMicro } from "./CreateTeamForm";
import { allowedControls, budgetPercent, exactCount, isTeamUnavailable, microUsd } from "./types";
import { agentFixture, snapshotFixture, teamFixture, unavailableTeamFixture } from "./testFixtures";
import { cameraKey, readCamera, saveCamera, worldNodes } from "./worldLayout";
import en from "@/i18n/locales/swarm/en.json";
import de from "@/i18n/locales/swarm/de.json";
import es from "@/i18n/locales/swarm/es.json";

describe("Swarm wire and isolated world contracts", () => {
  it("keeps healthy local teams visible beside an unavailable remote identity", async () => {
    const rows = [teamFixture("local"), unavailableTeamFixture("remote")];
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(rows)));
    try { expect(await listTeams()).toEqual(rows); }
    finally { vi.unstubAllGlobals(); }
  });
  it("preserves unavailable identities without accepting fabricated state or usage", () => {
    const unavailable = unavailableTeamFixture();
    expect(validTeamListItem(unavailable)).toBe(true);
    expect(isTeamUnavailable(unavailable)).toBe(true);
    expect(validTeamListItem(teamFixture())).toBe(true);
    expect(isTeamUnavailable(teamFixture())).toBe(false);
    expect(validTeamListItem({ ...unavailable, state: "failed" })).toBe(false);
    expect(validTeamListItem({ ...unavailable, tokens_used: "0" })).toBe(false);
    expect(validTeamListItem({ ...unavailable, available: true })).toBe(false);
    expect(validTeamListItem({ ...unavailable, error: undefined })).toBe(false);
    expect(validTeamListItem({ ...teamFixture(), state: "unavailable" })).toBe(false);
    expect(validSnapshot({ ...snapshotFixture(), team: unavailable }, unavailable.id)).toBe(false);
  });
  it("rejects foreign nodes, unsafe numeric counters and oversized projections", () => {
    const good = snapshotFixture();
    expect(validSnapshot(good, "alpha")).toBe(true);
    expect(validSnapshot(good, "beta")).toBe(false);
    expect(validSnapshot({ ...good, agents: [agentFixture("beta")] }, "alpha")).toBe(false);
    expect(validSnapshot({ ...good, counts: { agents: 10000000000 } }, "alpha")).toBe(false);
    expect(validSnapshot({ ...good, agents: Array(101).fill(good.agents[0]) }, "alpha")).toBe(false);
    expect(validSnapshot({ ...good, team: { ...good.team, tokens_used: 10000000000 } }, "alpha")).toBe(false);
    expect(validSnapshot({ ...good, revision: "1e9" }, "alpha")).toBe(false);
  });
  it("keeps huge budgets and micro-USD exact without floating-point conversion", () => {
    expect(usdToMicro("9007199254740993.123456")).toBe("9007199254740993123456");
    expect(microUsd("9007199254740993123456")).toBe(`$${BigInt("9007199254740993").toLocaleString()}.123456`);
    expect(exactCount("10000000000")).toBe(BigInt("10000000000").toLocaleString());
    expect(budgetPercent("9007199254740993", "9007199254740993", "9007199254740993")).toBe(100);
    expect(usdToMicro("")).toBeNull();
    expect(() => usdToMicro("1e6")).toThrow();
    expect(() => usdToMicro("1.1234567")).toThrow();
  });
  it("renders bounded aggregate groups instead of historical workers", () => {
    const snapshot = snapshotFixture();
    snapshot.aggregated = true;
    snapshot.groups = Array.from({ length: 60 }, (_, index) => ({ id: `g${index}`, title: `Group ${index}`, agents: "1000000", counts: { running: "1000" }, level: 2 }));
    expect(worldNodes(snapshot)).toHaveLength(50);
    expect(worldNodes(snapshot).every(node => !node.agent && node.group)).toBe(true);
    snapshot.aggregated = false;
    snapshot.agents = Array.from({ length: 200 }, (_, index) => ({ ...agentFixture("alpha", `a${index}`), role: "worker" }));
    expect(worldNodes(snapshot)).toHaveLength(100);
  });
  it("keeps camera preferences independent and rejects corrupted camera data", () => {
    sessionStorage.clear();
    saveCamera("alpha", { yaw: 2, zoom: 2, focus: [1, 2] });
    expect(readCamera("alpha").zoom).toBe(2);
    expect(readCamera("beta").zoom).toBe(1);
    sessionStorage.setItem(cameraKey("alpha"), '{"zoom":1000}');
    expect(readCamera("alpha").zoom).toBe(1);
  });
  it("exposes controls only for their durable lifecycle", () => {
    expect(allowedControls("created")).toEqual(["start", "cancel"]);
    expect(allowedControls("running")).toContain("stop");
    expect(allowedControls("paused")).toContain("resume");
    expect(allowedControls("succeeded")).toEqual(["archive"]);
    expect(allowedControls("archived")).toEqual([]);
  });
  it("has complete English, German and Spanish surface parity", () => {
    expect(Object.keys(de).sort()).toEqual(Object.keys(en).sort());
    expect(Object.keys(es).sort()).toEqual(Object.keys(en).sort());
  });
});
