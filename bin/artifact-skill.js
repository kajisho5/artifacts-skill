#!/usr/bin/env node
"use strict";
/**
 * Thin npx wrapper. The real engine is Python (`src/artifact_skill`) —
 * this file only locates a working interpreter/install and execs into it,
 * argv untouched, no shell string interpolation.
 */
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

const pkgRoot = path.resolve(__dirname, "..");
const args = process.argv.slice(2);

function run(cmd, cmdArgs, env) {
  const result = spawnSync(cmd, cmdArgs, { stdio: "inherit", env: env || process.env });
  if (result.error) return null;
  return result.status;
}

// 1. Prefer an already-installed `artifact-skill` console script (pip install
//    put it on PATH — this is the common case once the user has run pip
//    install once, and avoids re-resolving the Python package path).
//
// Guarded by a re-entry env var: right after `npm install -g`/`npx`, PATH's
// "artifact-skill" can resolve back to THIS SAME SCRIPT (npm's own bin shim
// for this package) rather than a real pip-installed console script — with
// no pip install done yet (exactly the "try npx first" scenario this step
// exists for), spawning "artifact-skill" would recurse into this file again
// forever instead of ever reaching the Python fallback below. The env var
// lets a recursive invocation of this same script detect that it's already
// inside this wrapper and skip straight to step 2.
if (!process.env.__ARTIFACT_SKILL_JS_REENTRY) {
  const childEnv = Object.assign({}, process.env, { __ARTIFACT_SKILL_JS_REENTRY: "1" });
  let status = run("artifact-skill", args, childEnv);
  if (status !== null) process.exit(status);
}

// 2. Fall back to running the package in place via `python3 -m`, using
//    PYTHONPATH so it works straight out of a git checkout / npm install
//    without requiring a separate `pip install -e .` first.
for (const py of ["python3", "python"]) {
  const env = Object.assign({}, process.env, {
    PYTHONPATH: [path.join(pkgRoot, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
  });
  const result = spawnSync(py, ["-m", "artifact_skill.cli.main", ...args], { stdio: "inherit", env });
  if (!result.error) process.exit(result.status ?? 1);
}

console.error(
  "artifact-skill: no working Python 3 interpreter found on PATH.\n" +
    "Install Python 3.10+ and its extras, then re-run:\n" +
    `  pip install -e "${pkgRoot}[all]"`
);
process.exit(1);
