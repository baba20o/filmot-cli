# Agent Research Guide: Sifting Signal from Noise

**Field-tested methodology for AI agents doing real research with Filmot CLI**

*Distilled from dozens of research sessions across topics: geopolitics, UAPs, superconductors, fusion energy, brain-computer interfaces, deep-sea mining, solid-state batteries, and more. These are the patterns that consistently separated truth from hype.*

---

## The Core Problem

YouTube is simultaneously the world's largest repository of expert knowledge and the world's largest repository of garbage. A single search returns Nobel laureates and AI-slop clickbait side by side. Your job isn't just to find information — it's to **evaluate** it.

This guide teaches you how.

---

## 1. Source Assessment

Not all sources are equally useful for every claim. Treat the tiers below as
inspection priorities, not verdicts: audience size and engagement are
heuristics, and authority is claim-specific. Before citing anything, inspect
the passage and verify the source's relationship to the claim.

### Tier 1: Primary Sources (Trust, but verify)
- **Official channels**: Nobel Prize, university channels, government agencies, C-SPAN
- **Named experts on camera**: A professor explaining their own published research
- **Major news outlets**: BBC, Reuters, AP, WSJ, NYT, PBS, CNN (for factual reporting, not opinion segments)
- **Credible science channels**: Veritasium, Dr. Ben Miles, SmarterEveryDay, German Science Guy, PBS Space Time

**Signals**: High subscriber count relative to niche, consistent upload history, credentials stated, sources cited in video

### Tier 2: Credible Analysis (Cross-reference)
- **Domain-specific channels** with track records (75K+ subs in a niche = real audience)
- **Podcast interviews** with named guests who have verifiable credentials
- **News aggregators** with editorial standards (Firstpost, WION, CNN-News18 for international coverage)

**Signals**: Views in the thousands-to-hundreds-of-thousands range, engagement ratio (likes/views > 2%), comments showing informed discussion

### Tier 3: Secondary Reporting (Use cautiously)
- **Smaller channels** reporting on primary source findings
- **Reaction/commentary channels** discussing news
- **Channels under 10K subs** covering breaking stories

**Signals**: Check if they cite the original paper/source. If they do, go find the primary source instead.

### Tier 4: High-risk material (Verify before using)
- **AI-generated content farms** (see Section 2)
- **Hype channels** with clickbait titles and no citations
- **Conspiracy aggregators** mixing real and fabricated claims
- **Very low-view "breakthrough" claims with no traceable primary source** — the combination is a warning, but low views alone can also mean niche expertise or very recent publication

---

## 2. Detecting AI-Generated Misinformation

This is the most important skill. AI-slop videos are flooding YouTube and they look increasingly convincing. Here are the red flags we've confirmed in the field:

### Strong Red Flags (any one requires primary-source verification)
- **Misspelled technical terms**: "germanmanium" instead of germanium, repeated consistently (AI doesn't know it's wrong)
- **Fabricated institutional reports**: "Goldman Sachs published a 180-page report titled..." — verify these exist before citing
- **Impossible specificity without sources**: "measured resistance of 0.001 ohms over 12 meters at 10,000 amperes" — real papers hedge; fake ones give exact numbers to sound credible
- **Timelines that don't exist**: "commercial versions will appear by late 2025, first in military submarines" — verifiable claims that no one else is reporting
- **Tiny audience plus a world-changing claim and no traceable evidence**: Treat it as unverified. View count alone never proves fabrication, especially for niche or launch-day material.

### Soft Red Flags (multiple = suspect)
- **No named researchers or institutions** — real breakthroughs have authors
- **Breathless narration with no caveats** — real scientists always hedge ("this could potentially", "more research needed")
- **Channel name is generic** ("Sci Factor", "Tech Genesis", "Future AI Tech") with no consistent identity
- **Duration exactly 10-11 minutes** (YouTube monetization sweet spot) with zero dead air — suggests scripted AI content
- **No comments section engagement** or all comments are generic ("Wow amazing!")
- **Published dates cluster** — AI content farms publish 2-3 videos per day

### The Confirmation Test

When you find a "breakthrough" claim, run this 30-second test:

```
1. Search for the claimed institution + finding in Filmot or YouTube
2. Check if ANY credible channel (Tier 1-2) is also reporting it
3. Look for the actual paper (researcher name + topic + "paper" or "arxiv")
4. If nobody else is discussing it, classify it as uncorroborated and check
   timing, niche context, and the primary document before judging it
```

