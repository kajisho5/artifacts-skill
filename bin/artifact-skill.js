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

function run(cmd, cmdArgs) {
  const result = spawnSync(cmd, cmdArgs, { stdio: "inherit" });
  if (result.error) return null;
  return result.status;
}

// 1. Prefer an already-installed `artifact-skill` console script (pip install
//    put it on PATH — this is the common case once the user has run pip
//    install once, and avoids re-resolving the Python package path).
let status = run("artifact-skill", args);
if (status !== null) process.exit(status);

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
    "Install Python 3.10+ and its PDF extras, then re-run:\n" +
    `  pip install -e "${pkgRoot}[pdf]"`
);
process.exit(1);
