# Agent Research Guide: Sifting Signal from Noise

**Field-tested methodology for AI agents doing real research with Filmot CLI**

*Distilled from dozens of research sessions across topics: geopolitics, UAPs, superconductors, fusion energy, brain-computer interfaces, deep-sea mining, solid-state batteries, and more. These are the patterns that consistently separated truth from hype.*

---

## The Core Problem

YouTube is simultaneously the world's largest repository of expert knowledge and the world's largest repository of garbage. A single search returns Nobel laureates and AI-slop clickbait side by side. Your job isn't just to find information — it's to **evaluate** it.

This guide teaches you how.

---

## 1. Source Credibility Scoring

Not all sources are equal. Before citing anything, mentally score it:

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

### Tier 4: Noise (Avoid or flag explicitly)
- **AI-generated content farms** (see Section 2)
- **Hype channels** with clickbait titles and no citations
- **Conspiracy aggregators** mixing real and fabricated claims
- **Channels with <100 views** on "breakthrough" claims (if it were real, someone would care)

---

## 2. Detecting AI-Generated Misinformation

This is the most important skill. AI-slop videos are flooding YouTube and they look increasingly convincing. Here are the red flags we've confirmed in the field:

### Hard Red Flags (any one = reject)
- **Misspelled technical terms**: "germanmanium" instead of germanium, repeated consistently (AI doesn't know it's wrong)
- **Fabricated institutional reports**: "Goldman Sachs published a 180-page report titled..." — verify these exist before citing
- **Impossible specificity without sources**: "measured resistance of 0.001 ohms over 12 meters at 10,000 amperes" — real papers hedge; fake ones give exact numbers to sound credible
- **Timelines that don't exist**: "commercial versions will appear by late 2025, first in military submarines" — verifiable claims that no one else is reporting
- **View count under 100 on "world-changing" claims**: Real breakthroughs get attention. A video claiming unlimited energy with 43 views is not suppressed — it's fabricated.

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
4. If NOBODY else is talking about it, it's almost certainly fake
```

**Real example from our research**: The "DARPA 2026 Room Temp Superconductor Breakthrough" video claimed germanium was made superconducting at room temperature via a DARPA-funded lab. Red flags: "germanmanium" (misspelled), fabricated Goldman Sachs report, impossible claims (18 months stable at room temp), 43 views on a channel called "Sci Factor." Meanwhile, the Caltech Cooper Pair Density Modulation discovery (real) was covered by German Science Guy (75K subs, 40K views), cited the actual paper, named the professors, and included appropriate caveats ("only one paper so far, needs replication").

---

## 3. The Cross-Reference Method

Never trust a single source. The power of this tool is **cross-referencing across multiple independent sources**.

### The Triangle Test

For any claim, find at least three independent sources saying the same thing:

```bash
# Source 1: Search transcripts for the claim
filmot search '"claim keyword" NEAR/15 "related context"' --sort density --min-matches 2

# Source 2: Scout for recent YouTube coverage
filmot yt-search "topic claim" --days 30

# Source 3: Search with different keywords for the same event
filmot search '"alternative phrasing" NEAR/15 "same context"' --sort density
```

### Convergence vs. Echo

**Convergence** (trustworthy): Multiple independent journalists/scientists arrive at the same conclusion from different angles. Different channels, different countries, different perspectives — same core facts.

**Echo** (suspicious): Multiple channels repeating the exact same script or numbers. This often means one source (possibly AI-generated) got copied across content farms.

**How to tell the difference**: Read the actual transcripts. Convergent sources use different words, different examples, and add their own analysis. Echo sources have suspiciously similar phrasing.

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

---

## 4. Multilingual Verification

Searching in other languages is a superpower for breaking echo chambers.

### When to Use It

- **Verifying claims originating from a specific country**: Korean superconductor claims → search in Korean (초전도체)
- **Finding local reporting on global events**: Russia-Ukraine → search in Ukrainian, Russian
- **Testing if a "worldwide breakthrough" is actually known globally**: If only English-language AI-slop channels report it, it's probably fake

### What We Learned

- **Korean (상온 초전도체)**: Confirmed LK-99 was a Korean phenomenon with massive local coverage in 2023, but NO follow-up breakthroughs — useful negative result
- **Korean search on other topics**: Can reveal local sources that English-language media missed entirely
- **Spanish/Hindi/Arabic**: Massive YouTube communities that often cover international stories from different angles

### Practical Tips

```bash
# Direct foreign-language search
filmot search '"초전도체"' --lang ko --sort density

