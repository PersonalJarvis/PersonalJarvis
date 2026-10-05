import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import { nextUnlock, rewardRoad } from "../cosmetics";
import type { ProgressionSnapshot, RewardRow, XpRuleRow } from "../progressionApi";
import { useProgression } from "../progressionStore";
import type { ToyLook } from "../../office/toyFigureModel";
import { groupRules, nextTitle, quickWins, rankLadder, upcomingLevelCosts, xpToReach } from "./hallModel";
import { LevelHallScreen } from "./LevelHallScreen";
import { roadSteps } from "./RewardsTab";

const REWARDS: RewardRow[] = [
  { rewardId: "headwear_patrol_cap", slot: "headwear", levels: { person: 10, agent: null, pet: null } },
  { rewardId: "decoration_ribbon_bar", slot: "decoration", levels: { person: 3, agent: 3, pet: null } },
  { rewardId: "uniform_field_jacket", slot: "uniform", levels: { person: 10, agent: null, pet: null } },
  { rewardId: "uniform_service_shirt", slot: "uniform", levels: { person: 2, agent: null, pet: null } },
  { rewardId: "decoration_medals", slot: "decoration", levels: { person: 40, agent: 44, pet: null } },
];
const BANDS = [{ level: 1, title: "private" }, { level: 5, title: "private_second_class" }, { level: 10, title: "sergeant" }];
const CURVE = [0, 40, 105, 195, 310, 450, 615, 805, 1020, 1260];
const rule = (source: XpRuleRow["source"], kind: XpRuleRow["kind"], xp: number): XpRuleRow =>
  ({ source, kind, xp, trigger: "server", cooldownS: 0, dailyCap: 0 });
const RULES: XpRuleRow[] = [
  rule("chat_turn", "person", 5), rule("agent_hired", "person", 50), rule("daily_visit", "person", 25),
  rule("quest_completed", "person", 20), rule("walk_together", "pet", 3), rule("task_done", "agent", 40),
];

function snapshot(): ProgressionSnapshot {
  return {
    subjects: [{ subjectId: "person", kind: "person", xp: 130, level: 3, xpIntoLevel: 25, xpForNext: 90, title: "private" }],
    petId: "gigi", recent: [], latestSeq: 0, maxLevel: 50, levelXp: CURVE, rules: RULES, rewards: REWARDS,
    titles: { person: BANDS, agent: BANDS, pet: BANDS }, looks: { cap: 1, suit: 4, crown: 10 },
  };
}

describe("hall model", () => {
  it("reads the server's bands as the rank ladder, skipping unknown ranks", () => {
    expect(rankLadder([...BANDS, { level: 12, title: "admiral" }]).map((r) => [r.level, r.rank, r.grade, r.tier]))
      .toEqual([[1, "private", "E-1", "enlisted"], [5, "private_second_class", "E-2", "enlisted"], [10, "sergeant", "E-5", "nco"]]);
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
      .toEqual(["uniform_service_shirt", "decoration_ribbon_bar", "uniform_field_jacket", "headwear_patrol_cap", "decoration_medals"]);
    expect(rewardRoad(REWARDS, "pet")).toEqual([]);
    expect(nextUnlock(REWARDS, "person", 3)?.rewardId).toBe("uniform_field_jacket");
    expect(nextUnlock(REWARDS, "person", 40)).toBeUndefined();
  });

  it("builds road steps from rewards and promotions", () => {
    const steps = roadSteps(rewardRoad(REWARDS, "person"), BANDS, "person");
    expect(steps.map((s) => s.level)).toEqual([2, 3, 5, 10, 40]);
    expect(steps.find((s) => s.level === 10)).toMatchObject({ title: "sergeant" });
    expect(steps.find((s) => s.level === 10)!.rewards.map((r) => r.rewardId)).toEqual(["uniform_field_jacket", "headwear_patrol_cap"]);
    expect(nextTitle([BANDS[1], BANDS[0]], 3)).toEqual(BANDS[1]);
    expect(nextTitle([BANDS[1]], 5)).toBeUndefined();
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

  it("opens on the overview with six pages, the next promotion and the next unlocks", () => {
    useProgression.getState().openPanel("overview");
    mount();
    const dialog = screen.getByRole("dialog");
    const tabs = within(dialog).getAllByRole("tab");
    expect(tabs).toHaveLength(6);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(within(dialog).getByText("Ruby")).toBeTruthy();
    expect(dialog.querySelector(".hall-promotion")).toBeTruthy();
    // The next unlock (the field jacket at level 10) opens in the studio, ready to try on.
    const cards = dialog.querySelectorAll(".hall-card");
    expect(cards.length).toBe(3);
    fireEvent.click(cards[0]);
    expect(useProgression.getState()).toMatchObject({ panel: "studio", focusReward: "uniform_field_jacket" });
  });

  it("charts every rank on the ranks page and marks the one held", () => {
    useProgression.getState().openPanel("ranks");
    mount();
    const cells = document.querySelectorAll(".hall-rank-cell");
    expect(cells).toHaveLength(BANDS.length);
    expect(document.querySelectorAll(".hall-rank-cell[data-current]")).toHaveLength(1);
    expect(cells[0].hasAttribute("data-current")).toBe(true);
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
    store.openPanel("rewards", { reward: "headwear_patrol_cap", subject: "pet" });
    expect(useProgression.getState()).toMatchObject({ panel: "rewards", focusReward: "headwear_patrol_cap", hallSubject: "pet" });
    useProgression.getState().openPanel("guide");
    expect(useProgression.getState()).toMatchObject({ focusReward: null, hallSubject: "pet" });
  });
});
