/**
 * The marketplace install commands — the lines that add a listing.
 *
 * Both stores show the same lines: this file inside the app, and the website's
 * `src/lib/install-standard.ts` on personaljarvis.ai. A visitor who reads a
 * command on the storefront and a user who reads it in the Plugins view have
 * to see the same string, or one of the two is advertising a command that does
 * not work. `installStandard.test.ts` pins the exact output, so a rename on
 * either side fails a test instead of going live unnoticed.
 *
 * TWO standards, not one:
 *
 *  - **Personal Jarvis** — `jarvis marketplace install <name>` installs into
 *    this app. Available for every listing, because the registry is ours.
 *  - **skills.sh** — `npx skills add <owner>/<repo>` is the open agent-skills
 *    installer (Vercel Labs; see https://skills.sh). It reads a `SKILL.md`
 *    straight out of a GitHub repository and drops it into whichever agent is
 *    configured locally: Claude Code, Cursor, Codex, Copilot, and the rest.
 *    Offered for SKILLS only — a plugin carries an MCP server and a connect
 *    flow, which that installer knows nothing about.
 *
 * The skills.sh line is derived, never stored: a published entry already
 * carries the two URLs it needs (`raw_url`, `source_url`). The derivation is
 * exact when `raw_url` points at the file (the folder holding `SKILL.md` IS
 * the `--skill` argument, and a `SKILL.md` at the repository root means no
 * `--skill` at all). With only a repository URL the entry name is used, which
 * is what the registry publishes as `skills/<name>/SKILL.md`.
 *
 * The commands themselves are identical on every operating system. Only the
 * shell prompt drawn in front of them differs, which is what `shellPrompt`
 * answers.
 */

export type EntryKind = "plugin" | "skill";

/** Which install standard a line belongs to. */
export type InstallStandardId = "jarvis" | "skills";

/** One copyable install line. */
export interface InstallCommand {
  id: InstallStandardId;
  /** Tab label — the name of the standard, not of the entry. */
  label: string;
  /** The line to show and copy. */
  command: string;
  /** One line under the terminal saying where this command works. */
  note: string;
}

/** The install surfaces for one listing. */
export interface InstallBlock {
  /** For a machine that already has the CLI — what the app itself runs. */
  cli: string;
  /** The zero-install equivalent of `npx`, for a machine without the CLI. */
  runner: string;
  /** Plain language to paste at a coding agent, which runs the same command. */
  prompt: string;
  /** Every standard this listing can be installed with, Jarvis first. */
  commands: InstallCommand[];
}

/** The published URLs of one entry, as the community index carries them. */
export interface EntrySource {
  /** The listing's page — for a GitHub-published skill, its repository. */
  sourceUrl?: string | null;
  /** Direct download of the `SKILL.md`. */
  rawUrl?: string | null;
}

/** A repository plus the skill folder inside it, ready for `npx skills add`. */
export interface SkillsShTarget {
  /** `owner/repo`. */
  repo: string;
  /** The folder holding the `SKILL.md`, or null when it sits at the root. */
  skill: string | null;
}

/**
 * The Agent Plugins name rule, mirroring
 * `agent_plugins_loader.validate_spec_name` and
 * `community_source._SKILL_NAME_RE`. Names matching it are `[a-z0-9.-]` only,
 * so they survive a shell line unquoted — which is what makes a copy-paste
 * one-liner possible at all.
 */
const NAME_RE = /^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$/;

/**
 * What a GitHub owner, repository, or folder segment may contain.
 *
 * Deliberately its own rule rather than `NAME_RE`: GitHub allows uppercase and
 * underscores (`PersonalJarvis/marketplace`), which our registry names do not.
 * It stays strict about everything a shell would interpret, because this value
 * is pasted into a command line unquoted.
 */
const GITHUB_SEGMENT_RE = /^[A-Za-z0-9._-]{1,100}$/;

/** A path segment that is a git ref prefix rather than part of the file path. */
function stripRefPrefix(segments: string[]): string[] {
  // raw.githubusercontent.com serves both `<owner>/<repo>/<ref>/<path>` and the
  // longer `<owner>/<repo>/refs/heads/<ref>/<path>`. Counting the ref as one
  // segment in the second shape would make the branch name look like the skill
  // folder ("main"), so the longer form is consumed as the three parts it is.
  if (segments[0] === "refs" && (segments[1] === "heads" || segments[1] === "tags")) {
    return segments.slice(3);
  }
  return segments.slice(1);
}

function safeSegment(value: string | undefined): string | null {
  if (!value) return null;
  if (value === "." || value === "..") return null;
  return GITHUB_SEGMENT_RE.test(value) ? value : null;
}

