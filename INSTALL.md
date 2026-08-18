# Agent installation contract

This file is intended for coding agents installing `dsh-bing-search` into an existing DeepSeek Harness setup.

Repository: `https://github.com/Biogod2020/dsh-bing-search`

## Goal

Install the `dsh-bing-search` executable, register it through DSH's official `@deepseek-ai/dsh-mcp-client`, preserve all unrelated user configuration, and verify a real web search (`mcp__web__search` may use DuckDuckGo or Bing).

## Required behavior

1. Inspect the existing DSH installation and determine which profile/config is actually active. Do not invent a profile path when one can be discovered from the user's current setup or invocation.
2. Prefer the profile patch layer (`cordis.patch.yml`) over editing the root `cordis.yml` when a patch layer is already in use.
3. Do not overwrite or reformat unrelated configuration.
4. Install the tool in an isolated environment with `uv` when available:

   ```bash
   uv tool install --force git+https://github.com/Biogod2020/dsh-bing-search.git
   ```

5. Resolve the installed executable to an absolute path. `uv tool dir --bin` reports the tool bin directory. Use `dsh-bing-search` on POSIX and the actual installed executable name on Windows.
6. When modifying `cordis.patch.yml`, add the plugin using the DSH patch `insert` form exactly once. If an `mcp-web` entry for this plugin already exists, update it instead of duplicating it.

   ```yaml
   - insert:
       - id: mcp-web
         name: '@deepseek-ai/dsh-mcp-client'
         config:
           serverName: web
           transport: stdio
           command: /ABSOLUTE/PATH/TO/dsh-bing-search
           args: []
           toolCallTimeoutMs: 30000
           failOnStartupError: true
           reconnect:
             enabled: true
             initialDelayMs: 500
             maxDelayMs: 30000
             maxAttempts: 10
   ```

7. A bare `- id: mcp-web` entry is valid in the root `cordis.yml`, but it is not the correct way to insert a previously unknown plugin through `cordis.patch.yml`.
8. Let DSH reload the profile normally. Restart DSH only if the existing setup requires it or hot reload does not occur.
9. Verify all three MCP tools are registered:

   ```text
   mcp__web__search
   mcp__web__search_images
   mcp__web__open
   mcp__web__find
   ```

10. Run one real search through the DSH tool surface, then open at least one returned public URL. Do not treat process startup alone as successful installation.
11. Report the executable path, the DSH config file changed, the exact plugin entry added/updated, and the smoke-test result.

## Development install fallback

If `uv tool install` is not appropriate because the user explicitly wants a development checkout:

```bash
git clone https://github.com/Biogod2020/dsh-bing-search.git
cd dsh-bing-search
uv sync --extra dev
```

Then point DSH at the absolute path of `.venv/bin/dsh-bing-search` (or the corresponding Windows executable).

## Safety constraints

- Do not delete existing DSH plugins.
- Do not replace the entire patch file to add this plugin.
- Do not disable TLS verification.
- Do not add CAPTCHA-bypass logic.
- Do not silently switch away from `curl_cffi` to another HTTP client for retrieval.
- Do not claim success until an actual tool call has been verified.
