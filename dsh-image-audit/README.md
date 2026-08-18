# dsh-image-audit

Native DSH tool plugin: batch **VLM audit** of image candidates with automatic
fallback to pure-text scores.

Complements `dsh-bing-search`'s `mcp__web__search_images` (text ranking).
The audit runs inside the harness, so it uses DSH's own model routing and
attachment service — no API keys are managed by the plugin.

**Primary tool: `search_and_audit_images`** — one call does everything: it runs
the MCP image search internally (`ctx.tools.execute`), detects a vision model
from the DSH configuration itself, audits the top candidates in a single batch
request, and returns the final ranking merged from text and VLM scores. When no
vision model exists the same call returns the pure-text ranking
(`audit: "text_only"`) — the agent never has to chain two tools.

## How it works

1. **Vision-model detection (native, from DSH's own config)** — the current
   session route is tried first, then every model DSH itself declares as
   image-capable (`ctx.llm.listProviders()` → `ctx.llm.listModels(provider)`),
   each validated with `ctx.llm.resolveModelInfo` and requiring
   `inputModalities` to include `image`. Nothing probes ports, endpoints, or
   other machine-specific settings: a vision model is "known" exactly when the
   harness's own model catalog says so. No vision model → the tool returns
   `status: "unavailable"` and the caller falls back to pure-text scores.
2. **One batch call** — all candidates (default up to 16, recommend 8) are
   downloaded with browser-like headers (and Referer fallbacks on 403),
   persisted through the attachment service, and sent as ImageBlocks in a
   **single** `ctx.llm.stream` request, together with each image's title,
   image URL, source page, and text score. The top 3 ranked images are
   written to `tmp/image_audit/<query>/` in the session workspace.
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
# 3. A new insert in cordis.patch.yml is picked up by profile HMR (next turn).
#    Changing this package's JS after DSH has already imported it needs a DSH
#    process restart — node_modules is not watched and ESM caches the module.
```

## Config

| key | default | meaning |
|---|---|---|
| `routes` | `[]` | optional explicit vision-route overrides `[{provider, model}]`, tried after the session route and DSH-declared vision models; usually empty — vision models are auto-detected from the DSH model configuration |
| `maxImages` | `16` | max images per audit call (recommend 8) |
| `maxOutputTokens` | `32000` | audit answer token cap (free VLMs often burn thinking tokens before JSON) |
| `timeoutMs` | `180000` | end-to-end vision-call timeout |
| `vetoBelow` | `30` | VLM scores below this veto the candidate |
| `textWeight` | `0.5` | weight of the pure-text score in the final blend |

No `routes` needed: the DSH model catalog decides. Example — the vision model
is just a model DSH already knows can read images (configured in DSH settings
with `input: [text, image]`):

```yaml
- insert:
    - id: image-audit
      name: dsh-image-audit
      config:
        maxImages: 8
```

## Test

```bash
node --test "dsh-image-audit/test/*.test.js"
```
