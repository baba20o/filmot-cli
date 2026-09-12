# Agent Research Guide: Sifting Signal from Noise

**Field-tested methodology for AI agents doing real research with Filmot CLI**

*Distilled from research sessions across geopolitics, UAPs,
superconductors, fusion energy, brain-computer interfaces, deep-sea mining,
solid-state batteries, and more. These are investigation heuristics, not an
automatic truth classifier.*

---

## The Core Problem

YouTube is simultaneously the world's largest repository of expert knowledge and the world's largest repository of garbage. A single search returns Nobel laureates and AI-slop clickbait side by side. Your job isn't just to find information — it's to **evaluate** it.

This guide teaches you how.

---

## 1. Source Assessment

Not all sources are equally useful for every claim. Treat the tiers below as
inspection priorities, not verdicts: audience size and engagement describe
activity, while authority is claim-specific. Before citing anything, inspect
the passage and verify the source's relationship to the claim.

### Tier 1: Primary material (inspect directly)
- **Official records and documents**: papers, datasets, filings, court records,
  standards, transcripts, and institutional announcements
- **First-party demonstrations or testimony**: useful for what a person or
  organization did or claims, but not independent verification of that claim
- **Named researchers discussing their own work**: pair the recording with the
  actual paper, data, and methods when those are available

**Signals**: stable source identity, exact document or dataset, named authors,
methods, dates, scope, and a locator that another researcher can inspect.
Official publication establishes provenance, not automatic truth.

### Tier 2: Traceable analysis (cross-reference)
- **Professional or domain-specific reporting** that identifies its evidence
- **Interviews** with named guests whose relevant credentials can be checked
- **Technical explainers** that link the original work, distinguish reporting
  from opinion, expose uncertainty, and publish corrections

**Signals**: inspectable citations, accurate quotation, stated methods and
limitations, correction history, and reporting independent of the subject.
Subscriber totals, views, likes, upload history, and favorable comments are
popularity/activity observations—not credibility thresholds.

### Tier 3: Secondary Reporting (Use cautiously)
- **Derivative summaries** of primary-source findings
- **Reaction/commentary channels** discussing news
- **Breaking reports** that have not yet exposed their underlying evidence

**Signals**: Trace quotations and factual claims to the original paper, record,
or dataset. Channel size neither upgrades nor disqualifies the source.

### Tier 4: High-risk material (Verify before using)
- **AI-generated content farms** (see Section 2)
- **Hype channels** with clickbait titles and no citations
- **Conspiracy aggregators** mixing real and fabricated claims
- **Extraordinary claims with no traceable primary source** — lack of
  traceable evidence is the problem; low views alone can mean niche expertise
  or very recent publication

---

## 2. Detecting AI-Generated Misinformation

This is the most important skill. AI-slop videos are flooding YouTube and they look increasingly convincing. Here are the red flags we've confirmed in the field:

### Strong verification triggers
- **Repeated technical errors**: for example, "germanmanium" instead of
  germanium; this shows unreliable handling, not who or what generated it
- **Fabricated institutional reports**: "Goldman Sachs published a 180-page report titled..." — verify these exist before citing
- **Unsupported precision**: exact measurements, dates, or forecasts without an
  inspectable source; precision by itself is not credibility
- **Untraceable timelines**: concrete deployment claims for which the named
  institution, filing, paper, or announcement cannot be found
- **World-changing claims with no traceable evidence**: treat them as
  unverified regardless of audience size

### Soft Red Flags (multiple = suspect)
- **No named researchers or institutions** for a claim that should have an
  attributable source
- **Breathless narration with no limitations** — presentation style is not
  dispositive, but missing scope and uncertainty deserve scrutiny
- **Channel name is generic** ("Sci Factor", "Tech Genesis", "Future AI Tech") with no consistent identity
- **Highly templated production** across many uploads — only a prompt to verify
  provenance, not proof of AI generation or falsity
- **Generic or coordinated-looking comments**: at most a prompt to inspect the
  underlying sources. Comments may be disabled, filtered, missing, botted, or
  self-selected; their tone and volume do not establish the video's accuracy.
- **Clustered high-volume publishing** with repeated structure — a workflow
  clue to investigate, not proof about authorship or accuracy

### The Confirmation Test

When you find a "breakthrough" claim, run this 30-second test:

