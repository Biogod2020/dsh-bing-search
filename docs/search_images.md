# search_images — pure-text image search

`mcp__web__search_images` finds and **ranks** images so that models without
vision can still pick a usable image. Ranking is 100% text-based and
explainable; no image bytes are inspected.

## Providers

| provider | source | notes |
|---|---|---|
| `auto` (default) | Bing Images first, Commons fallback | returns the Bing set when its top score is >= 40, else the better of the two (fallback is flagged in `warnings`) |
| `bing_images` | `<a class="iusc" m="...">` metadata on `bing.com/images/search` | original URL (`murl`), thumbnail (`turl`), source page (`purl`), md5, text title; `cc` country code derived from the market (`en-US` -> `US`) |
| `commons` | Wikimedia Commons `action=query&generator=search` API | curated, licence-clear; returns width/height/mime |

| env var | default | effect |
|---|---|---|
| `DSH_BING_IMAGES_CC` | empty | overrides the Bing Images country code (else derived from the market) |

## Score (0–100)

- Query overlap (0–55): how many query tokens appear in title/purl/murl.
  Tokens are Latin words (≥3 chars, stopwords dropped) **plus CJK bigrams**,
  so `广州市第二中学校服` still matches a title like `广州二中校服`.
- Title evidence: +20 when the exact (collapsed) query appears in the title;
  +10 for ≥2 matched tokens at ≥50% coverage; −15 when the title is missing.
- Domain reputation: +15 good / −25 bad / 0 neutral, applied to the source
  page host (`purl`). Suffixes `.gov.cn`, `.edu.cn`, `.wikimedia.org`,
  `.wikipedia.org` count as good. Explicit lists are configurable:

| env var | default |
|---|---|
| `DSH_IMAGE_GOOD_DOMAINS` | `wenmiyuan.com` (verified by the project owner) |
| `DSH_IMAGE_BAD_DOMAINS` | `dashangu.com` (aggregator pages mix unrelated schools) |

Every result carries `signals` (e.g. `query_tokens_matched: 二中,校服`,
`domain_reputation: good (www.wenmiyuan.com)`) so the caller can audit the rank.

## Guidance for the agent

- Prefer the highest `score`; treat scores below **40** as unverified
  (`top_score_low` warning is added in that case).
- With the default `provider="auto"` a weak Bing result set automatically
  falls back to Wikimedia Commons (`auto_fallback` warning explains why), so
  one call is enough for a non-vision model.
- For Chinese long-tail queries, `bing_images` titles are usually rich; if the
  top score is low, refine the query or try `provider="commons"`.
- Before downloading, verify with `find`/`open` on the source page (`purl`)
  when the topic matters.
