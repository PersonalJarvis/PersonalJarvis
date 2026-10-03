import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import { nextUnlock, rewardRoad } from "../cosmetics";
import type { ProgressionSnapshot, RewardRow, XpRuleRow } from "../progressionApi";
import { useProgression } from "../progressionStore";
import type { ToyLook } from "../../office/toyFigureModel";
import { groupRules, nextTitle, quickWins, rarityOf, upcomingLevelCosts, xpToReach } from "./hallModel";
import { LevelHallScreen } from "./LevelHallScreen";
import { roadSteps } from "./RewardsTab";

const REWARDS: RewardRow[] = [
  { rewardId: "gadget_drone", slot: "gadget", levels: { person: 10, agent: 12, pet: 9 } },
  { rewardId: "frame_bronze", slot: "frame", levels: { person: 3, agent: 3, pet: 3 } },
  { rewardId: "frame_silver", slot: "frame", levels: { person: 10, agent: 10, pet: 10 } },
  { rewardId: "trail_footprints", slot: "trail", levels: { person: 2, agent: null, pet: null } },
  { rewardId: "gadget_wings", slot: "gadget", levels: { person: 40, agent: 45, pet: null } },
];
const CURVE = [0, 40, 105, 195, 310, 450, 615, 805, 1020, 1260];
const rule = (source: XpRuleRow["source"], kind: XpRuleRow["kind"], xp: number): XpRuleRow =>
  ({ source, kind, xp, trigger: "server", cooldownS: 0, dailyCap: 0 });
const RULES: XpRuleRow[] = [
  rule("chat_turn", "person", 5), rule("agent_hired", "person", 50), rule("daily_visit", "person", 25),
  rule("quest_completed", "person", 20), rule("walk_together", "pet", 3), rule("task_done", "agent", 40),
];

function snapshot(): ProgressionSnapshot {
  return {
    subjects: [{ subjectId: "person", kind: "person", xp: 130, level: 3, xpIntoLevel: 25, xpForNext: 90, title: "newcomer" }],
    petId: "gigi", recent: [], latestSeq: 0, maxLevel: 50, levelXp: CURVE, rules: RULES, rewards: REWARDS,
    titles: { person: [{ level: 1, title: "newcomer" }, { level: 5, title: "apprentice" }], agent: [], pet: [] },
  };
}

describe("hall model", () => {
  it("grades rewards by the level they open at", () => {
    expect([2, 5, 6, 15, 16, 30, 31, 50].map(rarityOf)).toEqual(["common", "common", "rare", "rare", "epic", "epic", "legendary", "legendary"]);
  });

  it("measures what is missing on the server's curve", () => {
    expect(xpToReach(CURVE, 130, 5)).toBe(180);
    expect(xpToReach(CURVE, 999, 2)).toBe(0);
    expect(xpToReach(CURVE, 0, 99)).toBe(0);
    expect(upcomingLevelCosts(CURVE, 3, 3)).toEqual([{ level: 4, xp: 90 }, { level: 5, xp: 115 }, { level: 6, xp: 140 }]);
    expect(upcomingLevelCosts(CURVE, 9, 3)).toEqual([{ level: 10, xp: 240 }]);
  });

  it("orders the road by unlock level, slot order within a level, and skips what a kind never wears", () => {
    expect(rewardRoad(REWARDS, "person").map((r) => r.rewardId))
      .toEqual(["trail_footprints", "frame_bronze", "frame_silver", "gadget_drone", "gadget_wings"]);
    expect(rewardRoad(REWARDS, "pet").map((r) => r.rewardId)).toEqual(["frame_bronze", "gadget_drone", "frame_silver"]);
    expect(nextUnlock(REWARDS, "person", 3)?.rewardId).toBe("frame_silver");
    expect(nextUnlock(REWARDS, "person", 40)).toBeUndefined();
  });

  it("builds road steps from rewards and title bands", () => {
    const steps = roadSteps(rewardRoad(REWARDS, "person"), [{ level: 1, title: "newcomer" }, { level: 5, title: "apprentice" }, { level: 10, title: "operator" }], "person");
    expect(steps.map((s) => s.level)).toEqual([2, 3, 5, 10, 40]);
    expect(steps.find((s) => s.level === 10)).toMatchObject({ title: "operator" });
    expect(steps.find((s) => s.level === 10)!.rewards.map((r) => r.rewardId)).toEqual(["frame_silver", "gadget_drone"]);
    expect(nextTitle([{ level: 5, title: "apprentice" }, { level: 1, title: "newcomer" }], 3)).toEqual({ level: 5, title: "apprentice" });
    expect(nextTitle([{ level: 5, title: "apprentice" }], 5)).toBeUndefined();
  });

  it("groups the rules for the guide and picks repeatable quick wins", () => {
    const extra = { ...rule("chat_turn", "person", 1), source: "future_rule" as XpRuleRow["source"] };
    expect(groupRules([...RULES, extra], "person").map((g) => [g.id, g.rules.map((r) => r.source)])).toEqual([
      ["jarvis", ["chat_turn"]], ["team", ["quest_completed", "agent_hired"]], ["verse", ["daily_visit"]], ["other", ["future_rule"]],
    ]);
    expect(groupRules(RULES, "pet")).toEqual([{ id: "pet", rules: [RULES[4]] }]);
    expect(quickWins(RULES, "person", 2).map((r) => r.source)).toEqual(["daily_visit", "quest_completed"]);
  });
});

