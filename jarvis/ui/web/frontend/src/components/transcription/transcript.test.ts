import { describe, expect, it } from "vitest";
import type { VoiceTurnRow } from "@/components/sessions/types";
import { elapsedTime, exchangeText, transcriptExchanges } from "./transcript";

import { transcriptFixture } from "./testFixtures";

describe("transcript reading contract", () => {
  it("reads confirmed speech in order, retaining orphaned announcements and original wording", () => {
    const detail = transcriptFixture();
    const before = JSON.stringify(detail);
    const exchanges = transcriptExchanges(detail);
    expect(exchanges.map((exchange) => exchange.number)).toEqual([null, 1, null]);
    const turn = exchanges[1];
    expect(turn.passages.map((passage) => passage.text)).toEqual(["Find [notes], please.", "One moment.", "Here are the [notes]."]);
    expect(turn.passages.at(-1)?.pending).toBe(true);
    expect(exchangeText(turn, true, "You", "Assistant")).toContain("um find [notes] please");
    expect(exchangeText(turn, false, "You", "Assistant")).not.toContain("An unspoken draft");
    expect(JSON.stringify(detail)).toBe(before);
  });

  it("falls back to legacy replies, ignores malformed speech, and keeps stable turn numbers", () => {
    const detail = transcriptFixture();
    detail.events = [{ ...detail.events[0], payload: { text: { secret: "not speech" } } }];
    detail.turns.unshift({ ...detail.turns[0], id: "silent", user_text: "", jarvis_text: "" } as VoiceTurnRow);
    const exchanges = transcriptExchanges(detail);
    expect(exchanges).toHaveLength(1);
    expect(exchanges[0].number).toBe(2);
    expect(exchanges[0].passages.at(-1)?.text).toBe("An unspoken draft");
  });

  it("formats elapsed time without negative timestamps or minute wrapping", () => {
    expect(elapsedTime(-50)).toBe("0:00");
    expect(elapsedTime(3_661_000)).toBe("61:01");
  });
});
