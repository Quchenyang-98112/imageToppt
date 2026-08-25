# skill-merge architecture

## Current stage

The application has a durable local job envelope:

```text
POST /api/jobs (multipart PNG/JPG)
  -> jobs/<id>/input
  -> per-page analysis.json
  -> per-page manifest.json
  -> BG_CLEAN candidate and foreground mask
  -> needs_review until independent QA passes
```

The browser never receives model keys. The server invokes the existing Qwen
OCR/VL routes and the deterministic background builder. Each page remains
independently retryable; the final PPTX download endpoint refuses any job that
has no candidate deck. A candidate deck may be downloaded before `completed`,
but its quality status is exposed separately and it is never labelled as the
published artifact.

## Next strict gates

1. Replace the current whole-slide structure pass with detector-grounded local
   candidate verification and semantic-region evidence.
2. Resolve each non-text candidate through the copied image repository first,
   then Qwen Image for bounded unmatched assets.
3. Compile one PPTX from accepted page manifests with stable object IDs and
   native simple objects.
4. Render with `tools/powerpoint_render.ps1`, run background/text/non-text and
   move/delete checks, and only then mark a page `passed`.
5. Assemble pages in upload order and publish atomically.

## Local Windows Worker

The first runtime is intentionally a Windows desktop worker with the licensed
Microsoft PowerPoint installation. Node/PptxGenJS writes the candidate deck;
`tools/powerpoint_render.ps1` opens it in PowerPoint through COM and exports
PNG previews for QA. This is a local worker dependency, not a browser
dependency. The future intranet deployment can put the same Next.js API and
worker on this machine and expose only the HTTP upload/status/download surface.

## Publish evidence

`POST /api/jobs/:jobId/approve` is a hard, evidence-bearing gate. It requires
one manifest per page, verifies the uploaded source hash and server-side OCR
count, runs the deterministic move/delete/foreground-only audit, and requires
passing Qwen-VL and BG_CLEAN reports. It then copies the candidate to
`final/editable-deck.pptx` atomically from the application's point of view and
marks the job completed. There is no “force complete” flag.

## No template substitution

There is no template selector in this project. The source pixel coordinate and
measured geometry drive every page independently. Repeated visual components
are used as cross-checks and gallery search keys, never as replacement layouts.