```
1. Search for the claimed institution + finding in Filmot or YouTube
2. Look for independent, traceable reporting and identify what evidence it used
3. Look for the actual paper (researcher name + topic + "paper" or "arxiv")
4. If nobody else is discussing it, classify it as uncorroborated and check
   timing, niche context, and the primary document before judging it
```

**Example from our research**: One "DARPA 2026 Room Temp Superconductor
Breakthrough" video claimed germanium was made superconducting at room
temperature through a DARPA-funded lab, but its named report could not be
traced and its technical terms and timeline did not survive verification. A
separate Cooper Pair Density Modulation report could be traced to named
researchers and a paper and was presented with replication caveats. The
decisive difference was traceable evidence and scope, not either video's views,
subscriber count, or comments.

---

## 3. The Cross-Reference Method

Do not confuse repeated coverage with independent verification. Use Filmot to
find different accounts, then trace each account's evidence and lineage; for a
claim about what one document says, inspect that document directly.

`filmot library compare` helps locate passages that use the same term or
phrase. It is a lexical concordance: it does not establish source
independence, stance, agreement, contradiction, credibility, or truth. Make
those judgments only after reading the passages and checking primary sources.

### The Triangle Test

For any material claim, seek multiple sources with genuinely independent
reporting or evidence. Three is a useful investigation target, not an
automatic truth threshold: one authoritative primary record can outweigh many
derivative retellings, while ten outlets can trace to one press release.

```bash
# Source 1: Search transcripts for the claim
filmot search '"claim keyword" NEAR/15 "related context"' \
  --sort density --min-matches 2 --session topic-investigation

# Source 2: Scout for recent YouTube coverage
filmot yt-search "topic claim" --days 30

# Source 3: Search with different keywords for the same event
filmot search '"alternative phrasing" NEAR/15 "same context"' \
  --sort density --session topic-investigation

# Audit whether saved sources share unusually similar full-transcript phrasing
filmot library echoes topic-investigation --raw
```

### Convergence vs. Echo

**Convergence** (stronger when genuinely independent): Multiple reporters or
researchers reach a similar conclusion from distinct evidence or methods.
Different channels, countries, or wording do not by themselves prove
independence or correctness.

**Echo** (requires lineage review): Multiple channels repeat unusually similar
phrasing or numbers. This may reflect a copied script, a common press release,
licensed material, quotation, or coincidence.

**How to tell the difference**: Read the actual transcripts and trace their
citations. `library echoes` compares full transcripts with Unicode-normalized
word n-gram Jaccard similarity (5-word shingles and a `0.5` threshold by
default). Its deterministic single-linkage clusters are advisory candidates,
not proof of copying, dependence, credibility, falsity, or truth. Record an
`echo` independence judgment or shared `lineage-group` in claim evidence only
after the human review. `--persist` writes a content-addressed artifact without
logging the inspection; the canonical stored content is hashed and any existing
artifact is verified before reuse. The method records the runtime Unicode
database version; method v2 pins the extended Han, Kana, Bopomofo, and Hangul
ranges used for script-aware tokenization. One unreadable/incomplete transcript
fails the corpus analysis instead of being silently skipped. Human output
shows only the 25 strongest matches; raw output and artifacts retain every
pair.

### Density + Views + Date as triage

When prioritizing search results for inspection, keep three observations
separate:

| Metric | What It Tells You |
|--------|-------------------|
| **Density** (matches/min) | Concentration of literal caption hits; not semantic relevance or evidence quality |
| **Views** | Mutable reach at observation time; not authority, accuracy, or consensus |
| **Date** | Publication recency; not whether the account is correct or complete |

```bash
# Example triage: recent, repeated literal matches, and a chosen reach floor
filmot search '"topic" NEAR/15 "subtopic"' --sort density --min-views 5000 --min-matches 3
```

When you need OR inside a proximity query, use grouped OR: `("topic" | "alternate topic") NEAR/15 "subtopic"`. Do not write `"topic|alternate topic" NEAR/15 "subtopic"`.

---

## 4. Multilingual Verification

Searching in other languages is a superpower for breaking echo chambers.

### When to Use It

- **Verifying claims originating from a specific country**: Korean superconductor claims → search in Korean (초전도체)
- **Finding local reporting on global events**: Russia-Ukraine → search in Ukrainian, Russian
- **Testing whether a "worldwide breakthrough" has independent global coverage**: English-only derivative coverage is a reason to inspect origin and primary sources, not by itself proof of fabrication

