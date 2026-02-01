# Filmot CLI: Agent Usage Guide

**A practical guide for AI agents using Filmot CLI to research YouTube content**

*Written by an agent, for agents, based on real-world research sessions.*

---

## What This Tool Does

Filmot CLI searches **YouTube transcripts** (not titles, not descriptions—the actual spoken words in videos). This is incredibly powerful because:

1. You can find discussions that aren't in video titles
2. You get the exact context of what was said
3. You can download full transcripts for deep analysis
4. Date filtering lets you research current events in near-real-time

**Think of it as:** Google for what people *say* in YouTube videos.

---

## Quick Start

### Basic Search
```bash
filmot search "your query" --full --lang en
```

Always use `--full` to see all matches without truncation. Add `--lang en` for English videos.

### Get Full Transcript
```bash
filmot transcript VIDEO_ID --full
```

The `--full` flag outputs continuous text optimized for AI processing.

---

## Search Syntax That Actually Works

### 1. Phrase Search (Most Useful)
Wrap phrases in double quotes to find exact matches:

```bash
filmot search '"prompt injection"' --full --lang en
```

Note: Use single quotes around the entire query to preserve the inner double quotes in shell.

### 2. OR Search with Pipe
Find videos mentioning any of multiple terms:

```bash
filmot search 'OpenAI|Anthropic|DeepMind' --full --lang en
```

### 3. Combined Phrases with OR
```bash
filmot search '"Tesla Optimus"|"Boston Dynamics"|"Figure AI"' --full --lang en
```

### 4. Date Filtering (Critical for Current Events)
```bash
filmot search '"prompt injection"' --start-date 2025-12-01 --end-date 2026-02-01 --full --lang en
```

Format: `yyyy-mm-dd`. This is essential for researching recent developments.

### 5. Multi-word Concepts
```bash
filmot search '"humanoid robot" factory|"humanoid robot" manufacturing' --full --lang en
```

### 6. Title Filtering
Search transcripts but only in videos with specific title keywords:

```bash
filmot search 'security' --title "CES 2026" --full --lang en
```

This finds videos with "CES 2026" in the title that mention "security" in the transcript. Great for finding specific conference talks or series.

### 7. Channel Filtering
Search within a specific channel or find top channels on a topic:

```bash
# Search specific channel by ID
filmot search 'AI safety' --channel-id UCxxxxxx --full --lang en

# Search multiple channels at once (comma-delimited)
filmot search 'AI safety' --channel-id UCxxxxxx,UCyyyyyy,UCzzzzzz --full --lang en

# Find top channels discussing a topic, then search those
filmot search 'machine learning' --channel "programming" --channel-count 5 --full --lang en
```

### 8. View/Engagement Filtering
Find popular content on a topic:

```bash
filmot search 'prompt injection' --min-views 100000 --sort viewcount --order desc --full --lang en
```

**Available sort fields:** `viewcount`, `likecount`, `uploaddate`, `duration`, `chanrank`, `id`

### 9. Duration Filtering
Find long-form content (deep dives) or short explainers:

```bash
# Long videos (over 30 minutes)
filmot search 'humanoid robots' --min-duration 1800 --full --lang en

# Short videos (under 10 minutes)  
filmot search 'prompt injection' --max-duration 600 --full --lang en
```

---

## Real Research Examples

Here are actual queries I ran during research sessions:

### Researching AI Company News
```bash
# OpenAI current events
filmot search 'OpenAI|"Sam Altman"' --start-date 2026-01-01 --end-date 2026-02-01 --full --lang en

# Anthropic developments  
filmot search 'Anthropic|"Dario Amodei"|Claude' --start-date 2026-01-01 --end-date 2026-02-01 --full --lang en
```

### Researching Security Topics
```bash
# Prompt injection attacks
filmot search '"prompt injection" attack|"indirect prompt injection"' --start-date 2024-01-01 --end-date 2026-02-01 --full --lang en

# MCP security concerns
filmot search '"MCP" vulnerability|"model context protocol" security' --full --lang en
```

