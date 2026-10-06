/** The limit line under an XP rule: once a day, once per floor, a cooldown, a daily cap. */
import type { XpRuleRow } from "../progressionApi";

function duration(seconds: number, t: (key: string) => string): string {
  return seconds >= 60
    ? t("society.level.minutes").replace("{0}", String(Math.round(seconds / 60)))
    : t("society.level.seconds").replace("{0}", String(seconds));
}

export function ruleLimit(rule: XpRuleRow, t: (key: string) => string): string {
  if (rule.source === "daily_visit") return t("society.level.limit_daily");
  if (rule.source === "floor_discovered") return t("society.level.limit_floor");
  if (rule.source === "agent_hired") return t("society.level.limit_agent");
  const parts: string[] = [];
  if (rule.cooldownS > 0) parts.push(t("society.level.limit_cooldown").replace("{0}", duration(rule.cooldownS, t)));
  if (rule.dailyCap > 0) parts.push(t("society.level.limit_cap").replace("{0}", String(rule.dailyCap)));
  return parts.join(" · ");
}
