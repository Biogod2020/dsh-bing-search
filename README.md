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

`uv.lock` 已提交，`uv sync` 按锁文件安装，保证依赖可复现。

运行 MCP stdio server：

```bash
uv run dsh-bing-search
```

stdio server 正常启动后会等待 MCP host，不会输出普通日志到 stdout。

## 接入 DSH

先在仓库内运行一次 `uv sync`。DSH profile 由根配置（`cordis.yml`，条目列表）和 patch 层
（`cordis.patch.yml`，patch 操作列表）合成，两种写法不同，注意区分。

### 方式一：patch 层（推荐，`cordis.patch.yml`）

patch 层追加新插件条目必须用 `- insert:` 包裹：

```yaml
- insert:
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

⚠️ 不要使用裸条目（不带 `- insert:` 的 `- id: ...`）：patch 层里裸条目只按 id 覆盖**已有**
条目，id 不存在时会被**静默跳过**（只有 DSH 服务端日志里有一条 warning），插件不会加载，
界面上看不到任何异常。

修改后无需手动重启：格式正确时 DSH 的 profile watcher 会自动拉起 MCP 进程。若 20 秒内
`pgrep -f dsh-bing-search` 仍没有新进程，请检查是否用了 `- insert:` 包裹，然后重启 DSH。

### 方式二：profile 根配置（`cordis.yml`）

如果直接编辑 profile 根文件（条目列表），使用裸条目形式即可：

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

已知限制：

- **复杂查询相关性不稳定**：带多个关键词/年份的长查询偶尔会返回泛化结果（Bing HTML
  对长查询的解析不如 API 稳定），建议拆短查询、多查询交叉验证。
- **`open` 不做自动重试**：目标站点连接超时（默认 8s）或传输超时（默认 20s）会直接返回
  `fetch_error`；对慢站点可调大 `DSH_WEB_TIMEOUT_SECONDS` / `DSH_WEB_CONNECT_TIMEOUT_SECONDS`。

插件只负责获取、解析、清洗、缓存与来源追踪；搜索策略、查询改写、来源判断和最终综合由 DSH agent 完成。
