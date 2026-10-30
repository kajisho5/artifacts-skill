# Roadmap — artifacts-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **検証 (Verification)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v0.3.0, Python 3.10+ (+ npm wrapper, MCP server). 11 format adapters (pdf, pptx, docx, xlsx, image, html, svg, csv, markdown, epub, media). 39 test modules, CI on Ubuntu + macOS.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 生成文書の検証 | partial | no SRT/VTT/ASS adapter for captions |

## Wiring

Not reachable from the agent. Contract does not fit the OS schema (tool_name/tools[], no skill_id/provides).

## Steps

- [x] **1. OS-shaped contract** — add skill_id, version, contract_version, provides[] keeping the current keys
  - Done when: contract validates against the OS schema in CI
- [x] **2. Rename its receipt** — "Artifact Verification Receipt" (clashes with the agent's ProductionReceipt) ([#64](https://github.com/kajisho5/artifacts-skill/pull/64))
  - Done when: docs and schema updated, CLI alias kept
- [x] **3. Fix #47** — unique evidence dir per run ([#63](https://github.com/kajisho5/artifacts-skill/pull/63))
  - Done when: 50 concurrent runs, no collision
- [ ] **4. Captions: adapter or ADR leaving them to subtitle/qc**
  - Done when: written decision
- [ ] **5. (agent side) adapter + SkillSpec document_verify**
  - Done when: placeholder left in a doc fails QA

## Boundaries

Media facts duplicate qc / media-analysis.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
