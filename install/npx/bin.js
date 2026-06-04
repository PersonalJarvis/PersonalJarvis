#!/usr/bin/env node
"use strict";

/*
 * `npx personal-jarvis` — cross-platform bootstrap.
 *
 * This is a thin launcher: it detects the OS and hands off to the canonical
 * Stage-1 installer (install/install.ps1 on Windows, install/install.sh on
 * macOS/Linux) fetched from the repo — exactly the same path as the README
 * one-liners. Extra args (e.g. `--headless`, `--no-launch`) are forwarded to
 * the underlying installer.
 *
 *   npx personal-jarvis                # full desktop app
 *   npx personal-jarvis --headless     # server / no-GUI install
 *
 * No dependencies — pure Node + the platform's own shell.
 */

const { spawnSync } = require("node:child_process");
const os = require("node:os");

const REPO = process.env.JARVIS_INSTALL_REPO || "PersonalJarvis/PersonalJarvis";
const REF = process.env.JARVIS_INSTALL_REF || "main";
const BASE = `https://raw.githubusercontent.com/${REPO}/${REF}/install`;
const PS_URL = `${BASE}/install.ps1`;
const SH_URL = `${BASE}/install.sh`;

const extra = process.argv.slice(2);

function exit(code) {
  process.exit(code === null || code === undefined ? 1 : code);
}

function run(cmd, args) {
  console.log(`\n[personal-jarvis] OS=${os.platform()} → launching installer…\n`);
  const res = spawnSync(cmd, args, { stdio: "inherit" });
  if (res.error) {
    console.error(`[personal-jarvis] failed to launch ${cmd}: ${res.error.message}`);
    exit(1);
  }
  exit(res.status);
}

if (os.platform() === "win32") {
  // Download the script and invoke it as a scriptblock so extra args are
  // forwarded (plain `irm | iex` cannot take parameters).
  const argStr = extra.join(" ");
  const ps = `& ([scriptblock]::Create((Invoke-RestMethod '${PS_URL}'))) ${argStr}`.trim();
  run("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]);
} else {
  // curl … | bash -s -- <args>
  const argStr = extra.map((a) => `'${a.replace(/'/g, "'\\''")}'`).join(" ");
  const sh = `curl -fsSL '${SH_URL}' | bash -s -- ${argStr}`.trim();
  run("bash", ["-c", sh]);
}