/**
 * The `npx skills add` target for one entry, or null when there is none.
 *
 * Null is the honest answer for anything not published from GitHub: that
 * installer resolves `owner/repo` against github.com and nothing else, so a
 * skill hosted elsewhere simply has no skills.sh line.
 */
export function skillsShTarget(
  name: string,
  source: EntrySource = {},
): SkillsShTarget | null {
  const raw = parseRawUrl(source.rawUrl);
  if (raw) return raw;
  const repo = parseRepoUrl(source.sourceUrl);
  if (!repo) return null;
  // Only the repository is known. The registry publishes every skill as
  // `skills/<name>/SKILL.md`, so the entry name is the folder — and a wrong
  // guess costs a listing from the installer, not a broken install.
  return { repo, skill: safeSegment(name) };
}

/** `https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>/SKILL.md` */
function parseRawUrl(rawUrl: string | null | undefined): SkillsShTarget | null {
  const url = parseHttpsUrl(rawUrl);
  if (!url || url.hostname !== "raw.githubusercontent.com") return null;
  const parts = url.pathname.split("/").filter(Boolean);
  const owner = safeSegment(parts[0]);
  const repo = safeSegment(stripGitSuffix(parts[1]));
  if (!owner || !repo) return null;

  const path = stripRefPrefix(parts.slice(2));
  const file = path[path.length - 1];
  if (file !== "SKILL.md") return null;
  // The folder holding the file is the `--skill` argument; a SKILL.md sitting
  // at the repository root has none, and `npx skills add owner/repo` is then
  // the whole command.
  const folder = path.length >= 2 ? safeSegment(path[path.length - 2]) : null;
  return { repo: `${owner}/${repo}`, skill: folder };
}

/** `https://github.com/<owner>/<repo>` (with or without a `/tree/...` tail). */
function parseRepoUrl(sourceUrl: string | null | undefined): string | null {
  const url = parseHttpsUrl(sourceUrl);
  if (!url) return null;
  if (url.hostname !== "github.com" && url.hostname !== "www.github.com") {
    return null;
  }
  const parts = url.pathname.split("/").filter(Boolean);
  const owner = safeSegment(parts[0]);
  const repo = safeSegment(stripGitSuffix(parts[1]));
  if (!owner || !repo) return null;
  return `${owner}/${repo}`;
}

function stripGitSuffix(value: string | undefined): string | undefined {
  return value?.endsWith(".git") ? value.slice(0, -4) : value;
}

function parseHttpsUrl(value: string | null | undefined): URL | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

/** The `npx skills add …` line for a resolved target. */
export function skillsShCommand(target: SkillsShTarget): string {
  const base = `npx skills add ${target.repo}`;
  return target.skill ? `${base} --skill ${target.skill}` : base;
}

/**
 * The install strings for one community entry.
 *
 * Returns null when the name would not survive a shell line unquoted. Both
 * index validators already enforce the same rule, so a miss here means the
 * entry is invalid anyway — showing no command beats showing a quoted, and
 * therefore alarming, one.
 */
export function installBlock(
  name: string,
  kind: EntryKind,
  source: EntrySource = {},
): InstallBlock | null {
  if (!NAME_RE.test(name) || name.includes("..")) return null;
  const cli = `jarvis marketplace install ${name}`;
  const commands: InstallCommand[] = [
    {
      id: "jarvis",
      label: "Personal Jarvis",
      command: cli,
      note: "Runs in any terminal while Personal Jarvis is running.",
    },
  ];

  // A plugin is a connector with an MCP server and a sign-in flow; `npx skills`
  // installs instruction files. Offering it there would advertise an install
  // that cannot work.
  if (kind === "skill") {
    const target = skillsShTarget(name, source);
    if (target) {
      commands.push({
        id: "skills",
        label: "skills.sh",
        command: skillsShCommand(target),
        note: "Installs the same skill into Claude Code, Cursor, Codex, and other agents.",
      });
    }
  }

  return {
    cli,
    runner: `uvx --from personal-jarvis ${cli}`,
    prompt: `Install the "${name}" ${kind} from the community marketplace.`,
    commands,
  };
}

/**
 * The shell prompt to draw in front of the command on THIS machine.
 *
 * The app runs where the command would run, so it knows the answer and does
 * not ask — unlike the website, which has to offer a switch.
 */
export function shellPrompt(): string {
  if (typeof navigator === "undefined") return "$";
  const ua = navigator.userAgent;
  return /windows/i.test(ua) ? "PS>" : "$";
}