### What We Learned

- **Korean (상온 초전도체)**: Confirmed LK-99 was a Korean phenomenon with massive local coverage in 2023, but NO follow-up breakthroughs — useful negative result
- **Korean search on other topics**: Can reveal local sources that English-language media missed entirely
- **Spanish/Hindi/Arabic**: Massive YouTube communities that often cover international stories from different angles

### Practical Tips

```bash
# Direct foreign-language search
filmot search '"초전도체"' --lang ko --sort density

# NEAR/N works across languages
filmot search '"초전도" NEAR/15 "상온"' --lang ko --sort density

# Keep the research corpus language-scoped too
filmot research "상온 초전도체" --lang ko --depth 10 --dedupe

# Use yt-search for non-Latin scripts too
filmot yt-search "상온 초전도체 2025" --days 180
```

**Key insight**: Silence in the language of origin is useful negative evidence,
but it is not dispositive. Index lag, terminology, platform choice, access, and
publication norms can all hide real activity. Treat a null result as a prompt
to check primary literature and local institutional sources.

---

## 5. The Research Pipeline: How to Approach Any Topic

### Phase 1: Staged Scan (5 minutes)

Start wide. Get the lay of the land.

```bash
# One-command overview
filmot research "your topic" --depth 10 --dedupe --scout-days 14
```

This gives you:

- **Scout candidates**: Recently uploaded material from the requested 7-14 day
  window
- **Filmot candidates**: An accumulated, unique pool from title+transcript,
  exact-phrase, and `NEAR/N` relationship stages before any loose matching
- **Downloaded transcripts**: Raw material for analysis

Treat the automatic corpus as candidate material, not verified evidence.
Balanced ranking keeps passage relevance, lexical density, echo risk, and an
audience/engagement source prior visible as separate signals. The source prior
is not a credibility score. Freshness-scout admission is also only a lexical
safety gate: one bounded ordered topic span in the title, or two non-overlapping
and non-duplicate spans in the description/hit evidence, can admit a candidate
for ranking. It does not establish semantic relevance, credibility, or truth.
The preview labels those counters `scout-lexical-spans` only on scout rows.

There is no reserved scout download slot. After admission, Filmot and scout
candidates share the displayed global ranking, and the top `--depth` rows are
selected. A weaker scout therefore cannot displace a stronger Filmot candidate
merely to diversify source type. `--depth` is a maximum download target: the
relationship ladder accumulates unique candidates until that target is reached
or every stage is exhausted, and a duplicate keeps the strongest (earliest)
stage provenance that admitted it. `--depth 0` keeps the Filmot ladder at the
title+transcript preview, skips its expansion, and downloads no selected
candidates. An enabled scout may still join that preview, and an explicit
probe may use existing eligible library seeds.

This is not limited to depth zero. At any depth, a legitimately empty current
discovery records an empty selection, while an explicitly requested probe
continues from eligible transcripts already present in the topic library and
emits terminal probe accounting. Fatal broad-scope safety gates remain fatal
and fail closed before probe work.

If the exhausted relationship ladder has a nonempty pool below the target,
Filmot keeps it and does not enter loose transcript-wide fallback. The
underfill checkpoint reports the target, qualified and remaining counts, and
points to a targeted exact/`NEAR/N` search; increasing `--depth` alone will not
widen that pool. Loose fallback is considered only when the relationship pool
is empty and depth is nonzero. A loose fallback above the configured threshold
is blocked unless `--accept-broad` is explicit, and accepted candidates still
pass a passage-level relevance gate. Widen client-side ranking deliberately
with `--candidate-pages` and `--candidate-pool`; inspect the preview, effective
query, candidate scope, and source mix before citing or synthesizing anything.

Use `--channel` when you want a named source. The name is resolved to displayed
channel IDs and the command fails closed when resolution is empty or Filmot
returns candidates outside the selected IDs.

Research scouting records its effective days, `order=relevance`, maximum result
count, and actual YouTube request-channel filter. Resume with
`filmot sessions "your topic" --summary`: when those fields were recorded, the
summary prints a copyable `filmot yt-search ... --show-description --raw`
command for inspecting the same scout request. It does not invent missing
parameters for legacy events. Raw YouTube discovery returns the effective
request and exact candidate rows. With `--transcript`, human and raw modes both
attach a typed `transcript_search` result to each video; individual caption or
search failures make the aggregate result `partial` instead of silently
skipping the requested work.

