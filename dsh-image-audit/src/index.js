/**
 * DSH plugin: registers `search_and_audit_images` (integrated search + VLM
 * audit, one call) and `audit_images` (batch audit of caller-supplied
 * candidates).
 *
 * The integrated tool drives the MCP `mcp__web__search_images` tool through
 * `ctx.tools.execute`, so the pure-text ranking stays in dsh-bing-search while
 * the vision audit runs natively in the harness. Vision routes are detected
 * with `ctx.llm.resolveModelInfo` (`inputModalities` includes `image`): the
 * current session route first, then the configured `routes`. When no vision
 * route exists the search still returns, audited with pure-text scores only —
 * the caller never has to chain two tools.
 * @module dsh-image-audit
 */

import { randomUUID } from 'node:crypto'
import z from '@deepseek-ai/schemastery'
import { CallId } from '@deepseek-ai/dsh-llm/brand'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { BlockAssembler, createUserMessage } from '@deepseek-ai/dsh-llm'
import {
  buildAuditPrompt,
  downloadImage,
  mergeScores,
  parseAuditResponse,
  pickVisionRoute,
  resolveConfig,
} from './core.js'

/** Cordis plugin name used by loader diagnostics. */
export const name = 'image-audit'

/** Services required at registration time. */
export const inject = ['tools']

/** Plugin configuration with defaults. */
export const Config = z.object({
  routes: z.array(z.object({ provider: z.string(), model: z.string() })).default([]),
  maxImages: z.number().min(1).max(20).default(16),
  maxOutputTokens: z.number().min(64).max(16000).default(2000),
  timeoutMs: z.number().min(1000).max(300000).default(45000),
  vetoBelow: z.number().min(0).max(100).default(30),
  textWeight: z.number().min(0).max(1).default(0.5),
})

/** Map the inner MCP search result to a normalized candidate list. */
function candidatesFromSearch(value) {
  if (!value || typeof value !== 'object') return { provider: null, results: [], warnings: [] }
  const results = Array.isArray(value.results) ? value.results : []
  return {
    provider: typeof value.provider === 'string' ? value.provider : null,
    warnings: Array.isArray(value.warnings) ? value.warnings : [],
    results: results
      .filter(item => item && typeof item.murl === 'string')
      .map(item => ({
        url: item.murl,
        title: item.title ?? null,
        purl: item.purl ?? null,
        text_score: Number.isFinite(Number(item.score)) ? Math.round(Number(item.score)) : null,
      })),
  }
}

/** Download + persist + audit one batch through a single vision call. */
async function auditEntries(ctx, entries, { query, criteria, route, resolved, exec }) {
  const attachments = ctx.get('attachments')
  const results = []
  const imageBlocks = []
  const imageLimit = attachments.imageLimits?.maxImageBytes ?? resolved.downloadMaxBytes
  for (const [i, entry] of entries.entries()) {
    const row = { index: i + 1, url: entry.url, title: entry.title ?? null, purl: entry.purl ?? null }
    if (entry.text_score !== null && entry.text_score !== undefined) row.text_score = entry.text_score
    try {
      const { data, mediaType } = await downloadImage(entry.url, {
        referer: entry.referer,
        maxBytes: Math.min(resolved.downloadMaxBytes, imageLimit),
        timeoutMs: resolved.downloadTimeoutMs,
      })
      const ref = await attachments.saveImage({ data, mediaType, name: `audit-${i + 1}` })
      imageBlocks.push({ type: 'image', attachment: ref })
      results.push(row)
    } catch (error) {
      results.push({ ...row, error: error instanceof Error ? error.message : String(error) })
    }
  }
  if (imageBlocks.length === 0) {
    return { status: 'error', reason: 'all_image_downloads_failed', results }
  }

  const prompt = buildAuditPrompt({ query, criteria, count: imageBlocks.length })
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
  for (const row of results) {
    if (row.error) continue
    const verdict = parsed.find(item => item.index === row.index)
    if (!verdict) {
      row.error = 'no_audit_entry'
      continue
    }
    row.vlm_score = verdict.score
    row.accept = verdict.accept
    row.reasons = verdict.reasons
    const merged = mergeScores({
      textScore: row.text_score ?? null,
      vlmScore: verdict.score,
      vetoBelow: resolved.vetoBelow,
      textWeight: resolved.textWeight,
    })
    row.final_score = merged.finalScore
    row.vetoed = merged.vetoed
  }
  return { status: 'ok', route: `${route.provider}/${route.model}`, results }
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
    return { status: 'error', reason: 'search_returned_no_results', provider: search.provider }
  }
  return { status: 'ok', provider: search.provider, warnings: search.warnings, results: search.results }
}