# NEAR/N works across languages
filmot search '"초전도" NEAR/15 "상온"' --sort density

# Use yt-search for non-Latin scripts too
filmot yt-search "상온 초전도체 2025" --days 180
```

**Key insight**: When a claim originates from Country X but nobody in Country X is talking about it anymore, the claim is likely dead. The local community always knows first when their own breakthroughs get debunked.

---

## 5. The Research Pipeline: How to Approach Any Topic

### Phase 1: Broad Scan (5 minutes)

Start wide. Get the lay of the land.

```bash
# One-command overview
filmot research "your topic" --depth 10 --dedupe --scout-days 14
```

This gives you:
- **Scout results**: What's happening RIGHT NOW (last 7-14 days)
- **Filmot results**: Historical depth and established coverage
- **Downloaded transcripts**: Raw material for analysis

### Phase 2: NEAR/N Surgical Probes (5-10 minutes)

Based on what you learn in Phase 1, go deeper on specific claims:

```bash
# Find the specific moment two concepts connect
filmot search '"person" NEAR/15 "specific claim"' --sort density --min-matches 2

# Narrow by date if investigating a specific event
filmot search '"event" NEAR/10 "detail"' --start-date 2025-01-01 --sort density
```

**If you have a channel corpus downloaded**, you can run the same proximity operators offline:

```bash
# Same NEAR/N syntax, but against your local corpus — no API calls
filmot channel-search chat-with-traders '"risk management" NEAR/10 "position sizing"'
filmot channel-search chat-with-traders '"blew up account"~5'
```

### Phase 3: Credibility Verification (5 minutes)

For each major claim you want to report:

1. **Count independent sources** — need 2+ credible (Tier 1-2) sources
2. **Check for named experts** — anonymous claims are weak
3. **Look for the counter-narrative** — search for "debunked", "criticism", "fraud"
4. **Test in another language** if claim is country-specific

```bash
# Always check for the counter-narrative
filmot search '"topic" NEAR/15 "debunked"' --sort density
filmot search '"topic" NEAR/15 "criticism"' --sort density
filmot search '"topic" NEAR/15 "fraud"' --sort density
```

### Phase 4: Probe for Connections (optional, 5 minutes)

If you have enough transcript material, use `--probe` to discover connections you didn't know to look for:

```bash
filmot research "your topic" --depth 12 --dedupe --probe
```

The probe extracts entities from downloaded transcripts, finds co-occurring pairs, and auto-generates NEAR/N queries to surface related content you might have missed.

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
Many AI-slop videos use the frame "Scientists ACCIDENTALLY discovered..." because it's clickbait gold. Sometimes it's real (the Caltech superconductor state genuinely was unexpected). Usually it's fabricated.

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

### Confidence Levels

Be explicit about your confidence:

| Level | Meaning | Example |
|-------|---------|---------|
| **Confirmed** | 3+ independent credible sources agree | "The 2025 Nobel went to Clark, Devoret, Martinis" |
| **Likely** | 1-2 credible sources, no contradictions | "Caltech discovered Cooper Pair Density Modulation" |
| **Claimed** | Single source, not yet verified | "The professor says this is a step toward room-temp" |
| **Disputed** | Sources disagree | "LK-99 claims were contested and ultimately debunked" |
| **Fabricated** | Clear red flags, no credible backing | "The DARPA germanium video is AI-generated misinformation" |

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

### Key Takeaway
The tool's real power isn't finding information — any search engine does that. **The power is combining scout freshness + Filmot depth + NEAR/N precision + multilingual reach + probe discovery to triangulate truth.** No single query gives you the answer. The methodology does.

---

## Quick Reference: Research Checklist

```
Before reporting any claim:
[ ] Found 2+ independent credible sources?
[ ] Named researchers/experts involved?
[ ] Checked for counter-narrative (debunked/criticism)?
[ ] Verified institutional claims exist?
[ ] Checked view count vs. claim magnitude?
[ ] Tested in language of origin (if applicable)?
[ ] Separated confirmed facts from single-source claims?
[ ] Flagged anything that smells like AI-slop?
[ ] Attributed every claim to its source?
[ ] Stated confidence level for each finding?
```

---

*This guide is a living document. Update it as new patterns emerge from research sessions.*
