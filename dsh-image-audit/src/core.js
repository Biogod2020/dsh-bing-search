/**
 * Pure logic for the image audit tool: configuration validation, audit-prompt
 * construction, response parsing, score merging, vision-route picking, and
 * image downloading. No harness imports — unit-testable anywhere.
 * @module dsh-image-audit/src/core
 */

export const DEFAULT_MAX_IMAGES = 16
export const DEFAULT_MAX_OUTPUT_TOKENS = 32000
export const DEFAULT_TIMEOUT_MS = 180000
export const DEFAULT_VETO_BELOW = 30
export const DEFAULT_TEXT_WEIGHT = 0.5
export const DEFAULT_DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
export const DEFAULT_DOWNLOAD_TIMEOUT_MS = 20000
export const DEFAULT_PERSIST_COUNT = 3
export const BROWSER_IMAGE_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

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
  if (!Number.isInteger(maxOutputTokens) || maxOutputTokens < 64 || maxOutputTokens > 32000) {
    throw new Error('dsh-image-audit: maxOutputTokens must be an integer in 64..32000')
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

function clip(value, limit) {
  const text = typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : ''
  if (!text) return ''
  return text.length <= limit ? text : `${text.slice(0, limit)}…`
}

/** One numbered metadata block that lines up with the image sent at the same index. */
export function formatImageCaption(image, index) {
  const title = clip(image?.title, 200) || '(none)'
  const url = clip(image?.url, 300) || '(none)'
  const source = clip(image?.purl, 300)
  const score = Number.isFinite(Number(image?.text_score)) ? `${Math.round(Number(image.text_score))}/100` : null
  return [
    `[${index}]`,
    `title: ${title}`,
    `image_url: ${url}`,
    source ? `source_page: ${source}` : '',
    score ? `text_score: ${score}` : '',
  ]
    .filter(Boolean)
    .join('\n')
}

export function buildAuditPrompt({ query = '', criteria = '', count, images = [] }) {
  const n = Number.isInteger(count) && count > 0 ? count : images.length
  const queryLine = query ? `Search query: ${query}` : 'Search query: (not provided)'
  const criteriaLine = criteria ? `Extra criteria: ${criteria}` : ''
  const catalog = images
    .map((image, i) => formatImageCaption(image, i + 1))
    .join('\n')
  return [
    'You are auditing images for a search. ' +
      `There are ${n} numbered images (index 1..${n}). ` +
      'The catalog below is in the same order as the attached images.',
    queryLine,
    criteriaLine,
    catalog ? `Image catalog (same order as the attached images):\n${catalog}` : '',
    'For EACH image, use BOTH the pixels and that image\'s catalog entry ' +
      '(title, image URL, source page, text_score). Metadata is useful evidence ' +
      '(especially a matching title or a reputable source page) but can be SEO-stuffed ' +
      'or wrong — if the pixels contradict the title/URL, trust the pixels. ' +
      'Judge: relevance to the search query, clarity/resolution, ' +
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
    return { finalScore: vlm, vetoed }
  }
  const text = Math.max(0, Math.min(100, Math.round(Number(textScore) || 0)))
  if (vetoed) return { finalScore: Math.min(vlm, text), vetoed }
  return { finalScore: Math.round(textWeight * text + (1 - textWeight) * vlm), vetoed }
}

/**
 * DSH's MCP bridge returns `{ content, structuredContent }`. A direct tool
 * result is the payload itself. Prefer structuredContent, then a top-level
 * payload, then JSON text inside content blocks.
 */
export function unwrapMcpToolValue(value) {
  if (!value || typeof value !== 'object') return null
  if (value.structuredContent && typeof value.structuredContent === 'object' && !Array.isArray(value.structuredContent)) {
    return value.structuredContent
  }
  if (Array.isArray(value.results) || typeof value.provider === 'string' || typeof value.status === 'string') {
    return value
  }
  if (Array.isArray(value.content)) {
    const text = value.content
      .map(block => (block && typeof block.text === 'string' ? block.text : ''))
      .join('\n')
      .trim()
    if (text) {
      try {
        const parsed = JSON.parse(text)
        if (parsed && typeof parsed === 'object') return parsed
      } catch {
        // fall through
      }
    }
  }
  return value
}

/** Normalize an inner search_images result (raw or MCP-wrapped) into candidates. */
export function candidatesFromSearch(value) {
  const payload = unwrapMcpToolValue(value)
  if (!payload || typeof payload !== 'object') return { provider: null, results: [], warnings: [] }
  const results = Array.isArray(payload.results) ? payload.results : []
  return {
    provider: typeof payload.provider === 'string' ? payload.provider : null,
    warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
    results: results
      .filter(item => item && typeof item.murl === 'string' && item.murl.startsWith('http'))
      .map(item => ({
        url: item.murl,
        title: item.title ?? null,
        purl: item.purl ?? null,
        referer: item.referer ?? item.purl ?? null,
        text_score: Number.isFinite(Number(item.score)) ? Math.round(Number(item.score)) : null,
      })),
  }
}

/**
 * Map VLM verdicts onto download rows. The model sees only successful
 * downloads, numbered 1..N in that order — not the original candidate index.
 */
export function applyAuditVerdicts(rows, parsed) {
  let auditIndex = 0
  for (const row of rows) {
    if (row.error) continue
    auditIndex += 1
    const verdict = parsed.find(item => item.index === auditIndex)
    if (!verdict) {
      row.error = 'no_audit_entry'
      continue
    }
    row.vlm_score = verdict.score
    row.accept = verdict.accept
    row.reasons = verdict.reasons
  }
  return rows
}

/** Session route, then DSH-declared vision models, then optional config overrides. */
export function collectVisionRoutes(sessionRoute, enumerated = [], configuredRoutes = []) {
  return [sessionRoute, ...enumerated, ...configuredRoutes].filter(Boolean)
}

function optionalString(value) {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function optionalInt(value) {
  const n = Number(value)
  return Number.isFinite(n) ? Math.round(n) : undefined
}

/**
 * DSH validates tool output with additionalProperties:false and rejects nulls
 * on string/integer fields. Only emit declared keys, omit missing ones.
 */
export function publicSearchHit(row, rank) {
  const hit = { rank, url: row.url }
  const title = optionalString(row.title)
  const purl = optionalString(row.purl)
  const textScore = optionalInt(row.text_score)
  const vlmScore = optionalInt(row.vlm_score)
  const finalScore = optionalInt(row.final_score)
  if (title) hit.title = title
  if (purl) hit.purl = purl
  if (textScore !== undefined) hit.text_score = textScore
  if (vlmScore !== undefined) hit.vlm_score = vlmScore
  if (finalScore !== undefined) hit.final_score = finalScore
  if (typeof row.accept === 'boolean') hit.accept = row.accept
  if (typeof row.vetoed === 'boolean') hit.vetoed = row.vetoed
  if (Array.isArray(row.reasons)) hit.reasons = row.reasons.map(String)
  if (optionalString(row.error)) hit.error = row.error
  const localPath = optionalString(row.local_path)
  if (localPath) hit.local_path = localPath
  return hit
}

export function publicAuditHit(row) {
  const hit = { index: row.index, url: row.url }
  const title = optionalString(row.title)
  const purl = optionalString(row.purl)
  const textScore = optionalInt(row.text_score)
  const vlmScore = optionalInt(row.vlm_score)
  const finalScore = optionalInt(row.final_score)
  if (title) hit.title = title
  if (purl) hit.purl = purl
  if (textScore !== undefined) hit.text_score = textScore
  if (vlmScore !== undefined) hit.vlm_score = vlmScore
  if (finalScore !== undefined) hit.final_score = finalScore
  if (typeof row.accept === 'boolean') hit.accept = row.accept
  if (typeof row.vetoed === 'boolean') hit.vetoed = row.vetoed
  if (Array.isArray(row.reasons)) hit.reasons = row.reasons.map(String)
  if (optionalString(row.error)) hit.error = row.error
  const localPath = optionalString(row.local_path)
  if (localPath) hit.local_path = localPath
  return hit
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

export function browserImageHeaders(referer) {
  const headers = {
    'User-Agent': BROWSER_IMAGE_UA,
    Accept: 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Cache-Control': 'no-cache',
    Pragma: 'no-cache',
    'Sec-Fetch-Dest': 'image',
    'Sec-Fetch-Mode': 'no-cors',
    'Sec-Fetch-Site': 'cross-site',
  }
  if (referer) headers.Referer = referer
  return headers
}

/** Referers to try when a CDN hotlink-blocks the first request. */
export function refererCandidates(url, preferred) {
  const out = []
  const push = value => {
    if (value && !out.includes(value)) out.push(value)
  }
  push(preferred)
  try {
    push(`${new URL(url).origin}/`)
  } catch {
    // ignore
  }
  push('https://www.bing.com/')
  push('https://cn.bing.com/')
  out.push(null)
  return out
}

export function sniffMediaType(bytes, contentType) {
  const declared = IMAGE_MEDIA_TYPES.get((contentType ?? '').split(';')[0].trim().toLowerCase())
  if (declared) return declared
  if (!bytes || bytes.byteLength < 12) return null
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'image/jpeg'
  if (bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return 'image/png'
  if (bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46) return 'image/gif'
  if (
    bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46
    && bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50
  ) return 'image/webp'
  return null
}

export function querySlug(query) {
  const slug = String(query ?? '')
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
  return slug || 'images'
}

export function extensionForMediaType(mediaType) {
  if (mediaType === 'image/jpeg') return '.jpg'
  if (mediaType === 'image/png') return '.png'
  if (mediaType === 'image/webp') return '.webp'
  if (mediaType === 'image/gif') return '.gif'
  return '.img'
}

/** Highest-scoring downloadable rows, already expected in score-desc order. */
export function pickPersistable(rows, limit = DEFAULT_PERSIST_COUNT) {
  const n = Number.isInteger(limit) && limit > 0 ? limit : DEFAULT_PERSIST_COUNT
  return (rows ?? []).filter(row => !row.error && (row.data || row.url)).slice(0, n)
}

async function downloadOnce(url, { referer, timeoutMs, maxBytes, fetchImpl }) {
  const response = await fetchImpl(url, {
    headers: browserImageHeaders(referer),
    redirect: 'follow',
    signal: AbortSignal.timeout(timeoutMs),
  })
  if (!response.ok) throw new Error(`http_${response.status}`)
  const data = new Uint8Array(await response.arrayBuffer())
  if (data.byteLength > maxBytes) throw new Error('too_large')
  if (data.byteLength === 0) throw new Error('empty_body')
  const mediaType = sniffMediaType(data, response.headers.get('content-type'))
  if (!mediaType) throw new Error(`unsupported_content_type: ${response.headers.get('content-type')}`)
  return { data, mediaType }
}

export async function persistTopImages(rows, {
  query,
  cwd,
  keep = DEFAULT_PERSIST_COUNT,
  download = downloadImage,
  writeFile,
  mkdir,
} = {}) {
  if (typeof cwd !== 'string' || cwd.length === 0 || typeof writeFile !== 'function' || typeof mkdir !== 'function') {
    return { savedDir: null, saved: 0, warning: 'no_session_cwd: images were not written to disk' }
  }
  const sorted = [...(rows ?? [])].sort(
    (a, b) => (b.final_score ?? b.text_score ?? 0) - (a.final_score ?? a.text_score ?? 0),
  )
  const picked = pickPersistable(sorted, keep)
  if (picked.length === 0) {
    return { savedDir: null, saved: 0, warning: 'no_downloadable_images_to_save' }
  }
  const dir = `${cwd.replace(/\/+$/, '')}/tmp/image_audit/${querySlug(query)}`
  await mkdir(dir, { recursive: true })
  let saved = 0
  let index = 0
  for (const row of picked) {
    index += 1
    let payload = row.data && row.mediaType ? { data: row.data, mediaType: row.mediaType } : null
    if (!payload && row.url) {
      try {
        payload = await download(row.url, { referer: row.referer || row.purl })
        row.data = payload.data
        row.mediaType = payload.mediaType
      } catch (error) {
        if (!row.error) row.error = error instanceof Error ? error.message : String(error)
        continue
      }
    }
    if (!payload) continue
    const path = `${dir}/${String(index).padStart(2, '0')}${extensionForMediaType(payload.mediaType)}`
    await writeFile(path, payload.data)
    row.local_path = path
    saved += 1
  }
  return {
    savedDir: dir,
    saved,
    warning: saved === 0 ? 'failed_to_save_any_image' : null,
  }
}

export async function downloadImage(url, { referer, timeoutMs = DEFAULT_DOWNLOAD_TIMEOUT_MS, maxBytes = DEFAULT_DOWNLOAD_MAX_BYTES, fetchImpl = fetch } = {}) {
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) {
    throw new Error('invalid url')
  }
  let lastError
  for (const candidate of refererCandidates(url, referer)) {
    try {
      return await downloadOnce(url, { referer: candidate, timeoutMs, maxBytes, fetchImpl })
    } catch (error) {
      lastError = error
      const message = error instanceof Error ? error.message : String(error)
      if (!/^http_(401|403|429|503)/.test(message) && !message.startsWith('unsupported_content_type')) {
        throw error
      }
    }
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError))
}