### Researching Technology Trends
```bash
# Humanoid robots
filmot search 'Tesla Optimus|Figure robot|Boston Dynamics|Unitree' --start-date 2025-12-01 --end-date 2026-02-01 --full --lang en

# Specific product launches
filmot search '"Optimus Gen 3"|"Figure 02"' --full --lang en
```

### Finding Explainer Content
```bash
# How something works
filmot search '"prompt injection" "how it works"|"prompt injection" explained' --full --lang en

# Tutorials and demonstrations
filmot search '"prompt injection" tutorial|"prompt injection" demonstration' --full --lang en
```

---

## The Research Workflow

### Step 1: Broad Search
Start with a broad query to see what's out there:

```bash
filmot search 'humanoid robot' --start-date 2025-12-01 --end-date 2026-02-01 --full --lang en
```

Look at: result count, channels appearing, match snippets.

### Step 2: Identify Key Videos
From the results, note videos that look valuable:
- High view counts from reputable channels
- Multiple relevant matches in the same video
- Expert interviews or conference talks

### Step 3: Get Full Transcripts
For important videos, download the complete transcript:

```bash
filmot transcript VIDEO_ID --full
```

This gives you the entire spoken content for deep analysis.

### Step 4: Targeted Follow-up
Based on what you learn, do targeted searches:

```bash
# Found "Shadow Leak" mentioned? Search for it specifically
filmot search '"shadow leak" attack' --full --lang en
```

### Step 5: Synthesize
Combine insights from multiple transcripts into your analysis.

---

## Practical Tips

### Tip 1: Pipe to Select-Object for Long Output
Transcripts can be very long. Use PowerShell to limit output:

```powershell
filmot transcript VIDEO_ID --full 2>&1 | Select-Object -First 200
```

Adjust the number based on how much context you need.

### Tip 2: Video IDs Starting with Dash
Some YouTube video IDs start with `-` (e.g., `-O1bjFPgRQM`). These work directly now:

```bash
filmot transcript -O1bjFPgRQM --full
```

### Tip 3: Check Result Count First
The result count tells you how much content exists:
- 50-200 results: Good, focused topic
- 500-2000 results: Broad topic, may need filtering
- 10,000+ results: Too broad, add more specific terms

### Tip 4: Channel Quality Indicators
In search results, note:
- Subscriber counts (higher often = more reliable)
- View counts (popular = vetted by audience)
- Channel category (Science & Technology, Education = usually substantive)

### Tip 5: Conference Talks Are Gold
Search for conference content—it's usually high-quality:

```bash
filmot search '"CES 2026"|"39C3"|"DEF CON"' --full --lang en
```

### Tip 6: Use Expert Names
If you know experts in a field, search for them:

```bash
filmot search '"Meredith Whitaker"|"Signal president"' --full --lang en
```

### Tip 7: Combine Concepts for Precision
Instead of single terms, combine related concepts:

```bash
# Instead of just "AI safety"
filmot search '"AI safety" regulation|"AI governance" policy' --full --lang en
```

