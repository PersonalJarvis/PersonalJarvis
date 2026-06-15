import { describe, expect, it } from "vitest";
import { buildMissionDragPayload, MISSION_DND_MIME } from "./OutputsView";

describe("buildMissionDragPayload", () => {
  it("serialises the fields the dock needs", () => {
    const json = buildMissionDragPayload({
      slug: "20260615__x__abc",
      utterance: "recherchiere AI-News",
      status: "success",
      summary: "Three reports.",
      mission_id: "019ecb",
    });
    const parsed = JSON.parse(json);
    expect(parsed).toMatchObject({
      slug: "20260615__x__abc",
      utterance: "recherchiere AI-News",
      status: "success",
      summary: "Three reports.",
      mission_id: "019ecb",
    });
  });

  it("exports a stable DnD MIME type", () => {
    expect(MISSION_DND_MIME).toBe("application/x-jarvis-mission");
  });

  it("tolerates a card with only a slug", () => {
    const parsed = JSON.parse(buildMissionDragPayload({ slug: "only-slug" }));
    expect(parsed.slug).toBe("only-slug");
    expect(parsed.utterance).toBe("");
    expect(parsed.status).toBe("unknown");
  });
});
