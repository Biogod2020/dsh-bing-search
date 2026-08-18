/**
 * DSH plugin: registers `search_and_audit_images` (integrated search + VLM
 * audit, one call) and `audit_images` (batch audit of caller-supplied
 * candidates).
 *
 * The integrated tool drives the MCP `mcp__web__search_images` tool through
 * `ctx.tools.execute`, so the pure-text ranking stays in dsh-bing-search while
 * the vision audit runs natively in the harness. Vision routes come from the
 * harness's own model configuration (`ctx.llm.listProviders`/`listModels`,
 * validated with `resolveModelInfo` so `inputModalities` includes `image`) —
 * no ports, endpoints, or machine-specific settings are probed. The current
 * session route is tried first. When no vision model exists the search still
 * returns, audited with pure-text scores only — the caller never has to chain
 * two tools.
 * @module dsh-image-audit
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import z from '@deepseek-ai/schemastery'
import { CallId } from '@deepseek-ai/dsh-llm/brand'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { BlockAssembler, createUserMessage } from '@deepseek-ai/dsh-llm'
import {
  applyAuditVerdicts,
  buildAuditPrompt,
  candidatesFromSearch,
  collectVisionRoutes,
  downloadImage,
  enumerateVisionCandidates,
  mergeScores,
  parseAuditResponse,
  persistTopImages,
  pickVisionRoute,
  publicAuditHit,
  publicSearchHit,
  resolveConfig,
} from './core.js'

/** Cordis plugin name used by loader diagnostics. */
export const name = 'image-audit'

/** Services required at registration time. */
export const inject = ['tools']

/** Plugin configuration with defaults. */
export const Config = z.object({
  routes: z
    .array(z.object({ provider: z.string(), model: z.string() }))
    .default([])
    .description('Optional explicit vision-route overrides, tried after the session route and DSH-declared vision models. Usually empty: vision models are auto-detected from the DSH model configuration.'),
  maxImages: z.number().min(1).max(20).default(16),
  maxOutputTokens: z.number().min(64).max(32000).default(32000),
  timeoutMs: z.number().min(1000).max(300000).default(180000),
  vetoBelow: z.number().min(0).max(100).default(30),
  textWeight: z.number().min(0).max(1).default(0.5),
})

/** Download + persist + audit one batch through a single vision call. */
async function auditEntries(ctx, entries, { query, criteria, route, resolved, exec }) {
  const attachments = ctx.get('attachments')
  const results = []
  const imageBlocks = []
  const downloaded = []
  const imageLimit = attachments.imageLimits?.maxImageBytes ?? resolved.downloadMaxBytes
  for (const [i, entry] of entries.entries()) {
    const row = { index: i + 1, url: entry.url, title: entry.title ?? null, purl: entry.purl ?? null }
    if (entry.text_score !== null && entry.text_score !== undefined) row.text_score = entry.text_score
    try {
      const { data, mediaType } = await downloadImage(entry.url, {
        referer: entry.referer || entry.purl || undefined,
        maxBytes: Math.min(resolved.downloadMaxBytes, imageLimit),
        timeoutMs: resolved.downloadTimeoutMs,
      })
      const ref = await attachments.saveImage({ data, mediaType, name: `audit-${i + 1}` })
      row.data = data
      row.mediaType = mediaType
      imageBlocks.push({ type: 'image', attachment: ref })
      downloaded.push(row)
      results.push(row)
    } catch (error) {
      results.push({ ...row, error: error instanceof Error ? error.message : String(error) })
    }
  }
  if (imageBlocks.length === 0) {
    return { status: 'error', reason: 'all_image_downloads_failed', results }
  }

  const prompt = buildAuditPrompt({
    query,
    criteria,
    count: imageBlocks.length,
    images: downloaded,
  })
  const message = createUserMessage({ content: [{ type: 'text', text: prompt }, ...imageBlocks] })
  const assembler = new BlockAssembler()
  const signal =
    typeof AbortSignal.any === 'function'
      ? AbortSignal.any([exec.signal, AbortSignal.timeout(resolved.timeoutMs)])
      : exec.signal
  try {
    for await (const chunk of ctx.get('llm').stream({
      provider: route.provider,
      model: route.model,
      messages: [message],
      maxTokens: resolved.maxOutputTokens,
      signal,
    })) {
      assembler.push(chunk)
    }
  } catch (error) {
    return {
      status: 'error',
      reason: `vision_call_failed: ${error instanceof Error ? error.message : String(error)}`,
      route: `${route.provider}/${route.model}`,
      results,
    }
  }
  const text = assembler
    .blocks()
    .filter(block => block.type === 'text')
    .map(block => block.text)
    .join(' ')
  const parsed = parseAuditResponse(text, imageBlocks.length)
  applyAuditVerdicts(results, parsed)
  for (const row of results) {
    if (row.error || row.vlm_score === undefined) continue
    const merged = mergeScores({
      textScore: row.text_score ?? null,
      vlmScore: row.vlm_score,
      vetoBelow: resolved.vetoBelow,
      textWeight: resolved.textWeight,
    })
    row.final_score = merged.finalScore
    row.vetoed = merged.vetoed
  }
  return { status: 'ok', route: `${route.provider}/${route.model}`, results }
}

