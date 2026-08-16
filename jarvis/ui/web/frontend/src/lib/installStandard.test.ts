import { describe, expect, it } from "vitest";

import {
  installBlock,
  shellPrompt,
  skillsShCommand,
  skillsShTarget,
} from "@/lib/installStandard";

// The storefront on personaljarvis.ai computes these same strings in
// `src/lib/install-standard.ts` and pins them in its own check. Both surfaces
// have to advertise a command that actually works, so the expected values
// below are the contract between them: change one, change both.
describe("the marketplace install standard", () => {
  it("builds the three surfaces for a skill", () => {
    const block = installBlock("three-point-check", "skill");
    expect(block).toMatchObject({
      cli: "jarvis marketplace install three-point-check",
      runner:
        "uvx --from personal-jarvis jarvis marketplace install three-point-check",
      prompt:
        'Install the "three-point-check" skill from the community marketplace.',
    });
  });

  it("carries the kind into the agent prompt", () => {
    expect(installBlock("todo-fox", "plugin")?.prompt).toBe(
      'Install the "todo-fox" plugin from the community marketplace.',
    );
  });

  it("accepts the dots and digits a registry name may carry", () => {
    expect(installBlock("web.search2", "plugin")?.cli).toBe(
      "jarvis marketplace install web.search2",
    );
  });

  // A name that would need quoting is a name no store should print: the index
  // validators reject it anyway, so no command beats a scary one.
  it.each([
    ["a space", "todo fox"],
    ["a shell metacharacter", "todo;rm"],
    ["a path escape", "../etc"],
    ["a leading dash", "-todo"],
    ["an underscore", "todo_fox"],
    ["an empty name", ""],
  ])("refuses %s", (_label, name) => {
    expect(installBlock(name, "skill")).toBeNull();
  });

  it("draws the prompt of the machine it runs on", () => {
    // jsdom reports a Windows-free user agent, so this is the unix branch.
    expect(["$", "PS>"]).toContain(shellPrompt());
  });
});

// The second standard: `npx skills add` (skills.sh) installs a SKILL.md into
// whichever agent is configured locally. It resolves `owner/repo` against
// github.com and nothing else, so the derivation has to be exact about what it
// can and cannot answer.
describe("the skills.sh target", () => {
  it("reads owner, repo, and folder out of a raw file URL", () => {
    expect(
      skillsShTarget("three-point-check", {
        rawUrl:
          "https://raw.githubusercontent.com/PersonalJarvis/marketplace/main/skills/three-point-check/SKILL.md",
      }),
    ).toEqual({ repo: "PersonalJarvis/marketplace", skill: "three-point-check" });
  });

  it("does not mistake a refs/heads branch for the skill folder", () => {
    expect(
      skillsShTarget("solo", {
        rawUrl:
          "https://raw.githubusercontent.com/octocat/one-skill/refs/heads/main/SKILL.md",
      }),
    ).toEqual({ repo: "octocat/one-skill", skill: null });
  });

  it("has no --skill argument for a SKILL.md at the repository root", () => {
    const target = skillsShTarget("solo", {
      rawUrl: "https://raw.githubusercontent.com/octocat/one-skill/main/SKILL.md",
    });
    expect(skillsShCommand(target!)).toBe("npx skills add octocat/one-skill");
  });

  it("falls back to the entry name when only the repository is known", () => {
    expect(
      skillsShTarget("three-point-check", {
        sourceUrl: "https://github.com/PersonalJarvis/marketplace",
      }),
    ).toEqual({ repo: "PersonalJarvis/marketplace", skill: "three-point-check" });
  });

  it("prefers the raw URL over the repository URL", () => {
    // The raw URL names the actual file, so it wins over the guess: the folder
    // there is what `--skill` has to match.
    expect(
      skillsShTarget("listed-name", {
        sourceUrl: "https://github.com/octocat/skills",
        rawUrl:
          "https://raw.githubusercontent.com/octocat/skills/main/packs/real-folder/SKILL.md",
      }),
    ).toEqual({ repo: "octocat/skills", skill: "real-folder" });
  });

  it("tolerates a .git suffix and a /tree/ tail", () => {
    expect(
      skillsShTarget("pack", {
        sourceUrl: "https://github.com/octocat/skills.git/tree/main/pack",
      })?.repo,
    ).toBe("octocat/skills");
  });

  // Anything not on GitHub simply has no skills.sh line — that installer knows
  // no other host, so inventing one would advertise a broken command.
  it.each([
    ["a non-GitHub host", { sourceUrl: "https://gitlab.com/octocat/skills" }],
    ["plain http", { sourceUrl: "http://github.com/octocat/skills" }],
    ["a raw URL that is not a SKILL.md", {
      rawUrl: "https://raw.githubusercontent.com/octocat/skills/main/README.md",
    }],
    ["no URLs at all", {}],
  ])("returns nothing for %s", (_label, source) => {
    expect(skillsShTarget("pack", source)).toBeNull();
  });

  it("refuses a repository segment that would not survive a shell line", () => {
    expect(
      skillsShTarget("pack", {
        sourceUrl: "https://github.com/octo%20cat/skills",
      }),
    ).toBeNull();
  });
});

describe("the commands a listing offers", () => {
  it("gives a GitHub-published skill both standards, Jarvis first", () => {
    const block = installBlock("three-point-check", "skill", {
      sourceUrl: "https://github.com/PersonalJarvis/marketplace",
      rawUrl:
        "https://raw.githubusercontent.com/PersonalJarvis/marketplace/main/skills/three-point-check/SKILL.md",
    });
    expect(block?.commands.map((c) => c.command)).toEqual([
      "jarvis marketplace install three-point-check",
      "npx skills add PersonalJarvis/marketplace --skill three-point-check",
    ]);
  });

  // A plugin carries an MCP server and a sign-in flow; `npx skills` installs
  // instruction files. Offering it there would advertise an install that
  // cannot work.
  it("never offers skills.sh for a plugin", () => {
    const block = installBlock("todo-fox", "plugin", {
      sourceUrl: "https://github.com/octocat/todo-fox",
    });
    expect(block?.commands.map((c) => c.id)).toEqual(["jarvis"]);
  });

  it("falls back to the Jarvis command alone when nothing is on GitHub", () => {
    const block = installBlock("three-point-check", "skill", {
      sourceUrl: "https://example.com/skills/three-point-check",
    });
    expect(block?.commands.map((c) => c.id)).toEqual(["jarvis"]);
  });
});
