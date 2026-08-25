# skill-merge project boundary

This directory is an independent project snapshot created from
`C:\Users\LENOVO\Documents\seconddevPPT`. It is not a Git worktree and has no
runtime dependency on the source project or the `only_text` project.

## Runtime model contract

Production calls only DashScope/Qwen services:

- `qwen3.5-ocr`: authoritative text content and text boxes.
- `qwen3-vl-plus`: semantic regions, object routing, local candidate review and visual QA.
- `qwen-image-2.0-pro`: bounded complex asset/background repair only.

OpenAI, ChatGPT and GPT model identifiers are forbidden by the runtime policy.
Codex is a development tool, not a production dependency.

## Fidelity contract

The strict path never uses template substitution or a visible full-slide source
image. Text, cards, rules, connectors, ordinary arrows, tables and simple charts
are native PowerPoint objects. Complex visual objects are bounded, independently
movable reviewed SVG/PNG assets. Every page owns one `manifest.json` and cannot
be recorded or finalized until source/render, text, non-text, background and
move/delete editability checks pass.

## Local worker contract

The first deployment targets this Windows workstation. PPTX compilation is
performed by the application; Microsoft PowerPoint is used by the dedicated
desktop QA worker to render the final deck. The PowerPoint worker must use a
dedicated user profile, one job at a time, a timeout/watchdog, disabled macros,
and generated `.pptx` inputs only.

## Snapshot exclusions

The initial snapshot excludes `.git`, `.next`, `.pnpm-store`, `node_modules`,
`output`, logs and TypeScript build caches. `.env.local` was copied by explicit
user authorization but is ignored by Git and must never be committed or exposed
to the browser.
