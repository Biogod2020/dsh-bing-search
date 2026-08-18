import test from 'node:test'
import assert from 'node:assert/strict'
import {
  applyAuditVerdicts,
  buildAuditPrompt,
  candidatesFromSearch,
  collectVisionRoutes,
  downloadImage,
  enumerateVisionCandidates,
  formatImageCaption,
  mergeScores,
  parseAuditResponse,
  persistTopImages,
  pickPersistable,
  pickVisionRoute,
  publicSearchHit,
  querySlug,
  refererCandidates,
  resolveConfig,
  sniffMediaType,
  unwrapMcpToolValue,
} from '../src/core.js'

test('resolveConfig applies defaults and validates ranges', () => {
  const cfg = resolveConfig({})
  assert.equal(cfg.maxImages, 16)
  assert.equal(cfg.maxOutputTokens, 32000)
  assert.equal(cfg.timeoutMs, 180000)
  assert.equal(cfg.vetoBelow, 30)
  assert.equal(cfg.textWeight, 0.5)
  assert.deepEqual(cfg.routes, [])
  assert.throws(() => resolveConfig({ maxImages: 0 }))
  assert.throws(() => resolveConfig({ maxImages: 21 }))
  assert.throws(() => resolveConfig({ maxOutputTokens: 63 }))
  assert.throws(() => resolveConfig({ maxOutputTokens: 32001 }))
  assert.throws(() => resolveConfig(null))
  const cfg2 = resolveConfig({ routes: ['my-vision/mimo', { provider: 'x', model: 'y' }, 'junk'] })
  assert.equal(cfg2.routes.length, 2)
})

test('enumerateVisionCandidates finds only DSH-declared image-capable models', async () => {
  const listProviders = async () => [
    { id: 'text-only' },
    { id: 'vision' },
    { id: 'broken' },
    { id: 'empty' },
    'string-provider',
  ]
  const listModels = async provider => {
    if (provider === 'text-only') return [{ id: 'a', inputModalities: ['text'] }]
    if (provider === 'vision') {
      return [
        { id: 'm1', inputModalities: ['text', 'image'], name: 'Vision One' },
        { id: 'm2', inputModalities: ['text'] },
        { id: 'm3' }, // absent modality = unknown, not image
      ]
    }
    if (provider === 'broken') throw new Error('catalog unreachable')
    if (provider === 'empty') return []
    return [{ id: 'x', inputModalities: ['text', 'image'] }] // string provider
  }
  const found = await enumerateVisionCandidates(listProviders, listModels)
  assert.deepEqual(found, [
    { provider: 'vision', model: 'm1', name: 'Vision One' },
    { provider: 'string-provider', model: 'x' },
  ])
  assert.deepEqual(await enumerateVisionCandidates(null, listModels), [])
  assert.deepEqual(await enumerateVisionCandidates(async () => { throw new Error('boom') }, listModels), [])
})

test('buildAuditPrompt includes per-image title and URLs in download order', () => {
  const prompt = buildAuditPrompt({
    query: '广州市第二中学校服',
    criteria: 'no collages',
    count: 2,
    images: [
      {
        title: '广州市二中校服_文秘苑图库',
        url: 'https://files.eduuu.com/a.jpg',
        purl: 'http://www.wenmiyuan.com/p.html',
        text_score: 90,
      },
      {
        title: null,
        url: 'https://cdn.example/b.jpg',
      },
    ],
  })
  assert.match(prompt, /2 numbered images/)
  assert.match(prompt, /广州市第二中学校服/)
  assert.match(prompt, /no collages/)
  assert.match(prompt, /JSON array/)
  assert.match(prompt, /\[1\]/)
  assert.match(prompt, /title: 广州市二中校服_文秘苑图库/)
  assert.match(prompt, /image_url: https:\/\/files\.eduuu\.com\/a\.jpg/)
  assert.match(prompt, /source_page: http:\/\/www\.wenmiyuan\.com\/p\.html/)
  assert.match(prompt, /text_score: 90\/100/)
  assert.match(prompt, /\[2\]/)
  assert.match(prompt, /title: \(none\)/)
  assert.match(prompt, /trust the pixels/)
  assert.equal(formatImageCaption({ title: 'x' }, 3).startsWith('[3]'), true)
})

test('parseAuditResponse handles plain, fenced and garbage output', () => {
  const plain = '[{"index":1,"accept":true,"score":85,"reasons":["a"]},{"index":2,"accept":false,"score":10}]'
  assert.deepEqual(parseAuditResponse(plain, 2), [
    { index: 1, accept: true, score: 85, reasons: ['a'] },
    { index: 2, accept: false, score: 10, reasons: [] },
  ])
  const fenced = '```json\n' + plain + '\n```'
  assert.equal(parseAuditResponse(fenced, 2).length, 2)
  assert.equal(parseAuditResponse('sorry, I cannot', 2).length, 0)
  assert.equal(parseAuditResponse('[{"index":9,"score":50}]', 2).length, 0) // out of range
  assert.equal(parseAuditResponse('', 2).length, 0)
})

