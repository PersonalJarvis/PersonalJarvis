import { describe, expect, it } from "vitest";

import { cleanRequest, requestHeadline } from "@/lib/runRequest";

describe("cleanRequest", () => {
  it("drops the builder's supporting-context tail and collapses whitespace", () => {
    const utterance =
      "No, just spawn them.\n\nSupporting context from the recent conversation " +
      "(use only to resolve references; the underlying request remains authoritative):\n" +
      "- Conversation context (recent turns, newest last): …";
    expect(cleanRequest(utterance)).toBe("No, just spawn them.");
  });

  it("returns a plain request whole", () => {
    expect(cleanRequest("  Write   notes  ")).toBe("Write notes");
    expect(cleanRequest(undefined)).toBe("");
  });
});

describe("requestHeadline", () => {
  it("keeps a short request as it is", () => {
    expect(requestHeadline("Write notes")).toBe("Write notes");
  });

  it("takes the first sentence when it is short enough", () => {
    expect(
      requestHeadline(
        "Please start five new cloud coding instances. They should all do the same thing and " +
          "then report back in great detail on what happened.",
      ),
    ).toBe("Please start five new cloud coding instances.");
  });

  it("cuts a long single sentence at a word boundary with an ellipsis", () => {
    const text =
      "a quick deep dive that analyses all my e-mails through the Gmail plugin and then " +
      "tells me which ones matter and which ones I can delete";
    const headline = requestHeadline(text, 60);
    expect(headline.endsWith("…")).toBe(true);
    expect(headline.length).toBeLessThanOrEqual(61);
    expect(headline).toBe("a quick deep dive that analyses all my e-mails through the…");
  });
});
