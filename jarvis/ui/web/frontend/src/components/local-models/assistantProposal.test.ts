import { describe, expect, it } from "vitest";

import {
  confirmationMessage,
  matchesConfirmedStep,
  proposalFromText,
  proposalHash,
  type Proposal,
} from "./assistantProposal";

const BLOCK = `{
  "version": 1,
  "steps": [
    {"id": "s1", "kind": "pull", "model": "qwen3.5:8b", "size_gb": 5.2, "fit": "fits in VRAM", "proven": true, "label": "Download Qwen 3.5 8B"},
    {"id": "s2", "kind": "set_role", "role": "chat", "model": "qwen3.5:8b", "proven": "proven", "label": "Use it for Chat"},
    {"id": "s3", "kind": "pull", "model": "nomic-embed-text", "proven": "new_little_tested", "label": "Download the embedding model"},
    {"id": "s4", "kind": "test", "label": "Test the setup"}
  ],
  "brain_switch": {"provider": "ollama", "why": "Everything runs locally now."},
  "notes": ["Nothing is deleted."]
}`;

describe("proposalFromText", () => {
  it("finds a fenced jarvis-proposal block and validates it", () => {
    const p = proposalFromText(`Here is my plan.\n\n\`\`\`jarvis-proposal\n${BLOCK}\n\`\`\`\n`);
    expect(p).not.toBeNull();
    expect(p!.steps.map((s) => s.id)).toEqual(["s1", "s2", "s3", "s4"]);
    expect(p!.steps[0]).toMatchObject({ kind: "pull", model: "qwen3.5:8b", size_gb: 5.2, proven: "proven" });
    expect(p!.steps[2].proven).toBe("new_little_tested");
    expect(p!.steps[3].proven).toBeNull();
    expect(p!.brain_switch).toEqual({ provider: "ollama", why: "Everything runs locally now." });
    expect(p!.notes).toEqual(["Nothing is deleted."]);
  });

  it("takes the LAST block when the assistant corrected itself", () => {
    const first = BLOCK.replace('"id": "s1"', '"id": "old"');
    const text = `\`\`\`jarvis-proposal\n${first}\n\`\`\`\nActually, a better plan:\n\`\`\`jarvis-proposal\n${BLOCK}\n\`\`\``;
    expect(proposalFromText(text)!.steps[0].id).toBe("s1");
  });

  it("tolerates trailing prose and a missing closing fence", () => {
    const text = `\`\`\`jarvis-proposal\n${BLOCK}\nShall I go ahead?`;
    expect(proposalFromText(text)!.steps).toHaveLength(4);
  });

  it("returns null for prose, other fences, malformed JSON, or unknown kinds only", () => {
    expect(proposalFromText("No plan here.")).toBeNull();
    expect(proposalFromText("```json\n{\"version\":1,\"steps\":[]}\n```")).toBeNull();
    expect(proposalFromText("```jarvis-proposal\n{not json\n```")).toBeNull();
    expect(
      proposalFromText('```jarvis-proposal\n{"version":1,"steps":[{"kind":"delete","model":"x"}]}\n```'),
    ).toBeNull();
  });

  it("drops unknown step kinds but keeps the known ones, and de-duplicates ids", () => {
    const p = proposalFromText(
      '```jarvis-proposal\n{"version":1,"steps":[{"id":"a","kind":"pull","model":"m"},{"id":"a","kind":"pull","model":"n"},{"id":"b","kind":"delete"}]}\n```',
    );
    expect(p!.steps.map((s) => s.id)).toEqual(["a", "a-2"]);
  });
});

describe("proposalHash", () => {
  it("is short, stable, and independent of key order and notes", () => {
    const a = proposalFromText(`\`\`\`jarvis-proposal\n${BLOCK}\n\`\`\``)!;
    const reordered: Proposal = {
      ...a,
      notes: [],
      steps: a.steps.map((s) => Object.fromEntries(Object.entries(s).reverse()) as typeof s),
    };
    expect(proposalHash(a)).toMatch(/^[0-9a-f]{8}$/);
    expect(proposalHash(reordered)).toBe(proposalHash(a));
    const changed: Proposal = { ...a, steps: [{ ...a.steps[0], model: "other" }, ...a.steps.slice(1)] };
    expect(proposalHash(changed)).not.toBe(proposalHash(a));
  });

  it("names the chosen steps and the hash in the confirmation", () => {
    const p = proposalFromText(`\`\`\`jarvis-proposal\n${BLOCK}\n\`\`\``)!;
    expect(confirmationMessage(p, [p.steps[0], p.steps[3]])).toBe(
      `Execute steps: s1, s4 (proposal v1, hash ${proposalHash(p)})`,
    );
  });
});

describe("matchesConfirmedStep", () => {
  const p = proposalFromText(`\`\`\`jarvis-proposal\n${BLOCK}\n\`\`\``)!;
  const confirmed = p.steps;

  it("answers pulls and role writes for exactly the confirmed model", () => {
    expect(matchesConfirmedStep({ name: "lm_pull", input: { model: "qwen3.5:8b" } }, confirmed)).toBe(true);
    // Ollama's implicit tag is the same model.
    expect(matchesConfirmedStep({ name: "lm_pull", input: { model: "nomic-embed-text:latest" } }, confirmed)).toBe(true);
    expect(matchesConfirmedStep({ name: "lm_pull", input: { model: "llama4:70b" } }, confirmed)).toBe(false);
    expect(
      matchesConfirmedStep({ name: "lm_set_role", input: { role: "chat", model: "qwen3.5:8b" } }, confirmed),
    ).toBe(true);
    expect(
      matchesConfirmedStep({ name: "lm_set_role", input: { role: "deep", model: "qwen3.5:8b" } }, confirmed),
    ).toBe(false);
  });

  it("covers the server start, the test, and nothing outside the plan", () => {
    expect(matchesConfirmedStep({ name: "lm_start_server", input: {} }, confirmed)).toBe(true);
    expect(matchesConfirmedStep({ name: "lm_test_plan", input: {} }, confirmed)).toBe(true);
    expect(matchesConfirmedStep({ name: "lm_install_ollama", input: {} }, confirmed)).toBe(false);
    expect(matchesConfirmedStep({ name: "lm_apply_voice_stack", input: {} }, confirmed)).toBe(false);
    expect(matchesConfirmedStep({ name: "lm_set_model_options", input: { model: "qwen3.5:8b" } }, confirmed)).toBe(false);
    expect(matchesConfirmedStep({ name: "lm_delete", input: { model: "qwen3.5:8b" } }, confirmed)).toBe(false);
    expect(matchesConfirmedStep({ name: "lm_pull", input: { model: "qwen3.5:8b" } }, [])).toBe(false);
  });

  it("only the steps left ticked count", () => {
    const withoutEmbed = confirmed.filter((s) => s.id !== "s3");
    expect(matchesConfirmedStep({ name: "lm_pull", input: { model: "nomic-embed-text" } }, withoutEmbed)).toBe(false);
  });
});