/**
 * Ordered vision-route candidates: the current session route first, then every
 * model DSH itself declares as image-capable (enumerated from the harness's own
 * provider/model catalog — no port probing, nothing machine-specific), then the
 * optional explicit `routes` overrides last.
 */
async function visionCandidates(ctx, exec, configuredRoutes = []) {
  const llm = ctx.get('llm')
  const header = exec.agent?.session?.requestHeader?.()?.config
  const sessionRoute =
    header?.provider && header?.model ? { provider: header.provider, model: header.model } : null
  const enumerated = await enumerateVisionCandidates(
    () => llm.listProviders(),
    provider => llm.listModels(provider),
  )
  return collectVisionRoutes(sessionRoute, enumerated, configuredRoutes)
}

function sessionCwd(exec) {
  return exec.agent?.session?.header?.cwd
}

async function persistRanked(rows, { query, keep, exec, resolved }) {
  const persist = await persistTopImages(rows, {
    query,
    cwd: sessionCwd(exec),
    keep,
    writeFile,
    mkdir,
    download: (url, opts) => downloadImage(url, {
      ...opts,
      maxBytes: resolved.downloadMaxBytes,
      timeoutMs: resolved.downloadTimeoutMs,
    }),
  })
  const warnings = persist.warning ? [persist.warning] : []
  return { persist, warnings }
}

function finishSearch(search, ranked, persist, extra = {}) {
  return {
    status: 'ok',
    ...extra,
    ...(search.provider ? { provider: search.provider } : {}),
    ...(persist.savedDir ? { saved_dir: persist.savedDir, saved_count: persist.saved } : { saved_count: persist.saved }),
    warnings: extra.warnings ?? search.warnings,
    results: ranked.map((row, i) => publicSearchHit(row, i + 1)),
  }
}

/** Internal inner search through the MCP tool. */
async function innerSearch(ctx, exec, args) {
  const result = await ctx.tools.execute({
    callId: CallId(`image-audit:${randomUUID()}`),
    name: 'mcp__web__search_images',
    arguments: {
      query: args.query,
      count: args.count ?? 8,
      market: args.market ?? 'en-US',
      provider: args.provider ?? 'auto',
    },
    ...(exec.agent !== undefined ? { agent: exec.agent } : {}),
    signal: exec.signal,
  })
  if (result.isError) {
    const message = result.error?.message ?? 'inner search failed'
    return { status: 'error', reason: `search_failed: ${message}` }
  }
  const search = candidatesFromSearch(result.value)
  if (search.results.length === 0) {
    return {
      status: 'error',
      reason: 'search_returned_no_results',
      ...(search.provider ? { provider: search.provider } : {}),
    }
  }
  return {
    status: 'ok',
    ...(search.provider ? { provider: search.provider } : {}),
    warnings: search.warnings,
    results: search.results,
  }
}