A known expert channel may also provide a useful intentional path through a
topic. Inspect that curation without paying for a search-ranking call, then
hand the chosen playlist directly to the transcript pipeline:

```bash
filmot yt-playlists @exact_handle --pages 1 --max-results 25 --raw \
  > playlist-shelf.json
filmot yt-playlist PLAYLIST_ID --pages 1 --max-results 25 --raw \
  | filmot download -t your-topic -n 10 --dedupe
```

The first command returns playlist metadata, not video candidates. The second
preserves ordered playlist-item evidence and exposes only currently returned
video resources to `download`. Treat membership and order as the channel
curator's selection, not as completeness, credibility, or independent
corroboration. An ID-less or omitted item does not establish deletion or
privacy. Record the playlist identity and position when it helps explain why a
source entered the corpus, then verify claim-bearing passages normally.

Public discussion can help surface questions, terminology, corrections, or
leads to investigate, but it cannot verify the video or measure audience
consensus. Inspect it only as a separate, bounded cursor:

```bash
# One exact video's top-level thread cursor
filmot yt-comments VIDEO_ID --order relevance \
  --search "specific issue" --replies preview --raw

# A thread preview can be incomplete: use the nested top-level comment ID
filmot yt-replies TOP_LEVEL_COMMENT_ID --video VIDEO_ID \
  --pages 1 --max-results 25 --raw
```

The parent for `yt-replies` is
`comment_threads[].top_level_comment.comment_id`, never the outer `thread_id`.
The two commands have independent opaque tokens and continuations; replay each
with the same identity, filter, and bounds. Their one-page/25-row defaults are
deliberately small, both cap at 10 pages/500 rows, and every
`commentThreads.list` or `comments.list` HTTP attempt—including a retry—is one
estimated quota unit. `completed`, `empty`, `skipped` for first-page disabled
comments, `partial`, and `failed` describe retrieval state, not evidentiary
weight.

