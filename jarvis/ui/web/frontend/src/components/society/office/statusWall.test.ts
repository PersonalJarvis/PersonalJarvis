import { describe, expect, it } from "vitest";
import type { CostSummary } from "@/hooks/useCosts";
import { formatCount, formatUsd, ideSpendFrom } from "./ServerRoom";

const bucket = (key: string, cost_usd: number) => ({ key, cost_usd }) as CostSummary["series"][number];

describe("server room status wall: Agentic IDE spend", () => {
  it("reads cost, what an API key paid, tokens, sessions and the costliest models", () => {
    const summary = {
      totals: { cost_usd: 120, subscription_usd: 100, tokens_total: 3_400_000 },
      refs_total: 12,
      series: [bucket("d1", 20), bucket("d2", 100)],
      by_model: [bucket("small", 5), bucket("big", 90), bucket("mid", 25)],
    } as unknown as CostSummary;
    const spend = ideSpendFrom(summary, 30);
    expect(spend).toMatchObject({ days: 30, cost: 120, billed: 20, tokens: 3_400_000, sessions: 12 });
    expect(spend?.series.map((s) => s.cost)).toEqual([20, 100]);
    expect(spend?.models.map((m) => m.name)).toEqual(["big", "mid", "small"]);
    expect(ideSpendFrom(undefined, 30)).toBeNull();
  });

  it("formats money and counts to fit a tile", () => {
    expect(formatUsd(12694.39)).toBe("$12,694");
    expect(formatUsd(202.85)).toBe("$202.85");
    expect(formatUsd(0.0421)).toBe("$0.042");
    expect(formatUsd(0)).toBe("$0");
    expect(formatCount(16_775_188_426)).toBe("16.8B");
    expect(formatCount(34_500_000)).toBe("34.5M");
    expect(formatCount(812_400)).toBe("812K");
    expect(formatCount(950)).toBe("950");
  });
});
