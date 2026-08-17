import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildAuditPrompt,
  downloadImage,
  enumerateVisionCandidates,
  mergeScores,
  parseAuditResponse,
  pickVisionRoute,
  resolveConfig,
} from '../src/core.js'

test('resolveConfig applies defaults and validates ranges', () => {
  const cfg = resolveConfig({})
  assert.equal(cfg.maxImages, 16)
  assert.equal(cfg.vetoBelow, 30)
  assert.equal(cfg.textWeight, 0.5)
  assert.deepEqual(cfg.routes, [])
  assert.throws(() => resolveConfig({ maxImages: 0 }))
  assert.throws(() => resolveConfig({ maxImages: 21 }))
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

test('buildAuditPrompt mentions count, query and JSON contract', () => {
  const prompt = buildAuditPrompt({ query: '广州市第二中学校服', criteria: 'no collages', count: 8 })
  assert.match(prompt, /8 numbered images/)
  assert.match(prompt, /广州市第二中学校服/)
  assert.match(prompt, /no collages/)
  assert.match(prompt, /JSON array/)
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
      fetchImpl: async () => ({ ok: true, headers: { get: () => 'text/html' } }),
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