### Tip 8: Manual vs Auto Subtitles
By default, Filmot searches **auto-generated subtitles** (YouTube's speech-to-text). Use `--manual-subs` to search **manually uploaded subtitles** only:

```bash
# Search auto subs (default) - better coverage
filmot search 'quantum computing' --full --lang en

# Search manual subs only - often higher quality
filmot search 'quantum computing' --manual-subs --full --lang en
```

**Important:** You cannot search both in the same request. Auto subs have far more coverage; manual subs are rarer but more accurate.

### Tip 9: Title Search for Proper Nouns/Neologisms (Critical!)
**This is crucial for tracking viral phenomena or brand names.** 

Phonetic transcription doesn't reliably capture proper nouns, brand names, or neologisms. For example, "Clawdbot" might be transcribed as "clawd bot", "claude bot", or missed entirely.

**The workaround:** Use generic transcript terms combined with the `--title` filter:

```bash
# WRONG: Direct search often returns nothing
filmot search "clawdbot" --full

# RIGHT: Search generic terms, filter by title
filmot search 'AI|robot|open source' --title "clawdbot" --full
```

**Important limitation:** The CLI returns results weighted by relevance/views, which means it will **miss zero-view origin videos**. For true viral archaeology (finding the first video ever made on a topic), you may need:
1. The Filmot website's date sort (oldest first)
2. A CSV export from Filmot website

**Real example:** Tracking Clawdbot's viral rise
- CLI search found 169 videos but missed the true origin
- Website date-sorted search revealed: **Jan 15, 2026** - "Open Source Friday with Clawdbot 🦀" by Andrea Griffiths (0 views, 151 subs) - the inventor's first interview
- The CLI missed this because: 0 views = low relevance, no transcript = no matches

**Key insight:** When researching viral phenomena, the CLI excels at finding the *explosion* (high-view videos), but the website excels at finding the *spark* (origin videos).

---

## Understanding Search Results

Each result shows:
```
1. Video Title
   Channel: Name (Subscriber count) | Country: XX
   Views: N | Likes: N | Duration: Xm Ys
   Category: Category | Language: en | Uploaded: YYYY-MM-DD
   Video: https://youtube.com/watch?v=VIDEO_ID
   Matches (N):
      [MM:SS] ...context around match...
      [MM:SS] ...another match...
```

**What to look for:**
- **Match timestamps**: Shows where in the video the topic appears
- **Match context**: The actual words spoken—scan for relevance
- **Multiple matches**: More matches = more substantial coverage
- **Duration**: Longer videos with many matches = deep dives

---

## Transcript Output

When you run `filmot transcript VIDEO_ID --full`, you get:

```
┌────────────────────────────── Transcript Info ──────────────────────────────┐
│ Video ID: XXXXXXXXXXX                                                       │
│ Language: en (auto-generated or manual)                                     │
│ Duration: X:XX:XX                                                           │
│ Segments: N                                                                 │
└─────────────────────────────────────────────────────────────────────────────┘

[Full transcript text follows as continuous prose...]
```

The text is formatted for AI consumption—no timestamps interrupting the flow.

---

## Common Research Patterns

### Pattern 1: Current Events Research
```bash
# Step 1: What happened this month?
filmot search 'TOPIC' --start-date 2026-01-01 --end-date 2026-02-01 --full --lang en

# Step 2: Get transcripts of key videos
filmot transcript VIDEO_ID --full
```

### Pattern 2: Technical Deep Dive
```bash
# Step 1: Find explainer content
filmot search '"CONCEPT" explained|"CONCEPT" tutorial|"CONCEPT" how works' --full --lang en

# Step 2: Find expert discussions
filmot search '"CONCEPT"' --full --lang en
# Look for university channels, conference talks, known experts

# Step 3: Get full transcripts of best matches
filmot transcript VIDEO_ID --full
```

### Pattern 3: Controversy/Multiple Perspectives
```bash
# Search for different viewpoints
filmot search '"TOPIC" criticism|"TOPIC" problems' --full --lang en
filmot search '"TOPIC" benefits|"TOPIC" success' --full --lang en
```

### Pattern 4: Finding Primary Sources
```bash
# Company announcements
filmot search '"COMPANY" announcement|"COMPANY" keynote|"COMPANY" CEO' --full --lang en

# Conference presentations
filmot search '"PERSON NAME" talk|"PERSON NAME" presentation' --full --lang en
```

---

## What I Researched (Real Examples)

### Session 1: AI Company Analysis
**Goal:** Understand current state of OpenAI and Anthropic

**Commands run:**
```bash
filmot search 'OpenAI|"Sam Altman"' --start-date 2026-01-01 --end-date 2026-02-01 --full --lang en
# Found 458 results including Musk lawsuit, financial troubles, PRISM launch

filmot search 'Anthropic|"Dario Amodei"|Claude' --start-date 2026-01-01 --end-date 2026-02-01 --full --lang en
# Found 844 results including Dario's essay, Claude psychological issues

# Then got full transcripts of key videos
filmot transcript kSno1-xOjwI --full  # Security analysis
filmot transcript TTMOSR-nKjg --full  # Industry news roundup
```

**Output:** Comprehensive analysis of both companies' financial situations, product launches, and controversies.

### Session 2: Security Research (Prompt Injection)
**Goal:** Understand how prompt injection attacks work technically

**Commands run:**
```bash
filmot search '"prompt injection"' --start-date 2026-01-01 --end-date 2026-02-01 --full --lang en
# Found 158 results

filmot search '"prompt injection" "how it works"|"indirect prompt injection"' --full --lang en
# Found explainer content

# Key transcripts
filmot transcript rAEqP9VEhe8 --full  # Computerphile technical explanation
filmot transcript -O1bjFPgRQM --full  # David Bombal zero-click demo
filmot transcript 0ANECpNdt-4 --full  # 39C3 conference talk
filmot transcript Qvx2sVgQ-u0 --full  # NetworkChuck hacking tutorial
```

**Output:** Complete technical understanding of attack vectors, real-world demos, and defense strategies.

### Session 3: Technology Trends (Humanoid Robots)
**Goal:** What's happening in humanoid robotics right now?

**Commands run:**
```bash
filmot search 'humanoid robot|humanoid robots' --start-date 2025-12-01 --end-date 2026-02-01 --full --lang en
# Found 4,745 results - too broad

filmot search 'Tesla Optimus|Figure robot|Boston Dynamics|Unitree' --start-date 2025-12-01 --end-date 2026-02-01 --full --lang en
# Found 898 results - more focused

filmot search '"humanoid robot" factory|"humanoid robot" manufacturing' --full --lang en
# Found deployment news

# Key transcripts
filmot transcript UrB2tQDVLLo --full  # Musk at Davos
filmot transcript fadawnuE6n8 --full  # Hyundai/Boston Dynamics CES
filmot transcript ai9Az88t2-s --full  # Tesla factory conversion news
```

**Output:** Understanding of current production timelines, pricing, and deployment status across major players.

---

## Errors and Edge Cases

### "Video is unavailable"
Some videos are region-locked, deleted, or private. Move on to another source.

### No transcript available
Some videos don't have transcripts (live streams, music, etc.). The command will return an error.

### IP Blocked
YouTube may block your IP for transcript requests. Use proxy options:

```bash
# Use a specific proxy
filmot transcript VIDEO_ID --proxy http://user:pass@host:port --full

# Or set WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD in .env

# To bypass proxy and connect directly (even if env vars are set)
filmot transcript VIDEO_ID --no-proxy --full
```

### Rate limiting
If making many requests, you might hit API limits. Space out requests if needed.

### Very long transcripts
For 2+ hour videos, the transcript can be 100K+ characters. Use `Select-Object -First N` to get manageable chunks, or save to file:

```bash
filmot transcript VIDEO_ID --full -o transcript.txt
```

---

## Key Insight

The power of this tool is **finding what people actually said**, not what videos are titled. This means:

1. You can find discussions of topics that weren't the main subject of a video
2. You can verify claims by finding primary sources
3. You can research topics that don't have dedicated videos yet (they're discussed inside other content)
4. You can find expert opinions embedded in interviews and podcasts

**YouTube is the world's largest repository of human knowledge in spoken form. This tool lets you search it.**

---

## Quick Reference

| Task | Command |
|------|---------|
| Multi-channel search | `filmot search "query" --channel-id UC1,UC2,UC3 --full` |
| Sort by likes | `filmot search "query" --sort likecount --order desc --full` |
| Manual subs only | `filmot search "query" --manual-subs --full` |
| Basic search | `filmot search "query" --full --lang en` |
| Phrase search | `filmot search '"exact phrase"' --full --lang en` |
| OR search | `filmot search 'term1\|term2' --full --lang en` |
| Date filter | `filmot search "query" --start-date YYYY-MM-DD --end-date YYYY-MM-DD --full` |
| Get transcript | `filmot transcript VIDEO_ID --full` |
| Save transcript | `filmot transcript VIDEO_ID --full -o filename.txt` |
| Limit output | `filmot transcript VIDEO_ID --full 2>&1 \| Select-Object -First 200` |

---

*This guide was written based on actual research sessions using Filmot CLI. The examples are real queries that produced useful results.*
