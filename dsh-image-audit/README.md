# dsh-image-audit

Native DSH tool plugin: batch **VLM audit** of image candidates with automatic
fallback to pure-text scores.

Complements `dsh-bing-search`'s `mcp__web__search_images` (text ranking).
The audit runs inside the harness, so it uses DSH's own model routing and
attachment service — no API keys are managed by the plugin.

**Primary tool: `search_and_audit_images`** — one call does everything: it runs
the MCP image search internally (`ctx.tools.execute`), detects a vision route,
audits the top candidates in a single batch request, and returns the final
ranking merged from text and VLM scores. When no vision model exists the same
call returns the pure-text ranking (`audit: "text_only"`) — the agent never has
to chain two tools.

## How it works

1. **Vision-route detection (native)** — the current session route is tried
   first, then the configured `routes`; each is validated with
   `ctx.llm.resolveModelInfo` and must declare `inputModalities` including
   `image`. No vision route → the tool returns `status: "unavailable"` and the
   caller falls back to pure-text scores.
2. **One batch call** — all candidates (default up to 16, recommend 8) are
   downloaded, persisted through the attachment service, and sent as
   ImageBlocks in a **single** `ctx.llm.stream` request.
3. **Structured verdict** — the model returns a JSON array of
   `{index, accept, score, reasons}`; scores are merged with the text score
   (`final = textWeight*text + (1-textWeight)*vlm`), and a VLM score below
   `vetoBelow` vetoes the candidate (`final = min(text, vlm)`).

`audit_images` remains available to audit caller-supplied candidates
(e.g. results from other sources).

## Install

```bash
# 1. copy the package into the DSH profile workspace
cp -R dsh-image-audit ~/.dsh/profiles/node_modules/

# 2. add to ~/.dsh/profiles/web/cordis.patch.yml (see dsh-image-audit.cordis.yml)
# 3. restart DSH (or rely on profile HMR) — the model then sees `audit_images`
```

## Config

| key | default | meaning |
|---|---|---|
| `routes` | `[]` | vision route candidates `[{provider, model}]`; the current session route is tried first |
| `maxImages` | `16` | max images per audit call (recommend 8) |
| `maxOutputTokens` | `2000` | audit answer token cap |
| `timeoutMs` | `45000` | end-to-end vision-call timeout |
| `vetoBelow` | `30` | VLM scores below this veto the candidate |
| `textWeight` | `0.5` | weight of the pure-text score in the final blend |

Example route for a local OpenAI-compatible vision endpoint:

```yaml
- insert:
    - id: image-audit
      name: dsh-image-audit
      config:
        routes:
          - provider: local-4002
            model: mimo-v2.5-free
        maxImages: 8
```

## Test

```bash
node --test dsh-image-audit/test/
```
