import test from 'node:test'
import assert from 'node:assert/strict'

async function loadValidator() {
  try {
    const mod = await import('@deepseek-ai/dsh-tools')
    return mod.validateJsonSchemaValue
  } catch {
    return null
  }
}

const loaded = await import('../src/index.js').then(
  mod => ({ apply: mod.apply }),
  err => ({ error: err }),
)

function pngBytes() {
  return new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])
}

function mockCtx({ searchValue, visionText, listModels = async () => [{ id: 'vision', inputModalities: ['text', 'image'] }] }) {
  const registered = new Map()
  const llm = {
    listProviders: () => [{ id: 'local' }],
    listModels,
    resolveModelInfo: async (_provider, model) => ({
      inputModalities: model === 'vision' ? ['text', 'image'] : ['text'],
    }),
    async *stream() {
      yield { type: 'text-delta', index: 0, text: visionText }
    },
  }
  const attachments = {
    saveImage: async ({ mediaType }) => ({ id: 'att-1', mediaType }),
  }
  const ctx = {
    get(name) {
      if (name === 'llm') return llm
      if (name === 'attachments') return attachments
      return undefined
    },
    tools: {
      register(def) {
        registered.set(def.name, def)
      },
      async execute() {
        return { isError: false, value: searchValue }
      },
    },
  }
  return { ctx, registered }
}

test('search_and_audit_images unwraps MCP structuredContent and survives a failed first download', async t => {
  if (loaded.error) {
    t.skip(`DSH packages not resolvable: ${loaded.error.message}`)
    return
  }
  const { apply } = loaded
  const okPng = pngBytes()
  const originalFetch = globalThis.fetch
  globalThis.fetch = async url => {
    if (String(url).includes('fail')) {
      return { ok: false, status: 403, headers: { get: () => 'text/html' } }
    }
    return {
      ok: true,
      headers: { get: () => 'image/png' },
      arrayBuffer: async () => okPng.buffer,
    }
  }
  try {
    const wrapped = {
      content: [{ type: 'text', text: '{"results":[]}' }],
      structuredContent: {
        provider: 'bing_images',
        warnings: [],
        results: [
          { murl: 'https://cdn.example/fail.jpg', title: 'bad', score: 20, purl: 'https://bad.example/p' },
          { murl: 'https://cdn.example/ok.png', title: 'good', score: 90, purl: 'https://good.example/p' },
        ],
      },
    }
    const { ctx, registered } = mockCtx({
      searchValue: wrapped,
      visionText: '[{"index":1,"accept":true,"score":88,"reasons":["clear uniform"]}]',
    })
    apply(ctx, { maxImages: 8, routes: [{ provider: 'local', model: 'vision' }] })
    const tool = registered.get('search_and_audit_images')
    assert.ok(tool, 'search_and_audit_images should register')
    const result = await tool.execute(
      { query: '广州市第二中学校服', count: 2, market: 'zh-CN' },
      { signal: AbortSignal.timeout(5000) },
    )
    assert.equal(result.status, 'ok')
    assert.equal(result.audit, 'vlm')
    assert.equal(result.provider, 'bing_images')
    assert.equal(result.results.length, 2)
    const ok = result.results.find(row => row.url.includes('ok.png'))
    const failed = result.results.find(row => row.url.includes('fail'))
    assert.equal(ok.vlm_score, 88)
    assert.equal(ok.accept, true)
    assert.equal(failed.error, 'http_403')
    assert.equal(failed.vlm_score, undefined)
    assert.equal(ok.index, undefined)
    assert.equal(failed.index, undefined)
    assert.equal(failed.referer, undefined)
    const validate = await loadValidator()
    if (validate) {
      assert.deepEqual(validate(tool.output.schema, result, 'value'), [])
    }
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('search_and_audit_images does not throw when assembling vision routes', async t => {
  if (loaded.error) {
    t.skip(`DSH packages not resolvable: ${loaded.error.message}`)
    return
  }
  const { apply } = loaded
  const { ctx, registered } = mockCtx({
    searchValue: {
      structuredContent: {
        provider: 'commons',
        results: [{ murl: 'https://upload.wikimedia.org/a.png', title: 'a', score: 70 }],
      },
    },
    visionText: '[{"index":1,"accept":true,"score":70,"reasons":["ok"]}]',
  })
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => ({
    ok: true,
    headers: { get: () => 'image/png' },
    arrayBuffer: async () => pngBytes().buffer,
  })
  try {
    apply(ctx, {})
    const result = await registered.get('search_and_audit_images').execute(
      { query: 'test', count: 1 },
      { signal: AbortSignal.timeout(5000) },
    )
    assert.equal(result.status, 'ok')
    assert.equal(result.audit, 'vlm')
    assert.doesNotMatch(JSON.stringify(result), /resolved is not defined/)
  } finally {
    globalThis.fetch = originalFetch
  }
})
