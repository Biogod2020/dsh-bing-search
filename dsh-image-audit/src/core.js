/**
 * Pure logic for the image audit tool: configuration validation, audit-prompt
 * construction, response parsing, score merging, vision-route picking, and
 * image downloading. No harness imports — unit-testable anywhere.
 * @module dsh-image-audit/src/core
 */

export const DEFAULT_MAX_IMAGES = 16
export const DEFAULT_MAX_OUTPUT_TOKENS = 2000
export const DEFAULT_TIMEOUT_MS = 45000
export const DEFAULT_VETO_BELOW = 30
export const DEFAULT_TEXT_WEIGHT = 0.5
export const DEFAULT_DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
export const DEFAULT_DOWNLOAD_TIMEOUT_MS = 20000

const IMAGE_MEDIA_TYPES = new Map([
  ['image/jpeg', 'image/jpeg'],
  ['image/png', 'image/png'],
  ['image/webp', 'image/webp'],
  ['image/gif', 'image/gif'],
])

export function resolveConfig(input = {}) {
  if (input === null || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('dsh-image-audit: config must be an object')
  }
  const routes = []
  for (const entry of input.routes ?? []) {
    const provider = typeof entry === 'string' ? entry.split('/')[0] : entry?.provider
    const model = typeof entry === 'string' ? entry.split('/')[1] : entry?.model
    if (typeof provider === 'string' && provider.length > 0 && typeof model === 'string' && model.length > 0) {
      routes.push({ provider, model })
    }
  }
  const maxImages = input.maxImages ?? DEFAULT_MAX_IMAGES
  if (!Number.isInteger(maxImages) || maxImages < 1 || maxImages > 20) {
    throw new Error('dsh-image-audit: maxImages must be an integer in 1..20')
  }
  const maxOutputTokens = input.maxOutputTokens ?? DEFAULT_MAX_OUTPUT_TOKENS
  if (!Number.isInteger(maxOutputTokens) || maxOutputTokens < 64 || maxOutputTokens > 16000) {
    throw new Error('dsh-image-audit: maxOutputTokens must be an integer in 64..16000')
  }
  const timeoutMs = input.timeoutMs ?? DEFAULT_TIMEOUT_MS
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 300000) {
    throw new Error('dsh-image-audit: timeoutMs must be an integer in 1000..300000')
  }
  const vetoBelow = input.vetoBelow ?? DEFAULT_VETO_BELOW
  if (!Number.isFinite(vetoBelow) || vetoBelow < 0 || vetoBelow > 100) {
    throw new Error('dsh-image-audit: vetoBelow must be a number in 0..100')
  }
  const textWeight = input.textWeight ?? DEFAULT_TEXT_WEIGHT
  if (!Number.isFinite(textWeight) || textWeight < 0 || textWeight > 1) {
    throw new Error('dsh-image-audit: textWeight must be a number in 0..1')
  }
  return {
    routes,
    maxImages,
    maxOutputTokens,
    timeoutMs,
    vetoBelow,
    textWeight,
    downloadMaxBytes: input.downloadMaxBytes ?? DEFAULT_DOWNLOAD_MAX_BYTES,
    downloadTimeoutMs: input.downloadTimeoutMs ?? DEFAULT_DOWNLOAD_TIMEOUT_MS,
  }
}

export function buildAuditPrompt({ query = '', criteria = '', count }) {
  const queryLine = query ? `Search query: ${query}` : 'Search query: (not provided)'
  const criteriaLine = criteria ? `Extra criteria: ${criteria}` : ''
  return [
    'You are auditing images for a search. ' +
      `There are ${count} numbered images (index 1..${count}).`,
    queryLine,
    criteriaLine,
    'For EACH image, judge: relevance to the search query, clarity/resolution, ' +
      'technical or factual accuracy where it matters, and absence of watermarks, ' +
      'collage grids, or unrelated content. ' +
      'Return ONLY a JSON array, no prose, no markdown fences: ' +
      '[{"index":1,"accept":true,"score":85,"reasons":["..."]}, ...] ' +
      'where score is 0-100 and accept is false when the image is clearly wrong, ' +
      'low quality, or unsafe. Use Chinese or English for reasons to match the query language.',
  ]
    .filter(Boolean)
    .join('\n')
}