describe("Level Hall screen", () => {
  beforeAll(async () => { await loadLocaleChunk("society"); });
  beforeEach(() => {
    useProgression.setState({ snapshot: null, subjects: {}, panel: null, hallSubject: "person", focusReward: null, choices: {} });
    useProgression.getState().hydrate(snapshot(), []);
  });
  afterEach(() => { cleanup(); useProgression.getState().openPanel(null); });

  const look = {} as ToyLook;
  const mount = () => render(<LevelHallScreen agents={[]} playerName="Ruby" petName="Gigi" playerLook={look} />);

  it("stays closed until a page is opened", () => {
    mount();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens on the overview with five pages and the next unlocks", () => {
    useProgression.getState().openPanel("overview");
    mount();
    const dialog = screen.getByRole("dialog");
    const tabs = within(dialog).getAllByRole("tab");
    expect(tabs).toHaveLength(5);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(within(dialog).getByText("Ruby")).toBeTruthy();
    // The next unlock (silver frame at level 10) opens in the studio, ready to try on.
    const cards = dialog.querySelectorAll(".hall-card");
    expect(cards.length).toBe(3);
    fireEvent.click(cards[0]);
    expect(useProgression.getState()).toMatchObject({ panel: "studio", focusReward: "frame_silver" });
  });

  it("switches between the person and the pet, and lists every way to earn on the guide", () => {
    useProgression.getState().openPanel("guide");
    mount();
    expect(screen.getAllByText("Gigi").length).toBeGreaterThan(0);
    const radios = screen.getAllByRole("radio");
    fireEvent.click(radios[1]);
    expect(useProgression.getState().hallSubject).toBe("pet");
    expect(document.querySelectorAll(".hall-rule")).toHaveLength(RULES.length);
  });

  it("closes on Escape", () => {
    useProgression.getState().openPanel("team");
    mount();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(useProgression.getState().panel).toBeNull();
  });

  it("forgets a focused reward when another page opens without one", () => {
    const store = useProgression.getState();
    store.openPanel("rewards", { reward: "gadget_drone", subject: "pet" });
    expect(useProgression.getState()).toMatchObject({ panel: "rewards", focusReward: "gadget_drone", hallSubject: "pet" });
    useProgression.getState().openPanel("guide");
    expect(useProgression.getState()).toMatchObject({ focusReward: null, hallSubject: "pet" });
  });
});
