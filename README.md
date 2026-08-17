# dsh-bing-search

[简体中文](./README.zh-CN.md)

Bing web search for **DeepSeek Harness (DSH)**, implemented as a small MCP server and powered by [`curl_cffi`](https://github.com/lexiforest/curl_cffi).

It gives a DSH agent three browser-style tools:

- `mcp__web__search` — search Bing and return normalized organic results.
- `mcp__web__open` — open a public web page and extract readable text.
- `mcp__web__find` — find text inside a long page and return nearby context.

```text
DSH agent
  -> @deepseek-ai/dsh-mcp-client
  -> dsh-bing-search (MCP/stdio)
  -> curl_cffi.AsyncSession(impersonate="chrome")
  -> Bing / public web pages
```

> Community plugin: DeepSeek Harness asks third-party plugins to use the [`dsh-plugin`](https://github.com/topics/dsh-plugin) GitHub topic for discovery.

## Fastest install: give this repo to an agent

If your coding agent has terminal and filesystem access (Codex, Claude Code, Pi, OpenCode, etc.), paste this:

```text
Install this DeepSeek Harness plugin into my current DSH setup:
https://github.com/Biogod2020/dsh-bing-search

Read the repository README and INSTALL.md first. Install it with uv, detect my active
DSH profile, add it through cordis.patch.yml using the required `insert` patch form,
preserve all unrelated config, use the absolute path of the installed dsh-bing-search
executable, then verify that mcp__web__search, mcp__web__open, and mcp__web__find are
registered. Finally run one real Bing search smoke test and report what changed.
```

That is the recommended path. [`INSTALL.md`](./INSTALL.md) contains a deterministic install contract written for agents.

## Manual install

### 1. Install the executable

Python 3.10+ is required. With [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install --force git+https://github.com/Biogod2020/dsh-bing-search.git
```

Find the tool bin directory:

```bash
uv tool dir --bin
```

Use the **absolute path** to `dsh-bing-search` (or `dsh-bing-search.exe` on Windows) in the DSH config below.

For development instead of a tool install:

```bash
git clone https://github.com/Biogod2020/dsh-bing-search.git
cd dsh-bing-search
uv sync --extra dev
```

The repository includes `uv.lock` for reproducible development installs.

### 2. Add it to DSH

DSH profiles combine a root `cordis.yml` with a patch layer `cordis.patch.yml`. When adding a new plugin through the patch layer, the entry **must** be wrapped in `insert`:

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

Do **not** add a bare `- id: mcp-web` entry to `cordis.patch.yml`: bare entries patch existing IDs and an unknown ID can be skipped. If you are editing the root `cordis.yml` directly, a normal bare plugin entry is correct. See [`cordis.example.yml`](./cordis.example.yml).

### 3. Verify

After DSH reloads the profile, the model should see:

```text
mcp__web__search
mcp__web__open
mcp__web__find
```

Then ask the agent to search for something current and open one result. A successful round trip verifies both Bing access and MCP registration.

## Tools

### `search`

```json
{
  "query": "DeepSeek Harness GitHub",
  "count": 8,
  "offset": 0,
  "market": "en-US",
  "safe_search": "Moderate"
}
```

Returns normalized `title`, `url`, `snippet`, `rank`, and a stable `source_id`. Bing `/ck/a` redirect URLs are decoded where possible, common tracking parameters are removed, and duplicate results are merged.

### `open`

```json
{
  "url": "https://example.com/article",
  "max_chars": 24000
}
```

Fetches public HTTP(S) pages with `curl_cffi`, applies DNS/IP checks and safe redirects, limits response size, and extracts readable text without executing JavaScript.

### `find`

```json
{
  "url": "https://example.com/article",
  "pattern": "DeepSeek",
  "max_matches": 5,
  "context_chars": 700
}
```

Returns matching regions without injecting the entire page into the model context.

## Why three tools instead of one giant `search_and_summarize` tool?

The plugin keeps retrieval deterministic and lets the DSH model control the research loop:

```text
search -> inspect candidates -> open -> find / search again -> synthesize
```

The plugin handles HTTP, parsing, cleaning, caching and provenance. The agent decides what to search, which sources to trust, when to reformulate the query, and when enough evidence has been collected.

## Configuration

| Environment variable | Default | Purpose |
|---|---:|---|
| `DSH_BING_SEARCH_URL` | `https://www.bing.com/search` | Bing HTML search endpoint |
| `DSH_WEB_IMPERSONATE` | `chrome` | `curl_cffi` browser fingerprint |
| `DSH_WEB_PROXY` | empty | HTTP/HTTPS/SOCKS proxy |
| `DSH_WEB_TIMEOUT_SECONDS` | `20` | Transfer timeout |
| `DSH_WEB_CONNECT_TIMEOUT_SECONDS` | `8` | Connect timeout |
| `DSH_WEB_MAX_BODY_BYTES` | `5242880` | Maximum body size for `open` |
| `DSH_BING_MAX_BODY_BYTES` | `2097152` | Maximum Bing SERP body size |
| `DSH_WEB_MAX_REDIRECTS` | `8` | Maximum redirects |
| `DSH_WEB_CONCURRENCY` | `8` | Maximum in-process concurrent requests |
| `DSH_BING_CACHE_TTL_SECONDS` | `90` | Search cache TTL |
| `DSH_WEB_CACHE_TTL_SECONDS` | `600` | Page cache TTL |

## Tests

Offline tests:

```bash
uv run pytest -m "not live"
```

Real Bing smoke test:

```bash
RUN_LIVE_BING=1 uv run pytest -m live -s
```

CI covers Python 3.10, 3.12, 3.13 and 3.14.

## Design and safety notes

This is an unofficial Bing HTML adapter; it does not use the retired Bing Search API. Bing-specific DOM parsing is isolated in `src/dsh_bing_search/providers/bing_parser.py` so it can be repaired without changing the DSH-facing tool contract.

- Requests use `curl_cffi.AsyncSession` with browser impersonation.
- User-supplied page URLs are restricted to public HTTP(S) targets and safe redirect handling is enabled.
- Response bodies are size-limited.
- CAPTCHA/challenge pages are reported as `status="blocked"`; the plugin does not attempt to bypass them.
- Complex long queries can be less stable than short focused Bing queries; query decomposition is best handled by the DSH agent.
- `open` does not automatically retry slow target sites; increase the timeout environment variables if needed.

## Community

DeepSeek Harness is currently in developer preview, so plugin interfaces may still evolve. For DSH-specific support and discovery:

- Browse the [`dsh-plugin`](https://github.com/topics/dsh-plugin) topic.
- See the [DeepSeek Harness repository](https://github.com/deepseek-ai/deepseek-harness).
- Join the DSH community channels linked from the official repository.

Contributions and parser fixes are welcome.

## License

MIT