export function apply(ctx, config = {}) {
  const resolved = resolveConfig(config)

  ctx.tools.register(defineTool({
    name: 'search_and_audit_images',
    description:
      'Search images and audit the results with a vision-capable model in one call. ' +
      'Internally runs mcp__web__search_images, then (when a vision route exists) audits the top ' +
      'candidates with the vision model and returns a final ranking merged from the pure-text score ' +
      'and the VLM verdict. With no vision model the same call returns the pure-text ranking ' +
      '(audit: "text_only"). All candidates are audited in a single request.',
    parameters: {
      query: { type: 'string', required: true, description: 'What the image should depict.' },
      count: { type: 'integer', min: 1, max: 20, default: 8, description: 'Number of candidates to search and audit (default 8; expands once to up to maxImages when everything is vetoed).' },
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
                error: { type: 'string' },
              },
            },
          },
        },
      },
      render: (_args, value) => {
        const lines = [`search_and_audit_images: audit=${value.audit}${value.route ? ` via ${value.route}` : ''}`]
        for (const item of value.results ?? []) {
          if (item.error) lines.push(`#${item.rank} ERROR ${item.error}`)
          else {
            lines.push(
              `#${item.rank} final=${item.final_score} text=${item.text_score ?? '-'} vlm=${item.vlm_score ?? '-'}` +
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
      if (!llm || !ctx.get('attachments')) {
        // Vision layer unavailable: still deliver the pure-text ranking.
        const search = await innerSearch(ctx, exec, args)
        if (search.status !== 'ok') return search
        return {
          status: 'ok',
          audit: 'text_only',
          reason: 'llm or attachments service not mounted',
          provider: search.provider,
          warnings: search.warnings,
          results: search.results.map((row, i) => ({ rank: i + 1, ...row, final_score: row.text_score })),
        }
      }

      const header = exec.agent?.session?.requestHeader?.()?.config
      const sessionRoute =
        header?.provider && header?.model ? { provider: header.provider, model: header.model } : null
      const route = await pickVisionRoute([sessionRoute, ...resolved.routes], (provider, model) =>
        llm.resolveModelInfo(provider, model, exec.signal)
      )

      const search = await innerSearch(ctx, exec, args)
      if (search.status !== 'ok') return search
      if (!route) {
        return {
          status: 'ok',
          audit: 'text_only',
          reason: 'no_vision_model_route; pure-text scores only',
          provider: search.provider,
          warnings: search.warnings,
          results: search.results.map((row, i) => ({ rank: i + 1, ...row, final_score: row.text_score })),
        }
      }

      const cap = Math.min(args.count ?? 8, resolved.maxImages)
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
        .sort((a, b) => b.final_score - a.final_score)
        .map((row, i) => ({ rank: i + 1, ...row }))
      return {
        status: 'ok',
        audit: audited.status === 'ok' ? 'vlm' : 'vlm_failed',
        ...(audited.status === 'ok' ? { route: audited.route } : { reason: audited.reason }),
        provider: search.provider,
        expanded,
        warnings: search.warnings,
        results: ranked,
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'audit_images',
    description:
      'Audit a batch of image candidates (max ' + resolved.maxImages + ') with a vision-capable model and return ' +
      'per-image accept/veto/scores. Uses the current session route when it can read images, else the configured ' +
      'vision routes; returns status "unavailable" when no vision model exists — then fall back to pure-text scores.',
    parameters: {
      query: { type: 'string', description: 'The search query these images were retrieved for.' },
      criteria: { type: 'string', description: 'Optional extra audit criteria.' },
      images: {
        type: 'array',
        required: true,
        minItems: 1,
        maxItems: resolved.maxImages,
        description: 'Image candidates: {url, title?, purl?, text_score?, referer?}.',
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
      const header = exec.agent?.session?.requestHeader?.()?.config
      const sessionRoute =
        header?.provider && header?.model ? { provider: header.provider, model: header.model } : null
      const route = await pickVisionRoute([sessionRoute, ...resolved.routes], (provider, model) =>
        llm.resolveModelInfo(provider, model, exec.signal)
      )
      if (!route) {
        return { status: 'unavailable', reason: 'no_vision_model_route; fall back to pure-text scores' }
      }
      const audited = await auditEntries(ctx, args.images, {
        query: args.query ?? '',
        criteria: args.criteria,
        route,
        resolved,
        exec,
      })
      return audited
    },
  }))
}
