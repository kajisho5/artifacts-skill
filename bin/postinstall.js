#!/usr/bin/env node
"use strict";
/**
 * Deliberately does NOT `pip install` anything automatically — installing
 * across package-manager boundaries without being asked is exactly the
 * kind of implicit, hard-to-audit action this project's own security
 * principles (docs/security.md) argue against. It only tells the user what
 * to run and, if a Python 3 interpreter is already on PATH, runs `doctor`
 * so they see real capability status immediately.
 */
const { spawnSync } = require("node:child_process");
const path = require("node:path");

const pkgRoot = path.resolve(__dirname, "..");

function findPython() {
  for (const py of ["python3", "python"]) {
    const probe = spawnSync(py, ["--version"], { stdio: "ignore" });
    if (!probe.error && probe.status === 0) return py;
  }
  return null;
}

const py = findPython();
console.log("\nartifact-skill: Python engine detected at install time as:", py || "NOT FOUND");
console.log(`To enable the PDF adapter (required for inspect/plan/execute/render/verify/receipt):`);
console.log(`  pip install -e "${pkgRoot}[pdf]"`);
console.log("Then check status with: npx artifact-skill doctor\n");

if (py) {
  const env = Object.assign({}, process.env, {
    PYTHONPATH: [path.join(pkgRoot, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
  });
  spawnSync(py, ["-m", "artifact_skill.cli.main", "doctor"], { stdio: "inherit", env });
}
