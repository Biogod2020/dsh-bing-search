# dsh-bing-search

给 **DeepSeek Harness (DSH)** 使用的 Bing 搜索 MCP 插件。网络请求明确使用
`curl_cffi.AsyncSession`，通过浏览器指纹模拟访问 Bing HTML 搜索页，并向 DSH 暴露三个原生工具：

- `mcp__web__search`：搜索 Bing，返回标准化的自然搜索结果。
- `mcp__web__open`：打开公开网页并提取可读正文。
- `mcp__web__find`：在长网页中定位关键词并返回上下文。

核心链路：

```text
DSH agent
  -> @deepseek-ai/dsh-mcp-client
  -> this Python MCP server
  -> curl_cffi.AsyncSession(impersonate="chrome")
  -> https://www.bing.com/search
```

## 安装

需要 Python 3.10+，推荐使用 `uv`：

```bash
git clone https://github.com/Biogod2020/dsh-bing-search.git
cd dsh-bing-search
uv sync --extra dev
```

运行 MCP stdio server：

```bash
uv run dsh-bing-search
```

stdio server 正常启动后会等待 MCP host，不会输出普通日志到 stdout。

## 接入 DSH

先在仓库内运行一次 `uv sync`，然后把下面配置加入 DSH 的 `cordis.yml`：

```yaml
- id: mcp-web
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: web
    transport: stdio
    command: /ABS/PATH/dsh-bing-search/.venv/bin/dsh-bing-search
    args: []
    toolCallTimeoutMs: 30000
    failOnStartupError: true
    reconnect:
      enabled: true
      initialDelayMs: 500
      maxDelayMs: 30000
      maxAttempts: 10
```

Windows 下命令通常是：

```text
C:\ABS\PATH\dsh-bing-search\.venv\Scripts\dsh-bing-search.exe
```

DSH 完成 MCP 工具发现后，模型会看到：

```text
mcp__web__search
mcp__web__open
mcp__web__find
```

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

返回值包含 `title`、`url`、`snippet`、`rank` 和由规范化 URL 生成的稳定 `source_id`。
Bing `/ck/a` 跳转链接会被解码，常见追踪参数会被移除，重复结果会被合并。

### `open`

```json
{
  "url": "https://example.com/article",
  "max_chars": 24000
}
```

只允许公开 `http://` / `https://` 地址。初始目标会做 DNS/IP 检查，重定向使用
`curl_cffi.CurlFollow.SAFE`，响应体有大小上限，不执行 JavaScript。

### `find`

```json
{
  "url": "https://example.com/article",
  "pattern": "DeepSeek",
  "max_matches": 5,
  "context_chars": 700
}
```

## 配置

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `DSH_BING_SEARCH_URL` | `https://www.bing.com/search` | Bing HTML 搜索入口 |
| `DSH_WEB_IMPERSONATE` | `chrome` | `curl_cffi` 浏览器指纹目标 |
| `DSH_WEB_PROXY` | 空 | HTTP/HTTPS/SOCKS 代理 |
| `DSH_WEB_TIMEOUT_SECONDS` | `20` | 请求传输超时 |
| `DSH_WEB_CONNECT_TIMEOUT_SECONDS` | `8` | 连接超时 |
| `DSH_WEB_MAX_BODY_BYTES` | `5242880` | `open` 最大下载字节数 |
| `DSH_BING_MAX_BODY_BYTES` | `2097152` | Bing 搜索页最大字节数 |
| `DSH_WEB_MAX_REDIRECTS` | `8` | 最大重定向次数 |
| `DSH_WEB_CONCURRENCY` | `8` | MCP 进程内最大并发请求数 |
| `DSH_BING_CACHE_TTL_SECONDS` | `90` | 搜索缓存 TTL |
| `DSH_WEB_CACHE_TTL_SECONDS` | `600` | 网页正文缓存 TTL |

## 测试

离线单元测试：

```bash
uv run pytest -m "not live"
```

真实 Bing smoke test：

```bash
RUN_LIVE_BING=1 uv run pytest -m live -s
```

## 设计边界

这是 Bing HTML 的非官方适配层，不依赖已退役的 Bing Search API。Bing DOM 变化被隔离在
`src/dsh_bing_search/providers/bing_parser.py`。如果 Bing 返回 CAPTCHA 或 challenge 页面，工具会返回
`status="blocked"`，不会尝试绕过验证。

插件只负责获取、解析、清洗、缓存与来源追踪；搜索策略、查询改写、来源判断和最终综合由 DSH agent 完成。
