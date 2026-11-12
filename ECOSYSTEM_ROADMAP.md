# Roadmap — artifacts-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **検証 (Verification)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v0.3.0, Python 3.10+ (+ npm wrapper, MCP server). 11 format adapters (pdf, pptx, docx, xlsx, image, html, svg, csv, markdown, epub, media). 39 test modules, CI on Ubuntu + macOS.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 生成文書の検証 | partial | captions (SRT/VTT/ASS) left to subtitle-skill and qc-skill by decision (step 4) |

## Wiring

Wired: the agent's `document.verify` runs `artifacts-skill/verify` and a leftover placeholder fails QA (step 5, video-production-agent#110, pinned 0.3.0). The contract is in the OS shape since step 1.

## Steps

- [x] **1. OS-shaped contract** — add skill_id, version, contract_version, provides[] keeping the current keys
  - Done when: contract validates against the OS schema in CI
- [x] **2. Rename its receipt** — "Artifact Verification Receipt" (clashes with the agent's ProductionReceipt) ([#64](https://github.com/kajisho5/artifacts-skill/pull/64))
  - Done when: docs and schema updated, CLI alias kept
- [x] **3. Fix #47** — unique evidence dir per run ([#63](https://github.com/kajisho5/artifacts-skill/pull/63))
  - Done when: 50 concurrent runs, no collision
- [x] **4. Captions: adapter or ADR leaving them to subtitle/qc** ([#65](https://github.com/kajisho5/artifacts-skill/pull/65))
  - Done when: written decision
- [x] **5. (agent side) adapter + SkillSpec document_verify** ([video-production-agent#110](https://github.com/kajisho5/video-production-agent/pull/110))
  - Done when: placeholder left in a doc fails QA
- [ ] **6. Event documents against their source** — verify generated cue sheets, run-of-show sheets, speaker lists and lower-third CSVs against the organiser's source list: every name, title and affiliation spelled exactly as the source (no variant kanji, no dropped honorific), every session time consistent. Field review 2026-11-05 (Velisa ROADMAP, third round)
  - なぜ要るか：テロップの氏名・所属の誤字は現場で一番重い事故。生成した一覧を元データと突き合わせて止める
  - Done when: a fixture with one 髙/高 swap and one wrong time gives two findings with their source rows
- [x] **7. Numbers and terms in generated text** — in a generated summary, narration script or description, every number with a unit, every drug name and every person's name must appear in the source (transcript, slides); anything else is reported as unsupported ([#71](https://github.com/kajisho5/artifacts-skill/pull/71))
  - なぜ要るか：医学系の要約やナレーションで、出典のない数値や薬剤名が紛れ込まない
  - Done when: a fixture summary with one invented dosage reports exactly that dosage
- [ ] **8. Credits and licence lines** — the end roll, description or credits document is checked against the licence records of every asset used (sound-design-skill, stock assets, AI voices, fonts): every required attribution is present and worded as the licence asks
  - なぜ要るか：BGM・素材・フォントのクレジット漏れを公開前に見つけられる
  - Done when: a fixture missing one CC BY attribution reports it with the asset

## Boundaries

Media facts duplicate qc / media-analysis.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