test('mergeScores vetoes below threshold and blends otherwise', () => {
  assert.deepEqual(mergeScores({ vlmScore: 10, textScore: 90 }), { finalScore: 10, vetoed: true })
  assert.deepEqual(mergeScores({ vlmScore: 85, textScore: 75 }), { finalScore: 80, vetoed: false })
  assert.deepEqual(mergeScores({ vlmScore: 85 }), { finalScore: 85, vetoed: false })
  assert.deepEqual(mergeScores({ vlmScore: 100, textScore: 0, textWeight: 0 }), { finalScore: 100, vetoed: false })
})

test('pickVisionRoute picks the first image-capable route and skips errors', async () => {
  const resolve = async (provider, model) => {
    if (provider === 'bad') throw new Error('unreachable')
    return { inputModalities: model === 'vision' ? ['text', 'image'] : ['text'] }
  }
  const picked = await pickVisionRoute(
    [{ provider: 'bad', model: 'x' }, { provider: 'local', model: 'vision' }, { provider: 'local', model: 'text' }],
    resolve,
  )
  assert.equal(picked.provider, 'local')
  assert.equal(picked.model, 'vision')
  assert.equal(await pickVisionRoute([{ provider: 'local', model: 'text' }], resolve), null)
  assert.equal(await pickVisionRoute([], resolve), null)
})

test('unwrapMcpToolValue prefers structuredContent over the MCP wrapper', () => {
  const payload = {
    provider: 'bing_images',
    warnings: ['auto_fallback'],
    results: [{ murl: 'https://x.com/a.jpg', title: 't', score: 90, purl: 'https://src.example/p' }],
  }
  const wrapped = {
    content: [{ type: 'text', text: JSON.stringify({ results: [] }) }],
    structuredContent: payload,
  }
  assert.equal(unwrapMcpToolValue(wrapped), payload)
  assert.deepEqual(unwrapMcpToolValue(payload), payload)
  const textOnly = { content: [{ type: 'text', text: JSON.stringify(payload) }] }
  assert.deepEqual(unwrapMcpToolValue(textOnly), payload)
})

test('candidatesFromSearch reads MCP-wrapped search_images results', () => {
  const wrapped = {
    content: [{ type: 'text', text: 'ignore me' }],
    structuredContent: {
      provider: 'bing_images',
      warnings: [],
      results: [
        { murl: 'https://x.com/a.jpg', title: '校服', score: 90, purl: 'http://www.wenmiyuan.com/p' },
        { murl: 'not-a-url', title: 'bad' },
      ],
    },
  }
  const search = candidatesFromSearch(wrapped)
  assert.equal(search.provider, 'bing_images')
  assert.equal(search.results.length, 1)
  assert.equal(search.results[0].url, 'https://x.com/a.jpg')
  assert.equal(search.results[0].referer, 'http://www.wenmiyuan.com/p')
  assert.equal(search.results[0].text_score, 90)
  assert.deepEqual(candidatesFromSearch(wrapped).results, search.results)
  assert.equal(candidatesFromSearch({ content: [], structuredContent: {} }).results.length, 0)
})

test('collectVisionRoutes does not depend on an outer resolved binding', () => {
  const session = { provider: 'iworld', model: 'flash' }
  const enumerated = [{ provider: 'local-4002', model: 'mimo' }]
  const routes = [{ provider: 'override', model: 'v' }]
  assert.deepEqual(collectVisionRoutes(session, enumerated, routes), [session, ...enumerated, ...routes])
  assert.deepEqual(collectVisionRoutes(null, enumerated, []), enumerated)
})

test('publicSearchHit omits nulls and undeclared keys', () => {
  const hit = publicSearchHit(
    { url: 'https://x.com/a.jpg', title: null, purl: 'http://src', referer: 'http://src', text_score: 70, index: 3 },
    1,
  )
  assert.deepEqual(hit, { rank: 1, url: 'https://x.com/a.jpg', purl: 'http://src', text_score: 70 })
})

test('applyAuditVerdicts numbers only successful downloads', () => {
  const rows = [
    { index: 1, url: 'https://x.com/fail.jpg', error: 'http_403' },
    { index: 2, url: 'https://x.com/ok.jpg' },
  ]
  applyAuditVerdicts(rows, [{ index: 1, accept: true, score: 88, reasons: ['clear'] }])
  assert.equal(rows[0].error, 'http_403')
  assert.equal(rows[0].vlm_score, undefined)
  assert.equal(rows[1].vlm_score, 88)
  assert.equal(rows[1].accept, true)
  assert.deepEqual(rows[1].reasons, ['clear'])
})

