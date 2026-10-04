import { describe, expect, it } from "vitest";
import { lexicalAgentMatches, mergeAgentMatches, needsSemanticSearch } from "./agentLexicalSearch";
import type { AgentSearchDocument } from "./agentSearch";

const documents: AgentSearchDocument[] = [
  { id: "notes", name: "T1", texts: ["Obsidian notes"] },
  { id: "auth", name: "T2", texts: ["OAuth reconnect", "Refresh expired plugin tokens"] },
  { id: "video", name: "T10", texts: ["OBS recording", "Replace the demo video"] },
  { id: "security", name: "T3", texts: ["Security audit"] },
];
const ids = (query: string, docs = documents) => lexicalAgentMatches(query, docs).map(({ id }) => id);

describe("instant terminal word search", () => {
  it("finds an acronym without needing the full title or a model", () => {
    expect(ids("OAuth")).toEqual(["auth"]);
    expect(ids("OBS")).toEqual(["video"]);
    expect(ids("obs")).toEqual(["video"]);
    expect(needsSemanticSearch("OAuth", documents, lexicalAgentMatches("OAuth", documents))).toBe(false);
  });

  it("matches terminal names exactly without confusing T1 and T10", () => {
    expect(ids("t1")).toEqual(["notes"]);
    expect(ids("T10")).toEqual(["video"]);
    expect(ids("T11")).toEqual([]);
    expect(needsSemanticSearch("T11", documents, [])).toBe(false);
  });

  it("requires all query terms, in any order, across title and task", () => {
    expect(ids("tokens OAuth")).toEqual(["auth"]);
    expect(ids("OAuth toaster")).toEqual([]);
    expect(ids("recording security")).toEqual([]);
  });

  it("tolerates missing and swapped letters, but leaves short words exact", () => {
    expect(ids("securty")).toEqual(["security"]);
    expect(ids("SECURTY")).toEqual(["security"]);
    expect(ids("reconenct")).toEqual(["auth"]);
    expect(ids("cats", [{ id: "calls", texts: ["Voice calls"] }])).toEqual([]);
    expect(ids("soup", [{ id: "soul", texts: ["Soul feature"] }])).toEqual([]);
  });

  it("finds an unfinished final word and ignores accents and punctuation", () => {
    expect(ids("OAuth recon")).toEqual(["auth"]);
    expect(ids("oauth-reconnect")).toEqual(["auth"]);
    // i18n-allow: multilingual title and typo fixtures.
    expect(ids("Sicherheitsprüfun", [{ id: "audit", texts: ["Sicherheitsprüfung"] }])).toEqual(["audit"]); // i18n-allow
    expect(ids("revision", [{ id: "review", texts: ["Revisión de seguridad"] }])).toEqual(["review"]);
    expect(ids("Straße", [{ id: "street", texts: ["Strasse"] }])).toEqual(["street"]); // i18n-allow
  });

  it("places full titles before exact words, then prefixes and typos", () => {
    expect(ids("security", [
      { id: "typo", texts: ["Securty audit"] },
      { id: "prefix", texts: ["Securityguard"] },
      { id: "word", texts: ["Security audit"] },
      { id: "title", texts: ["Security"] },
    ])).toEqual(["title", "word", "prefix", "typo"]);
  });

  it("does not manufacture hits for empty or unrelated queries", () => {
    expect(ids("   ")).toEqual([]);
    expect(ids("!!!")).toEqual([]);
    expect(ids("banana orchard")).toEqual([]);
    expect(needsSemanticSearch("!!!", documents, [])).toBe(false);
  });

  it("keeps free descriptions and unknown synonyms eligible for semantic matching", () => {
    expect(needsSemanticSearch("Fix expired logins", documents, [])).toBe(true);
    expect(needsSemanticSearch("authentication", documents, [])).toBe(true);
  });

  it("appends semantic results without replacing or duplicating concrete hits", () => {
    const words = lexicalAgentMatches("OAuth", documents);
    const merged = mergeAgentMatches(words, [{ id: "voice", score: 0.99 }, { id: "auth", score: 0.9 }, { id: "voice", score: 0.8 }]);
    expect(merged.map(({ id }) => id)).toEqual(["auth", "voice"]);
    expect(words.map(({ id }) => id)).toEqual(["auth"]);
  });
});