**Real example from our research**: The "DARPA 2026 Room Temp Superconductor Breakthrough" video claimed germanium was made superconducting at room temperature via a DARPA-funded lab. Red flags: "germanmanium" (misspelled), fabricated Goldman Sachs report, impossible claims (18 months stable at room temp), 43 views on a channel called "Sci Factor." Meanwhile, the Caltech Cooper Pair Density Modulation discovery (real) was covered by German Science Guy (75K subs, 40K views), cited the actual paper, named the professors, and included appropriate caveats ("only one paper so far, needs replication").

---

## 3. The Cross-Reference Method

Never trust a single source. The power of this tool is **cross-referencing across multiple independent sources**.

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

**Convergence** (trustworthy): Multiple independent journalists/scientists arrive at the same conclusion from different angles. Different channels, different countries, different perspectives — same core facts.

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

### The Density + Views + Date Triangle

When evaluating search results, cross-reference three metrics:

| Metric | What It Tells You |
|--------|-------------------|
| **Density** (matches/min) | How focused the discussion is — high density = dedicated deep-dive, not passing mention |
| **Views** | Social proof — but beware: low views on old videos ≠ wrong, low views on "breakthrough" claims = suspicious |
| **Date** | Recency — but the first report isn't always the best. Look for the 2nd-3rd wave of analysis |

```bash
# The sweet spot: recent + focused + viewed
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

- **Scout results**: What's happening RIGHT NOW (last 7-14 days)
- **Filmot candidates**: Historical depth from title+transcript, exact-phrase,
  and `NEAR/N` stages before loose matching
- **Downloaded transcripts**: Raw material for analysis

Treat the automatic corpus as candidate material, not verified evidence.
Balanced ranking keeps passage relevance, lexical density, echo risk, and an
audience/engagement source prior visible as separate signals. The source prior
is not a credibility score. A loose fallback above the configured threshold is
blocked unless `--accept-broad` is explicit, and accepted candidates still
pass a passage-level relevance gate. Widen client-side ranking deliberately
with `--candidate-pages` and `--candidate-pool`; inspect the preview, effective
query, candidate scope, and source mix before citing or synthesizing anything.

Use `--channel` when you want a named source. The name is resolved to displayed
channel IDs and the command fails closed when resolution is empty or Filmot
returns candidates outside the selected IDs.

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
candidates across stages are not presented as one corpus count.

**If you have a channel corpus downloaded**, you can run the same proximity operators offline:

```bash
# Same NEAR/N syntax, but against your local corpus — no API calls
filmot channel-search chat-with-traders '"risk management" NEAR/10 "position sizing"'
filmot channel-search chat-with-traders '("risk" | "drawdown") NEAR/10 ("position" | "sizing")'
filmot channel-search chat-with-traders '"blew up account"~5'
```

### Phase 3: Credibility Verification (5 minutes)

For each major claim you want to report:

1. **Trace source independence** — seek multiple credible sources, but do not
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
only to source kind `video`; `--source` and `--video` are mutually exclusive,
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
rewritten.

### Phase 4: Probe for Connections (optional, 5 minutes)

If you have enough transcript material, use `--probe` to discover connections you didn't know to look for:

```bash
filmot research "your topic" --depth 12 --dedupe --probe
```

The probe preserves source and sentence boundaries, prefers terms supported by
multiple transcripts, clusters likely ASR variants, and reports both
`co-windows:N` (overlapping 50-word windows, 25-word stride) and distinct
supporting-source counts. A relationship must occur in at least two transcripts
before it consumes a probe query. It logs the exact query, effective title/channel
scope, raw API count, returned candidates, post-scope count, and errors before
downloading a small set of related discoveries. A probe keeps the title
constraint only when the initial title stage proved usable; otherwise the
reported scope uses a passage-level topic relevance filter.

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

**Fix**: Combine view count with channel credentials. A 500-view video from a university physics department > a 500K-view video from "AMAZING SCIENCE FACTS."

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

### Key Takeaway
The tool's real power isn't finding information — any search engine does that. **The power is combining scout freshness + Filmot depth + NEAR/N precision + multilingual reach + probe discovery to triangulate truth.** No single query gives you the answer. The methodology does.

---

## Quick Reference: Research Checklist

```
Before reporting any claim:
[ ] Found 2+ independent credible sources?
[ ] Checked full-transcript echo/lineage candidates before counting independence?
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