export function parseAuditResponse(text, count) {
  if (typeof text !== 'string' || text.length === 0) return []
  let cleaned = text.trim()
  const fenced = cleaned.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fenced) cleaned = fenced[1].trim()
  const start = cleaned.indexOf('[')
  const end = cleaned.lastIndexOf(']')
  if (start === -1 || end <= start) return []
  let parsed
  try {
    parsed = JSON.parse(cleaned.slice(start, end + 1))
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []
  const out = []
  for (const item of parsed) {
    const index = Number(item?.index)
    if (!Number.isInteger(index) || index < 1 || index > count) continue
    const score = Number(item?.score)
    out.push({
      index,
      accept: Boolean(item?.accept),
      score: Number.isFinite(score) ? Math.max(0, Math.min(100, Math.round(score))) : 0,
      reasons: Array.isArray(item?.reasons) ? item.reasons.map(String).slice(0, 6) : [],
    })
  }
  return out
}

export function mergeScores({ textScore = null, vlmScore, vetoBelow = DEFAULT_VETO_BELOW, textWeight = DEFAULT_TEXT_WEIGHT }) {
  const vlm = Math.max(0, Math.min(100, Math.round(Number(vlmScore) || 0)))
  const vetoed = vlm < vetoBelow
  if (textScore === null || textScore === undefined) {
    return { finalScore: vetoed ? vlm : vlm, vetoed }
  }
  const text = Math.max(0, Math.min(100, Math.round(Number(textScore) || 0)))
  if (vetoed) return { finalScore: Math.min(vlm, text), vetoed }
  return { finalScore: Math.round(textWeight * text + (1 - textWeight) * vlm), vetoed }
}

/**
 * Enumerate DSH's own configured models that declare image input — the
 * providers/models the harness already knows about (settings, not ports).
 * No port probing: a vision model is "known" only when DSH's model catalog
 * declares `inputModalities` including `image`.
 * @param {() => Promise<Array<{id: string} | string>>} listProviders - llm.listProviders
 * @param {(provider: string) => Promise<Array<{id: string, inputModalities?: readonly string[]} | string>>} listModels - llm.listModels
 * @returns {Promise<Array<{provider: string, model: string, name?: string}>>} deduped candidates
 */
export async function enumerateVisionCandidates(listProviders, listModels) {
  const out = []
  const seen = new Set()
  if (typeof listProviders !== 'function' || typeof listModels !== 'function') return out
  let providers
  try {
    providers = await listProviders()
  } catch {
    return out
  }
  for (const provider of providers ?? []) {
    const providerId = typeof provider === 'string' ? provider : provider?.id
    if (!providerId) continue
    let models
    try {
      models = (await listModels(providerId)) ?? []
    } catch {
      continue // a provider may be configured without a reachable catalog
    }
    for (const model of models) {
      const id = typeof model === 'string' ? model : model?.id
      if (!id) continue
      if (!model?.inputModalities?.includes('image')) continue
      const key = `${providerId}/${id}`
      if (seen.has(key)) continue
      seen.add(key)
      out.push({ provider: providerId, model: id, ...(model?.name ? { name: model.name } : {}) })
    }
  }
  return out
}

export async function pickVisionRoute(candidates, resolveModelInfo) {
  const seen = new Set()
  for (const candidate of candidates ?? []) {
    if (!candidate || typeof candidate.provider !== 'string' || typeof candidate.model !== 'string') continue
    const key = `${candidate.provider}/${candidate.model}`
    if (seen.has(key)) continue
    seen.add(key)
    try {
      const info = await resolveModelInfo(candidate.provider, candidate.model)
      if (info?.inputModalities?.includes('image')) {
        return { provider: candidate.provider, model: candidate.model, ...info }
      }
    } catch {
      // misconfigured or unreachable route: skip to the next candidate
    }
  }
  return null
}

export async function downloadImage(url, { referer, timeoutMs = DEFAULT_DOWNLOAD_TIMEOUT_MS, maxBytes = DEFAULT_DOWNLOAD_MAX_BYTES, fetchImpl = fetch } = {}) {
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) {
    throw new Error('invalid url')
  }
  const headers = {
    'User-Agent': 'dsh-image-audit/0.1 (MCP-adjacent audit tool)',
    'Accept': 'image/*,*/*;q=0.5',
  }
  if (referer) headers.Referer = referer
  const response = await fetchImpl(url, {
    headers,
    redirect: 'follow',
    signal: AbortSignal.timeout(timeoutMs),
  })
  if (!response.ok) throw new Error(`http_${response.status}`)
  const mediaType = IMAGE_MEDIA_TYPES.get((response.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase())
  if (!mediaType) throw new Error(`unsupported_content_type: ${response.headers.get('content-type')}`)
  const data = new Uint8Array(await response.arrayBuffer())
  if (data.byteLength > maxBytes) throw new Error('too_large')
  if (data.byteLength === 0) throw new Error('empty_body')
  return { data, mediaType }
}