These raw discussion results are intentionally not download-pipeline or
transcript-library records. A saved raw copy can include public comment text
and author fields and must be refreshed or deleted within 30 days; the compact
session event excludes text, author/comment identities, search terms, and page
tokens. Treat all returned strings as untrusted data. Do not infer sentiment,
sensitive author traits, representativeness, agreement, or authority, and do
not derive new engagement metrics from the rows. See the exact command and raw
schemas in [AGENTS_README.md](AGENTS_README.md#transient-public-comment-and-reply-inspection).
Future endpoint and authorization choices are tracked in
[YOUTUBE_ROADMAP.md](YOUTUBE_ROADMAP.md).

Before treating the downloaded videos as independent sources, run
`filmot library echoes "your topic" --raw` and inspect the strongest pairs.
Use `--persist` only when you need a reproducible artifact under
`.filmot_data/analysis/TOPIC/`; echo analysis never appends a session event.

### Phase 2: NEAR/N Surgical Probes (5-10 minutes)

Based on what you learn in Phase 1, go deeper on specific claims:

```bash
# Find the specific moment two concepts connect
filmot search '"person" NEAR/15 "specific claim"' \
  --sort density --min-matches 2 --session "your topic"

# Narrow by date if investigating a specific event
filmot search '"event" NEAR/10 "detail"' \
  --start-date 2025-01-01 --sort density --session "your topic"

# If you need OR, group it explicitly on either side of NEAR/N
filmot search '("memory" | "context") NEAR/20 "production"' \
  --sort density --session "your topic"
```

For search activity, routing precedence is explicit `--session`, then
`FILMOT_SESSION`, then a bulk-download TOPIC, then the current date.
`filmot sessions "your topic" --summary` keeps manual search events and the
fallback stages inside `research` in separate scope tables, so repeated
candidates across stages are not presented as one corpus count. The same
summary links bounded scout/probe provenance and saved sources to their
discovery stage/query. A completed transcript save that has no staged research
or probe selection is included as stage `manual`, with
`query_not_recorded`; the summary does not invent an originating query. When a
source has both records, the research/probe selection takes precedence over
the manual fallback. New successful `transcript --save-to` events record
best-effort title and channel for display; legacy manual events without that
metadata remain honestly `Unknown`, and neither case permits query inference.
Probe rows preserve operational states such as
`broad_sampled`, `deferred`, and `failed_closed` rather than presenting them as
completed calls. The legacy selected-source stage `title_transcript` is joined
to the canonical `title+transcript` search stage; a legacy
`query not recorded` marker remains an explicit unknown, not permission to
reconstruct or infer the missing relationship.

**If you have a channel corpus downloaded**, you can run the same proximity operators offline:

```bash
# Same NEAR/N syntax, but against your local corpus — no API calls
filmot channel-search chat-with-traders '"risk management" NEAR/10 "position sizing"'
filmot channel-search chat-with-traders '("risk" | "drawdown") NEAR/10 ("position" | "sizing")'
filmot channel-search chat-with-traders '"blew up account"~5'
```

### Phase 3: Credibility Verification (5 minutes)

For each major claim you want to report:

1. **Trace source independence** — seek multiple traceable sources, but do not
   substitute a count for an inspectable primary record
2. **Check for named experts** — anonymous claims are weak
3. **Look for the counter-narrative** — search for "debunked", "criticism", "fraud"
4. **Test in another language** if claim is country-specific
5. **Close on the primary source** when the claim points to a paper, filing,
   patent, announcement, dataset, or other inspectable original
6. **Record the evidence relation and assessment explicitly** rather than
   treating concordance counts as a verdict

```bash
# Always check for the counter-narrative
filmot search '"topic" NEAR/15 "debunked"' --sort density
filmot search '"topic" NEAR/15 "criticism"' --sort density
filmot search '"topic" NEAR/15 "fraud"' --sort density
```

Use the durable claim register to keep exact source text, analyst notes, and
judgments separate:

```bash
# Narrow a saved passage locally; output includes a literal timestamped URL
filmot transcript VIDEO_ID --grep '"claim phrase" NEAR/20 "mechanism"'

# The same evaluation as versioned JSON, including citation-ready match rows
filmot transcript VIDEO_ID --grep '"claim phrase" NEAR/20 "mechanism"' --raw
```

`transcript --grep` filters saved copies by requested language and validated,
monotonic segment/text alignment, collapses equivalent copies, and reuses the
single remaining content variant before configuring external routes. Multiple
distinct usable variants or incompatible timing retain the normal fetch path
and explain the fallback on stderr. Human matches include a literal timestamped
URL; raw matches contain `seconds`, `timestamp`, `deep_link`, and `excerpt`, and
a valid query with no hits is a typed empty result.

Every supplied grep value is parsed before library lookup, proxy setup, or
network retrieval. Blank or malformed proximity expressions therefore fail as
`InvalidGrepQuery` at `parse-query` without turning into a full-transcript
download. Human and raw rendering consume the same typed evaluator, and a
unique local result routes its compact activity event to the saved source topic.

```bash
# Declare one atomic, falsifiable statement
filmot claims add "your topic" "One exact claim statement"

# Add supporting, contradictory, qualifying, contextual, origin, or mention evidence
filmot claims cite "your topic" c-CLAIMID \
  --source "https://example.org/primary-document" \
  --source-kind official --relation qualifies --locator "Section 4" \
  --excerpt "short exact source passage" --note "Analyst interpretation" \
  --primary --independence independent

# Append a human assessment after reviewing the evidence
filmot claims assess "your topic" c-CLAIMID \
  --verdict mixed --confidence medium --note "Why this assessment follows"

# Inspect without changing or logging the claim register
filmot claims show "your topic" c-CLAIMID --raw
```

Relations are `supports`, `contradicts`, `qualifies`, `context`, `origin`, and
`mentions`. `library compare` hits do not become evidence merely because they
match lexically; the analyst must choose and record the relationship. Claim
events are strict and append-only, while their session mutation logs contain
only compact IDs and classifications. A timestamp/`--video` locator belongs
only to source kind `video`; `--at` accepts finite non-negative seconds or a
displayed `M:SS`/`H:MM:SS` timestamp and stores canonical seconds. `--source`
and `--video` are mutually exclusive,
and `--video` requires an exact 11-character YouTube ID matching
`[A-Za-z0-9_-]{11}`, not a URL; malformed values fail before persistence. Cite
a paper or patent as a separate evidence item. Derived claim IDs use
the runtime-independent `utf8-ascii-whitespace/v1` method recorded on the claim.
Evidence and assessment IDs use the recorded `canonical-json-array/v2` method.
For evidence, v2 covers all persisted identity/provenance inputs, including
source locators, quoted text and analyst note, classifications, title/channel,
and research run; a video deep link is derived and validated separately.
Strict replay verifies those IDs and the assessment supersedes chain.
Topic-wide transaction locks prevent simultaneous writers from forking that
chain, and complete events are validated before they become visible. New
events use `filmot.claim/v2` with contiguous per-topic sequences. Valid
sequence-less `filmot.claim/v1` histories remain readable and can be continued
with v2 events, but the legacy files are ordered only in memory and never
rewritten. A successful new publication normally removes its private
same-directory temporary after the immutable event is visible. If that unlink
fails, the failure names both the durable destination and exact retained
temporary; it does not imply rollback. If publication and cleanup both fail,
both errors are surfaced, the exact retained temporary is named, and the
destination is explicitly not confirmed. Claim operations never scan for or
delete historical or unrelated temporaries. Exact mutation retries are
content-idempotent, but recovery should inspect the reported
durable/not-confirmed outcome rather than guess.

### Phase 4: Probe for Connections (optional, 5 minutes)

If you have enough transcript material, use `--probe` to discover connections you didn't know to look for:

```bash
filmot research "your topic" --depth 12 --dedupe --probe
```

Only manually saved sources and transcripts selected by the staged Filmot
workflow seed an automatic probe. Scout and earlier probe discoveries are
quarantined from the seed frontier so one loose result cannot recursively steer
later queries. The probe preserves source and sentence boundaries, clusters
likely ASR variants, and ranks lexical pairs with a language-neutral,
source-normalized salience score. It reports both `co-windows:N` (overlapping
50-word windows, 25-word stride) and distinct supporting-source counts. A
relationship must occur in at least two eligible transcripts before it consumes
a probe query. These are lexical discovery signals, not semantic or evidentiary
judgments.

Probe planning also has explicit terminal outcomes. With enough eligible
seeds but no sufficiently specific multiword terms, it records
`no_candidate_terms`; with terms but no pair meeting cross-source support and
co-window requirements, it records `no_cross_source_pairs`. Both outcomes run
zero queries rather than appearing to hang or silently finish. Named session
summaries retain a bounded table of probe-run outcomes (including skipped,
empty, partial, and completed runs), with older rows counted as omitted.

Each attempted probe logs the exact query, effective title/channel/language
scope, raw API count, returned sample, post-scope count, and errors before
downloading at most three related discoveries. API calls stop once that
capacity is full. If a query exceeds the broad threshold and its returned
sample contains no lexically coherent new candidate, Filmot records the query
as `broad_sampled`,
reports sample coverage without claiming a global zero, marks the lower-ranked
tail `deferred`, and stops automatic probing. Its copyable manual command keeps
the same language, usable title constraint, and channel-ID scope. A probe keeps
the title constraint only when the initial title stage proved usable; otherwise
the automatic result reports its passage-level topic relevance post-filter and
the manual command reproduces the API request scope for explicit screening.

For a reusable synthesis packet, `filmot library context TOPIC --format
structured --output PATH` creates missing parent directories for nested output
paths. Parent creation and the file write share the typed context-write failure
boundary, so automation receives a command failure instead of an uncaught
filesystem exception.

---

## 6. Common Research Traps

### Trap 1: Recency Bias
The most recent video isn't the most accurate. Often the **second wave** of coverage (1-4 weeks after breaking news) provides the best analysis because:
- Initial reports are often wrong or incomplete
- Experts take time to weigh in
- Corrections and context emerge

**Fix**: Don't stop at scout results. Filmot's depth gives you the backstory.

### Trap 2: View Count = Authority
High views can mean quality OR clickbait. Low views can mean obscure OR niche expert.

**Fix**: Use view count only as mutable reach context. Inspect who made the
specific claim, whether the cited source exists, what the source actually says,
and whether independent evidence supports it. An institutional channel is
useful provenance, not an automatic verdict; popularity does not repair weak
evidence.

### Trap 3: The "Accidental Discovery" Frame
Many low-quality videos use the frame "Scientists ACCIDENTALLY discovered..."
because it is clickbait gold. Sometimes the underlying surprise is real; the
framing alone cannot establish fabrication. Trace the named result to its
primary source and compare the source's actual agency and scope claims.

**Fix**: Check if the "accidental" discovery has a paper, named researchers, and institutional backing.

### Trap 4: Confirmation Bias in Search
If you search for "X is true" you'll find videos saying X is true. If you search for "X is false" you'll find those too.

**Fix**: Always run the counter-search. For every `"X" NEAR/15 "breakthrough"`, also run `"X" NEAR/15 "debunked"`. Report both sides.

### Trap 5: The Filmot Index Lag
Filmot indexes transcripts ~24-48 hours after upload. For breaking news, you'll miss the latest.

**Fix**: Use `--scout` (on by default) to catch the last 7 days via YouTube API. For fast-moving stories, use `--scout-days 14` or even `--scout-days 30`.

### Trap 6: Single-Language Echo Chamber
English-language YouTube is massive but not comprehensive. Many stories look different (or don't exist) in other languages.

**Fix**: Search in the language of origin. Korean topic → Korean search. Russian event → Russian search. Even a null result is informative.

### Trap 7: Stopping at YouTube (the primary-source handoff)
YouTube transcripts tell you what *the world is saying* about a thing — fast, broad, multilingual. They do not tell you what the *thing itself* says. Field-tested lesson: on a launch-day investigation, transcripts got ~90% of the truth in an hour, but the last 10% — and two outright corrections — required the primary document (a company announcement and a system card). Two specific failure modes:

- **The negative-space heuristic inverts on release day.** "If a big claim had real backing, someone credible would be covering it" is a good fake-detector — *except* in the first 24-48 hours after an official announcement, when even true claims haven't echoed yet. A press-release claim with thin organic coverage is not suspicious; it's just new. Don't file it as fabricated.
- **YouTube inflates agency.** Creators systematically upgrade "the tool assisted experts" into "the tool autonomously beat the experts." Capability claims survive cross-referencing; *agency* claims often don't. Check the primary source for who-did-what.

**Fix**: For any claim that traces to a specific document (paper,
announcement, model card, filing, court record), **close on the primary source**
before you assign final confidence. Use the tool to find *who is talking and
what they emphasize*; use the original document to nail *what it actually
says*. Multiple independent reporters can raise confidence, but a count does
not substitute for a primary source when the original is inspectable.

### Trap 8: Curated means verified

A focused playlist can feel pre-vetted because someone intentionally grouped
and ordered its videos. That establishes a curator and a path, not accuracy,
coverage, or independence. A channel may include its own interviews, repeated
versions, promotional material, or only one side of a dispute.

**Fix**: Keep playlist ID and position as discovery provenance, inspect source
lineage, run counter-searches outside the playlist, and close important claims
on primary sources. Never count playlist membership itself as supporting
evidence.

---

## 7. Reporting What You Find

### Structure Your Output

After researching, present findings in this order:

1. **Bottom line up front**: What's the current state? What's real?
2. **Key findings**: Numbered, with source attribution
3. **What's credible vs. what's hype**: Explicitly separate them
4. **What we don't know**: Gaps, unverified claims, pending replication
5. **Sources**: Name the channels, view counts, dates

### Attribution Standards

Always attribute. The user should be able to verify anything you claim:

- **Good**: "According to German Science Guy (75K subs, 40K views), citing the Caltech paper published Jan 2026..."
- **Bad**: "Scientists recently discovered a new superconducting state"
- **Good**: "The DARPA germanium claim (Sci Factor, 43 views) shows multiple red flags: misspelled terms, fabricated institutional reports..."
- **Bad**: "Some sources are less credible"

### Verdict and Confidence

Keep the direction of the evidence separate from how certain you are. These
are the same controlled values accepted by `filmot claims assess`:

| Verdict | Meaning |
|---------|---------|
| **open** | Evidence has not yet justified a directional assessment |
| **supported** | Reviewed evidence supports the exact statement as written |
| **contradicted** | Reviewed evidence contradicts the exact statement as written |
| **mixed** | Material supporting and contradicting/qualifying evidence remains |

| Confidence | Meaning |
|------------|---------|
| **unknown** | Not assessed or insufficiently inspected |
| **low** | Tentative; major evidence or independence gaps remain |
| **medium** | Material evidence reviewed, with explicit limitations |
| **high** | Strong claim-specific evidence, primary-source closeout where applicable, and serious alternatives addressed |

Source counts are a research heuristic, not an automatic confidence formula.
Document the rationale in `--note`; Filmot never computes the verdict or
confidence from citations, popularity, density, or echo clusters.

---

## 8. Field Notes from Real Research

These are patterns we discovered the hard way.

### David Grusch / UAPs
- **Credible pipeline**: Whistleblower testimony → Congressional hearings → documentary coverage → expert analysis. Following this chain separates real disclosure from conspiracy noise.
- **The "Age of Disclosure" documentary** (Nov 2025, 34 government insiders on camera) was a major event — but only discoverable through scout, not Filmot search alone (too recent).
- **Probe discovered** second whistleblower Jake Barber with firsthand retrieval claims — a connection the initial search missed entirely.

### Russia-Ukraine Ceasefire
- **Scout caught breaking news** (Feb 14, 2026 elections story, Abu Dhabi talks) that Filmot hadn't indexed yet.
- **Filmot provided depth** on the Easter ceasefire violations and territorial dynamics going back months.
- **The combination** was more complete than either source alone — neither scout nor Filmot alone would have given the full picture.

### Room-Temperature Superconductors
- **The field is dominated by LK-99 noise** (2023). You have to actively filter past it to find current work.
- **Korean-language search confirmed** the LK-99 story is dead in Korea — useful negative result.
- **AI-slop is thick** in this topic. "Breakthrough" claims with impossible specificity are the biggest hazard.
- **The real advance** (Caltech PDM) is modest but genuine. It doesn't claim room-temperature superconductivity — it claims a new state that might help us understand how to get there.
- **Twistronics** (magic-angle graphene) is the strongest legitimate pathway — well-documented, Nobel-adjacent, and progressing steadily.
- **Update (2026-06-10):** PDM remains *unreplicated but undisputed* — still one Nature paper (FeTe₀.₅₅Se₀.₄₅, 40% gap modulation), no published independent confirmation 16 months on. Twistronics coverage went quiet (zero transcript hits Feb–Jun 2026). No new LK-99-style hype wave emerged; the slop ecosystem migrated to quantum-computing chips instead (same announcement-vs-evidence pattern, see Majorana 2). Lesson reinforced: **quiet science is underrepresented on YouTube exactly as press-release claims are overrepresented** — a stale field note can mean "nothing happened," not "you missed it." Check primary literature before assuming either.

### Active-inference curated path

- **A five-call discovery path was enough:** one bounded Machine Learning Street
  Talk shelf exposed 20 public playlists, and one ten-item slice of “Active
  Inference / CogSci” returned ten current video resources plus a continuation.
- **The raw handoff preserved the curator's path:** the first two downloaded
  transcripts retained playlist ID, item ID, zero-based position, and addition
  time alongside the normal content-addressed discovery reference.
- **Curation did not answer the comparison by itself:** the two transcripts
  contained 17 exact “active inference” matches but zero exact “reinforcement
  learning” matches. That makes them useful active-inference material, not a
  balanced comparison corpus. A counter-search is still required.

### Key Takeaway
The tool's real power isn't finding information — any search engine does that. **The power is combining scout freshness + Filmot depth + NEAR/N precision + multilingual reach + probe discovery to triangulate evidence and test claims.** No single query gives you the answer. The methodology does.

---

## Quick Reference: Research Checklist

```
Before reporting any claim:
[ ] Sought independent coverage and the strongest inspectable primary evidence?
[ ] Checked full-transcript echo/lineage candidates before counting independence?
[ ] Treated playlist membership/order as curator provenance, not evidence?
[ ] Treated comments/likes as mutable discourse and activity, not consensus or credibility?
[ ] Named researchers/experts involved?
[ ] Checked for counter-narrative (debunked/criticism)?
[ ] Verified institutional claims exist?
[ ] Closed on the primary document when it is inspectable?
[ ] Checked view count vs. claim magnitude?
[ ] Tested in language of origin (if applicable)?
[ ] Separated exact source excerpt/locator from analyst note?
[ ] Recorded supporting, contradictory, and qualifying evidence explicitly?
[ ] Kept lexical mentions separate from evidence relationships?
[ ] Assigned a human verdict and confidence with rationale?
[ ] Attributed every claim to its source?
[ ] Preserved timestamp/deep-link details when saved segments provide them?
```

---

*This guide is a living document. Update it as new patterns emerge from research sessions.*
