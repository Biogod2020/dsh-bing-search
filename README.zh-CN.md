# dsh-bing-search

[English](./README.md)

<p align="center">
  <img src="docs/social-preview.png" width="100%" alt="dsh-bing-search：优先 DuckDuckGo，按语言回退 Bing，带质量分" />
</p>

给 **DeepSeek Harness (DSH)** 使用的网页搜索插件。它以 MCP stdio server 的形式接入 DSH，所有网络请求明确使用 [`curl_cffi`](https://github.com/lexiforest/curl_cffi)。

`search` 的顺序：

1. 探测 DuckDuckGo HTML（`html.duckduckgo.com`）是否可及（结果缓存约 60 秒）。
2. 可及则优先走 DDG。
3. DDG 不可及、被限流（HTTP 202 / challenge）、或结果 `quality_label=poor` 时，回退 Bing。
4. Bing 按语言分流：中文 / `zh-*` market 走 `cn.bing.com`，否则走 `www.bing.com`。

每条搜索响应都带 `quality_score`（0–1）和 `quality_label`（`good` / `weak` / `poor`）。
`poor` 表示标题和查询几乎对不上（词典页、只命中首词等），**不要当答案用**。

三个浏览器式工具：

- `mcp__web__search`：搜索公开网页，返回清洗后的自然搜索结果。
- `mcp__web__open`：打开公开网页并提取可读正文。
- `mcp__web__find`：在长网页中定位关键词并返回附近上下文。

```text
DSH agent
  -> @deepseek-ai/dsh-mcp-client
  -> dsh-bing-search (MCP/stdio)
  -> curl_cffi.AsyncSession(impersonate="chrome")
  -> html.duckduckgo.com          （可及时）
  -> 否则 cn.bing.com / www.bing.com
```

> DSH 官方目前通过 GitHub [`dsh-plugin`](https://github.com/topics/dsh-plugin) topic 发现社区插件。

## 最简单的安装方式：把仓库链接发给 Agent

如果你使用的 coding agent 有终端和文件系统权限（如 Codex、Claude Code、Pi、OpenCode），直接把下面这段发给它：

```text
请把这个 DeepSeek Harness 插件安装到我当前使用的 DSH 环境：
https://github.com/Biogod2020/dsh-bing-search

先阅读仓库 README 和 INSTALL.md。使用 uv 安装，自动识别我当前使用的 DSH profile，
通过 cordis.patch.yml 的 `insert` 形式添加插件，不要覆盖任何无关配置；配置中使用
已安装 dsh-bing-search 可执行文件的绝对路径。完成后确认 mcp__web__search、
mcp__web__open、mcp__web__find 三个工具已经注册，最后执行一次真实网页搜索作为 smoke test，
并告诉我改了哪些文件。
```

这是推荐方式。仓库中的 [`INSTALL.md`](./INSTALL.md) 是专门给 Agent 阅读的确定性安装协议。

## 手工安装

### 1. 安装可执行程序

需要 Python 3.10+。推荐使用 [`uv`](https://docs.astral.sh/uv/)：

```bash
uv tool install --force git+https://github.com/Biogod2020/dsh-bing-search.git
```

查看 uv tool 的可执行文件目录：

```bash
uv tool dir --bin
```

随后在 DSH 配置中使用 `dsh-bing-search` 的**绝对路径**；Windows 下对应 `dsh-bing-search.exe`。

如果你要开发插件而不是单纯安装：

```bash
git clone https://github.com/Biogod2020/dsh-bing-search.git
cd dsh-bing-search
uv sync --extra dev
```

仓库已提交 `uv.lock`，用于可复现的开发环境。

### 2. 接入 DSH

DSH profile 由根配置 `cordis.yml` 和 patch 层 `cordis.patch.yml` 合成。通过 patch 层新增插件时，必须使用 `insert`：

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

不要在 `cordis.patch.yml` 中直接写裸的 `- id: mcp-web`。裸条目只用于覆盖已经存在的 id，未知 id 可能会被跳过。如果你直接编辑根 `cordis.yml`，则普通裸插件条目是正确的。另见 [`cordis.example.yml`](./cordis.example.yml)。

### 3. 验证

DSH 重新加载 profile 后，模型应该能看到：

```text
mcp__web__search
mcp__web__open
mcp__web__find
```

随后让 Agent 搜索一个当前话题并打开其中一个结果。这个 round trip 同时验证搜索访问和 MCP 注册是否正常。改完插件代码后需要重启 DSH（或让 MCP 子进程重拉），stdio 进程不会热加载 Python。

## 工具接口

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

返回值包含：

| 字段 | 含义 |
|---|---|
| `provider` | `duckduckgo` 或 `bing` |
| `title` / `url` / `snippet` / `rank` | 自然结果 |
| `source_id` | 由规范化 URL 生成的稳定 ID |
| `quality_score` | 0–1，查询词与标题/摘要的重合度 |
| `quality_label` | `good` / `weak` / `poor` |
| `warnings` | 回退原因、质量警告 |

中文查询请把 `market` 设为 `zh-CN`。查询里带汉字时，即使 `market` 是 `en-US`，Bing 回退也会走中国站。

DDG 的 `/l/?uddg=` 与 Bing 的 `/ck/a` 跳转会被解开，常见追踪参数会被去掉，重复 URL 会合并。

人名、论文题、图解 blog 等长尾查询：先搜作者名或短专有名词；`quality_label=poor` 时不要连打加长 query。中文学术题录更适合专用语料（如知网），不要指望通用网页搜索。

### `open`

```json
{
  "url": "https://example.com/article",
  "max_chars": 24000
}
```

只允许公开 HTTP(S) 地址；会进行 DNS/IP 检查和安全重定向处理，限制响应体大小，并在不执行 JavaScript 的情况下提取正文。

### `find`

```json
{
  "url": "https://example.com/article",
  "pattern": "DeepSeek",
  "max_matches": 5,
  "context_chars": 700
}
```

只返回命中位置附近的内容，避免为了找一句话把整页塞进模型上下文。

## 为什么是 `search + open + find`

插件刻意不做一个巨大的 `search_and_summarize` 黑盒。更合理的研究循环是：

```text
search -> 查看候选来源 -> open -> find / 再次 search -> 综合
```

插件负责 HTTP、解析、清洗、缓存、引擎回退、来源追踪和质量标记；DSH 主模型负责决定搜什么、看哪篇、是否改写查询，以及何时证据已经足够。模型必须阅读 `quality_label` 和 `warnings`。

## 配置

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `DSH_BING_SEARCH_URL` | `https://www.bing.com/search` | 仅当设成非默认值时覆盖 Bing 入口（测试用）。默认会按语言在 `www` / `cn` 之间选择 |
| `DSH_WEB_IMPERSONATE` | `chrome` | `curl_cffi` 浏览器指纹目标 |
| `DSH_WEB_PROXY` | 空 | HTTP/HTTPS/SOCKS 代理。进程默认 `trust_env=False`，不会读窗口里的 `HTTP_PROXY` |
| `DSH_WEB_TIMEOUT_SECONDS` | `20` | 请求传输超时 |
| `DSH_WEB_CONNECT_TIMEOUT_SECONDS` | `8` | 连接超时 |
| `DSH_WEB_MAX_BODY_BYTES` | `5242880` | `open` 最大响应体 |
| `DSH_BING_MAX_BODY_BYTES` | `2097152` | 搜索页最大响应体 |
| `DSH_WEB_MAX_REDIRECTS` | `8` | 最大重定向次数 |
| `DSH_WEB_CONCURRENCY` | `8` | MCP 进程内最大并发请求数 |
| `DSH_BING_CACHE_TTL_SECONDS` | `90` | 搜索缓存 TTL |
| `DSH_WEB_CACHE_TTL_SECONDS` | `600` | 网页正文缓存 TTL |

## 测试

离线测试（解析、质量分、语言分流、DDG 优先 / Bing 回退）：

```bash
uv run pytest -m "not live"
```

真实联网 smoke test：

```bash
RUN_LIVE_BING=1 uv run pytest -m live -s
```

标记名仍是 `live` / `RUN_LIVE_BING`。在线时会先打 DDG，DDG 不可及才打 Bing。

CI 覆盖 Python 3.10、3.12、3.13、3.14。

## 设计与安全边界

这是 DuckDuckGo HTML 与 Bing HTML 的非官方适配层，不依赖已经退役的 Bing Search API。

- DDG DOM 在 `src/dsh_bing_search/providers/ddg.py`。
- Bing DOM 在 `src/dsh_bing_search/providers/bing_parser.py`。
- 质量分在 `src/dsh_bing_search/quality.py`，与引擎无关。
- 所有网络请求使用 `curl_cffi.AsyncSession`。
- 用户提供的页面 URL 只允许公开 HTTP(S) 目标，并启用安全重定向。
- 响应体有大小限制。
- CAPTCHA / challenge / HTTP 202 返回 `status="blocked"`，不会尝试绕过验证。
- 无头访问下 Bing 国际站常返回结构完整但内容无关的结果；中国站能修好部分热门中文，长尾人名/标题仍可能首词坍缩。质量分就是为这种情况准备的。
- `open` 默认不自动重试慢站点，需要时可调大 timeout 环境变量。

## 实测里容易踩的点

- **MCP 子进程不会继承你终端里的 `HTTP_PROXY` / `10808`。** 插件用 `trust_env=False` 起 `curl_cffi`。本机直连打不开 DDG 时，在 cordis 的 `env:` 里设 `DSH_WEB_PROXY: http://127.0.0.1:10808`。看到 `duckduckgo_unreachable` 就是探测失败、已经改走 Bing。
- **`quality_label=weak` 不等于人名也搜到了。** 「魏子涵 + 颞叶癫痫」可能因论文主题对上而打 weak，warnings 里的 `missing=` 会列出没出现的词（如人名、学校）。先看这个再引用。
- **`open` 不是天气组件。** tianqi.com / weather.com.cn 一类页面 trafilatura 经常只抽出导航。温度请看搜索 snippet，或换更干净的页面再 `open`。
- 改完插件代码要重启 DSH 或 MCP 子进程，stdio 不会热加载 Python。

## 社区

DeepSeek Harness 目前仍处于 developer preview，插件接口可能继续变化。DSH 官方目前建议通过 [`dsh-plugin`](https://github.com/topics/dsh-plugin) GitHub topic 发现第三方插件。

欢迎提交 issue、PR 和 parser 修复。

## License

MIT
