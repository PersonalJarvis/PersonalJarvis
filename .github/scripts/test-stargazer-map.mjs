import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const yaml = readFileSync(new URL("../workflows/stargazer-map.yml", import.meta.url), "utf8").replace(/\r\n/g, "\n");
const script = yaml.split("          script: |\n")[1].replace(/^            /gm, "");
const execute = new (Object.getPrototypeOf(async function () {}).constructor)(
  // github-script v9 injects getOctokit; declaring it in the script must fail.
  "require", "github", "context", "core", "getOctokit", script,
);

const snapshot = {
  repo: "PersonalJarvis/PersonalJarvis", count: 1, forks: 0, placed: 1, stated: 1,
  generatedAt: "2026-09-01T00:00:00Z", login: "must-not-publish",
  clusters: [{ lat: 52, lon: 13, label: "Berlin", country: "Germany", count: 1,
    profile: "must-not-publish" }],
};

async function run({ exists = false, fail = false } = {}) {
  const writes = [];
  const github = { rest: {
    git: {
      async getRef({ ref }) {
        if (ref === "heads/stargazer-map" && !exists) throw { status: fail ? 403 : 404 };
        return { data: { object: { sha: "main-sha" } } };
      },
      async createRef(args) { writes.push({ kind: "branch", ...args }); },
    },
    repos: {
      async getContent() {
        if (!exists) throw { status: 404 };
        return { data: { sha: "old-file-sha" } };
      },
      async createOrUpdateFileContents(args) { writes.push({ kind: "file", ...args }); },
    },
  } };
  await execute((specifier) => {
    assert.equal(specifier, "node:fs", "the publisher only imports a Node built-in");
    return { readFileSync: () => JSON.stringify(snapshot) };
  }, github, { repo: { owner: "PersonalJarvis", repo: "PersonalJarvis" } },
  { info() {} }, () => { throw new Error("no additional authenticated clients expected"); });
  return writes;
}

test("bootstrap creates the data branch and publishes only aggregate fields", async () => {
  const writes = await run();
  assert.equal(writes[0].ref, "refs/heads/stargazer-map");
  assert.equal(writes[0].sha, "main-sha");
  const data = JSON.parse(Buffer.from(writes[1].content, "base64"));
  assert.equal(data.login, undefined);
  assert.equal(data.clusters[0].profile, undefined);
  assert.equal(data.placed, 1);
  assert.equal(writes[1].branch, "stargazer-map");
});

test("existing branch is updated using the previous file SHA without touching main", async () => {
  const writes = await run({ exists: true });
  assert.equal(writes.length, 1);
  assert.equal(writes[0].sha, "old-file-sha");
  assert.equal(writes[0].branch, "stargazer-map");
});

test("permission failures abort publication", async () => {
  await assert.rejects(run({ fail: true }), (error) => error.status === 403);
});

test("star and reconciliation triggers use a pinned collector without a personal token", () => {
  assert.match(yaml, /watch:\s+types: \[started\]/);
  assert.match(yaml, /cron: "23 \* \* \* \*"/);
  assert.match(yaml, /ref: [a-f0-9]{40}/);
  assert.match(yaml, /GITHUB_TOKEN: \$\{\{ github.token \}\}/);
  assert.doesNotMatch(yaml, /STARGAZERS_TOKEN|pull_request_target/);
});

test("all actions are immutable and Node setup cannot create a privileged cache", () => {
  const actions = [...yaml.matchAll(/uses: (actions\/[^\s]+)(?:[^\n]*)/g)];
  assert.equal(actions.length, 5);
  for (const [, action] of actions) {
    assert.match(action, /^actions\/[^@]+@[a-f0-9]{40}$/);
  }
  const setups = yaml.split(/- uses: actions\/setup-node@/).slice(1);
  assert.equal(setups.length, 2);
  for (const setup of setups) {
    assert.match(setup.split(/\n\s*- (?:uses|name|run):/)[0], /package-manager-cache: false/);
  }
});

test("the required CI gate exercises the publisher without running the collector", () => {
  const ci = readFileSync(new URL("../workflows/ci.yml", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  const gates = ci.split("  gates:\n")[1]?.split(/^  [a-z][\w-]*:\s*$/m)[0];
  assert.ok(gates, "the blocking static gates job exists");
  assert.match(gates, /node --test \.github\/scripts\/test-stargazer-map\.mjs/);
  assert.doesNotMatch(gates, /fetch-stargazers|continue-on-error/);
});