export function apply(ctx, config = {}) {
  const resolved = resolveConfig(config)

  ctx.tools.register(defineTool({
    name: 'search_and_audit_images',
    description:
      'Search images and audit the results with a vision-capable model in one call. ' +
      'Internally runs mcp__web__search_images, then (when the DSH configuration declares a ' +
      'vision-capable model) audits the top candidates with that model and returns a final ranking ' +
      'merged from the pure-text score and the VLM verdict. With no vision model in the DSH config ' +
      'the same call returns the pure-text ranking (audit: "text_only"). ' +
      'The top 3 ranked images (keep, default 3) are written under tmp/image_audit/<query>/ in the session workspace. ' +
      'Prefer a compact query of concrete nouns, but keep the qualifier that uniquely identifies the subject: ' +
      '"复旦光华楼" is better than "光华楼" (the extra place/institution is necessary, not padding). ' +
      'Do not write whole sentences. If a compact query is still ambiguous or hits the wrong entity, write more — ' +
      'place, institution, year, or type. ' +
      'All candidates are audited in a single request.',
    parameters: {
      query: { type: 'string', required: true, description: 'Compact concrete nouns plus the qualifier that makes the subject unique. "复旦光华楼" beats "光华楼". Do not write sentences; if still ambiguous, add more words (place, institution, year, type).' },
      count: { type: 'integer', default: 8, description: 'Number of candidates to search and audit (default 8; clamped to 1..maxImages).' },
      keep: { type: 'integer', default: 3, description: 'How many top-ranked images to save locally (default 3).' },
      market: { type: 'string', default: 'en-US', description: 'Locale such as en-US or zh-CN.' },
      provider: { type: 'string', enum: ['auto', 'bing_images', 'commons'], default: 'auto' },
      criteria: { type: 'string', description: 'Optional extra audit criteria.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          status: { type: 'string', enum: ['ok', 'error'], required: true },
          reason: { type: 'string' },
          audit: { type: 'string', enum: ['vlm', 'text_only', 'vlm_failed', 'error'] },
          route: { type: 'string' },
          provider: { type: 'string' },
          expanded: { type: 'boolean' },
          saved_dir: { type: 'string' },
          saved_count: { type: 'integer' },
          warnings: { type: 'array', items: { type: 'string' } },
          results: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                rank: { type: 'integer', required: true },
                url: { type: 'string', required: true },
                title: { type: 'string' },
                purl: { type: 'string' },
                text_score: { type: 'integer' },
                vlm_score: { type: 'integer' },
                final_score: { type: 'integer' },
                accept: { type: 'boolean' },
                vetoed: { type: 'boolean' },
                reasons: { type: 'array', items: { type: 'string' } },
                local_path: { type: 'string' },
                error: { type: 'string' },
              },
            },
          },
        },
      },
      render: (_args, value) => {
        const lines = [`search_and_audit_images: audit=${value.audit}${value.route ? ` via ${value.route}` : ''}`]
        if (value.saved_dir) lines.push(`saved ${value.saved_count ?? 0} file(s) under ${value.saved_dir}`)
        for (const item of value.results ?? []) {
          if (item.error) lines.push(`#${item.rank} ERROR ${item.error}`)
          else {
            lines.push(
              `#${item.rank} final=${item.final_score} text=${item.text_score ?? '-'} vlm=${item.vlm_score ?? '-'}` +
                `${item.vetoed ? ' VETOED' : ''} ${item.accept ? 'accept' : 'reject'}` +
                `${item.local_path ? ` saved=${item.local_path}` : ''} :: ${(item.reasons ?? []).join('; ')}`
            )
          }
        }
        return [{ type: 'text', text: lines.join('\n') }]
      },
    },
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      const llm = ctx.get('llm')
      const keep = Number.isInteger(args.keep) ? Math.min(Math.max(args.keep, 1), resolved.maxImages) : 3
      if (!llm || !ctx.get('attachments')) {
        // Vision layer unavailable: still deliver the pure-text ranking and save top images.
        const search = await innerSearch(ctx, exec, args)
        if (search.status !== 'ok') return search
        const ranked = search.results.map(row => ({ ...row, final_score: row.text_score }))
        const { persist, warnings } = await persistRanked(ranked, { query: args.query, keep, exec, resolved })
        return finishSearch(search, ranked, persist, {
          audit: 'text_only',
          reason: 'llm or attachments service not mounted',
          warnings: [...(search.warnings ?? []), ...warnings],
        })
      }

      const route = await pickVisionRoute(await visionCandidates(ctx, exec, resolved.routes), (provider, model) =>
        llm.resolveModelInfo(provider, model, exec.signal)
      )

      const search = await innerSearch(ctx, exec, args)
      if (search.status !== 'ok') return search
      if (!route) {
        const ranked = search.results.map(row => ({ ...row, final_score: row.text_score }))
        const { persist, warnings } = await persistRanked(ranked, { query: args.query, keep, exec, resolved })
        return finishSearch(search, ranked, persist, {
          audit: 'text_only',
          reason: 'no_vision_model_in_dsh_config; pure-text scores only',
          warnings: [...(search.warnings ?? []), ...warnings],
        })
      }

      const requested = Number.isInteger(args.count) ? args.count : 8
      const cap = Math.min(Math.max(requested, 1), resolved.maxImages)
      let audited = await auditEntries(ctx, search.results.slice(0, cap), {
        query: args.query,
        criteria: args.criteria,
        route,
        resolved,
        exec,
      })
      let expanded = false
      if (
        audited.status === 'ok'
        && cap < resolved.maxImages
        && !audited.results.some(row => row.accept && !row.vetoed)
      ) {
        const retry = await innerSearch(ctx, exec, { ...args, count: Math.min(cap * 2, resolved.maxImages) })
        if (retry.status === 'ok') {
          const retried = await auditEntries(ctx, retry.results.slice(0, Math.min(cap * 2, resolved.maxImages)), {
            query: args.query,
            criteria: args.criteria,
            route,
            resolved,
            exec,
          })
          if (retried.status === 'ok' && retried.results.some(row => row.accept && !row.vetoed)) {
            audited = retried
            expanded = true
          }
        }
      }
      const ranked = (audited.results ?? [])
        .map(row => ({ ...row, final_score: row.final_score ?? row.text_score ?? 0 }))
        .sort((a, b) => (b.final_score ?? 0) - (a.final_score ?? 0))
      const { persist, warnings } = await persistRanked(ranked, { query: args.query, keep, exec, resolved })
      const anyVerdict = ranked.some(row => row.vlm_score !== undefined)
      return finishSearch(search, ranked, persist, {
        audit: audited.status === 'ok' && anyVerdict ? 'vlm' : 'vlm_failed',
        ...(audited.status === 'ok' ? { route: audited.route } : { reason: audited.reason }),
        expanded,
        warnings: [...(search.warnings ?? []), ...warnings],
      })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'audit_images',
    description:
      'Audit a batch of image candidates (max ' + resolved.maxImages + ') with a vision-capable model and return ' +
      'per-image accept/veto/scores. Uses the current session route when it can read images, else any vision model ' +
      'the DSH configuration declares; returns status "unavailable" when no vision model exists — then fall back to ' +
      'pure-text scores.',
    parameters: {
      query: { type: 'string', description: 'The search query these images were retrieved for.' },
      criteria: { type: 'string', description: 'Optional extra audit criteria.' },
      images: {
        type: 'array',
        required: true,
        description: 'Image candidates: {url, title?, purl?, text_score?, referer?}. At least one, at most maxImages.',
        items: {
          type: 'object',
          additionalProperties: true,
          properties: {
            url: { type: 'string', required: true },
            title: { type: 'string' },
            purl: { type: 'string' },
            text_score: { type: 'number' },
            referer: { type: 'string' },
          },
        },
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          status: { type: 'string', enum: ['ok', 'unavailable', 'error'], required: true },
          reason: { type: 'string' },
          route: { type: 'string' },
          results: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                index: { type: 'integer', required: true },
                url: { type: 'string', required: true },
                title: { type: 'string' },
                purl: { type: 'string' },
                text_score: { type: 'integer' },
                vlm_score: { type: 'integer' },
                final_score: { type: 'integer' },
                accept: { type: 'boolean' },
                vetoed: { type: 'boolean' },
                reasons: { type: 'array', items: { type: 'string' } },
                error: { type: 'string' },
              },
            },
          },
        },
      },
      render: (_args, value) => {
        if (value.status !== 'ok') {
          return [{ type: 'text', text: `audit_images: ${value.status}${value.reason ? ` (${value.reason})` : ''}` }]
        }
        const lines = [`audit_images via ${value.route}`]
        for (const item of value.results ?? []) {
          if (item.error) lines.push(`#${item.index} ERROR ${item.error}`)
          else {
            lines.push(
              `#${item.index} final=${item.final_score} vlm=${item.vlm_score} text=${item.text_score ?? '-'}` +
                `${item.vetoed ? ' VETOED' : ''} ${item.accept ? 'accept' : 'reject'} :: ${(item.reasons ?? []).join('; ')}`
            )
          }
        }
        return [{ type: 'text', text: lines.join('\n') }]
      },
    },
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      const llm = ctx.get('llm')
      const attachments = ctx.get('attachments')
      if (!llm || !attachments) {
        return { status: 'unavailable', reason: 'llm or attachments service not mounted' }
      }
      const route = await pickVisionRoute(await visionCandidates(ctx, exec, resolved.routes), (provider, model) =>
        llm.resolveModelInfo(provider, model, exec.signal)
      )
      if (!route) {
        return { status: 'unavailable', reason: 'no vision model declared in the DSH config; fall back to pure-text scores' }
      }
      const images = Array.isArray(args.images) ? args.images.slice(0, resolved.maxImages) : []
      if (images.length === 0) {
        return { status: 'error', reason: 'images must contain at least one candidate' }
      }
      const audited = await auditEntries(ctx, images, {
        query: args.query ?? '',
        criteria: args.criteria,
        route,
        resolved,
        exec,
      })
      return {
        ...audited,
        results: (audited.results ?? []).map(publicAuditHit),
      }
    },
  }))
}