test('refererCandidates try source page, origin, then bing, then none', () => {
  const list = refererCandidates('https://cdn.example/a.jpg', 'http://www.wenmiyuan.com/p')
  assert.deepEqual(list, [
    'http://www.wenmiyuan.com/p',
    'https://cdn.example/',
    'https://www.bing.com/',
    'https://cn.bing.com/',
    null,
  ])
})

test('sniffMediaType recovers jpeg when the server lies about content-type', () => {
  const jpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 0, 0, 0, 0, 0, 0, 0])
  assert.equal(sniffMediaType(jpeg, 'application/octet-stream'), 'image/jpeg')
  assert.equal(sniffMediaType(jpeg, 'image/jpeg'), 'image/jpeg')
})

test('pickPersistable keeps the first N downloadable rows', () => {
  const rows = [
    { url: 'https://a', error: 'http_403' },
    { url: 'https://b', final_score: 90 },
    { url: 'https://c', final_score: 80 },
    { url: 'https://d', final_score: 70 },
    { url: 'https://e', final_score: 60 },
  ]
  assert.deepEqual(pickPersistable(rows, 3).map(r => r.url), ['https://b', 'https://c', 'https://d'])
})

test('querySlug keeps CJK and strips punctuation', () => {
  assert.equal(querySlug('复旦大学光华楼'), '复旦大学光华楼')
  assert.match(querySlug('Fudan / Guanghua!'), /^Fudan-Guanghua$/)
})

test('persistTopImages writes the top 3 files', async () => {
  const written = []
  const rows = [
    { url: 'https://a', final_score: 10, data: new Uint8Array([1]), mediaType: 'image/jpeg' },
    { url: 'https://b', final_score: 90, data: new Uint8Array([2]), mediaType: 'image/png' },
    { url: 'https://c', final_score: 80, data: new Uint8Array([3]), mediaType: 'image/jpeg' },
    { url: 'https://d', final_score: 70, data: new Uint8Array([4]), mediaType: 'image/jpeg' },
  ]
  const result = await persistTopImages(rows, {
    query: '复旦大学光华楼',
    cwd: '/tmp/ws',
    keep: 3,
    mkdir: async () => {},
    writeFile: async (path, data) => { written.push({ path, data }) },
  })
  assert.equal(result.saved, 3)
  assert.equal(result.savedDir, '/tmp/ws/tmp/image_audit/复旦大学光华楼')
  assert.deepEqual(written.map(item => item.path), [
    '/tmp/ws/tmp/image_audit/复旦大学光华楼/01.png',
    '/tmp/ws/tmp/image_audit/复旦大学光华楼/02.jpg',
    '/tmp/ws/tmp/image_audit/复旦大学光华楼/03.jpg',
  ])
  assert.equal(rows[1].local_path, written[0].path)
})

test('downloadImage retries with another referer after 403', async () => {
  const seen = []
  const png = new Uint8Array([137, 80, 78, 71, 1, 2, 3, 4, 5, 6, 7, 8])
  const fetchImpl = async (url, init) => {
    seen.push(init.headers.Referer ?? null)
    if (seen.length === 1) return { ok: false, status: 403, headers: { get: () => 'text/html' } }
    return { ok: true, headers: { get: () => 'image/png' }, arrayBuffer: async () => png.buffer }
  }
  const { mediaType } = await downloadImage('https://cdn.example/a.png', {
    referer: 'https://blocked.example/p',
    fetchImpl,
  })
  assert.equal(mediaType, 'image/png')
  assert.ok(seen.length >= 2)
  assert.equal(seen[0], 'https://blocked.example/p')
})

test('downloadImage enforces type, size and errors', async () => {
  const png = new Uint8Array([137, 80, 78, 71, 1, 2, 3])
  const okFetch = async () => ({
    ok: true,
    headers: { get: () => 'image/png' },
    arrayBuffer: async () => png.buffer,
  })
  const { data, mediaType } = await downloadImage('https://x.com/a.png', { fetchImpl: okFetch })
  assert.equal(mediaType, 'image/png')
  assert.equal(data.byteLength, 7)

  await assert.rejects(
    downloadImage('https://x.com/a.html', {
      fetchImpl: async () => ({
        ok: true,
        headers: { get: () => 'text/html' },
        arrayBuffer: async () => new Uint8Array([60, 104, 116, 109, 108]).buffer,
      }),
    }),
    /unsupported_content_type/,
  )
  await assert.rejects(
    downloadImage('not-a-url', { fetchImpl: okFetch }),
    /invalid url/,
  )
  await assert.rejects(
    downloadImage('https://x.com/big.png', {
      maxBytes: 4,
      fetchImpl: async () => ({ ok: true, headers: { get: () => 'image/png' }, arrayBuffer: async () => png.buffer }),
    }),
    /too_large/,
  )
  await assert.rejects(
    downloadImage('https://x.com/nope.png', {
      fetchImpl: async () => ({ ok: false, status: 403 }),
    }),
    /http_403/,
  )
})
