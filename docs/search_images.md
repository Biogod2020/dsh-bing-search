# search_images — pure-text image search

`mcp__web__search_images` finds and **ranks** images so that models without
vision can still pick a usable image. Ranking is 100% text-based and
explainable; no image bytes are inspected.

## Providers

| provider | source | notes |
|---|---|---|
| `bing_images` (default) | `<a class="iusc" m="...">` metadata on `bing.com/images/search` | original URL (`murl`), thumbnail (`turl`), source page (`purl`), md5, text title |
| `commons` | Wikimedia Commons `action=query&generator=search` API | curated, licence-clear; returns width/height/mime |

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
- For Chinese long-tail queries, `bing_images` titles are usually rich; if the
  top score is low, refine the query or try `provider="commons"`.
- Before downloading, verify with `find`/`open` on the source page (`purl`)
  when the topic matters.
