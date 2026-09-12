# Filmot Low-Cognitive-Load Field Test

This log records friction observed while using Filmot for genuine research.
It distinguishes operational load imposed by the tool from the inherent work
of understanding evidence and ideas.

## Baseline — 2026-08-30

- Goal contract: `context/goal.md` (local, ignored workspace context).
- Code baseline: `main` at `caaa8e0`; no semantic uncommitted source changes
  before this log was created. The existing tracked-file noise is LF-to-CRLF
  conversion and is intentionally preserved.
- Environment: created the ignored `.venv` and installed `.[dev]` from
  `pyproject.toml`; the base interpreter lacked Filmot runtime dependencies.
- Config check: Filmot reports its API credential as configured without
  exposing it. Project research data resolves to `.filmot_data/` and
  machine-level state resolves under the user directories.
- Baseline test result: `TMPDIR=/var/tmp .venv/bin/python -m pytest -q`
  produced 500 passes and one setup error. The error is recorded as F-001.

## Investigation 1 — Active inference and reinforcement learning

### Research question

How do technical speakers distinguish active inference from model-based
reinforcement learning, and which claims about exploration, planning, and
sample efficiency are tied to concrete mechanisms rather than metaphor?

### Why this question

This is a genuine conceptual interest: the two frameworks often use different
vocabularies for beliefs, policies, objectives, and uncertainty while making
apparently overlapping claims about intelligent action. It should also expose
whether Filmot can carry a research thread from broad discovery into exact
passages, source comparison, claims, and resumable synthesis.

### Friction log

#### F-001 — Unbounded Click dependency breaks a documented test environment

- Status: fixed and regression-tested.

- Stage: baseline verification.
- Command: `TMPDIR=/var/tmp .venv/bin/python -m pytest -q` after installing
  `.[dev]` into a new virtual environment.
- Expected: the supported dependency declaration produces a green suite.
- Observed: 500 tests passed, but `tests/test_review_regressions.py` failed in
  fixture setup because Click 8.5 removed the `CliRunner(mix_stderr=False)`
  constructor argument. The affected product regression never ran.
- Cognitive cost: a fresh contributor must diagnose whether this is a product
  failure, a WSL issue, or dependency drift before trusting the baseline.
- Classification: reproducibility / dependency compatibility.
- Severity: medium; occurs on every fresh install that resolves Click 8.5.
- Reproducible: yes.
- Resolution: use the argument-free `CliRunner`, whose `Result` object exposes
  separate stdout and stderr on both the older supported Click releases and
  Click 8.5; the removed constructor argument was test-harness coupling rather
  than a product requirement.
- Verification: `tests/test_review_regressions.py` passes under the freshly
  resolved Click 8.5 environment.

#### F-002 — Freshness scout fills research depth with lexical false positives

- Status: fixed after two gate iterations, regression-tested, and verified in
  the original workflow.

- Stage: `research` candidate selection.
- Command: `filmot research "active inference reinforcement learning" -n 6
  --dedupe --probe`.
- Expected: after strong relationship-preserving Filmot candidates, scout
  additions should still refer to the technical framework "active inference"
  in relation to reinforcement learning.
- Observed: scout candidates about LLM inference-time reasoning and a generic
  brain-realities video passed the displayed relevance gate. One LLM-RL video
  was selected and saved even though it does not discuss active inference.
- Cognitive cost: the researcher must manually re-screen sources after the
  command has already presented its gate as topic relevance protection.
- Classification: correctness / ambiguous filtering / corpus pollution.
- Severity: high; naturally reproduced in the first investigation.
- Reproducible: captured in run `197c6cbd2800` and the topic session.
- Iteration evidence: an ordered, same-passage topic-token gate reduced scout
  candidates from 10 to 1 in retry run `e973ecd45997`, but that survivor was
  the same `Does Your Brain Build Thousands of Realities?` false positive. It
  consumed one of two download slots, incurred a 30-second route timeout plus
  retry, and was saved. Passing a stronger lexical gate therefore still did
  not justify automatic corpus admission; diagnosis continues.
- Resolution: scout admission now requires one ordered topic span in the title
  or two non-duplicate corroborating spans across description/hits. A long
  related-topics enumeration no longer counts as several independent pieces of
  evidence. The hidden scout quota was also removed: download selection follows
  the displayed global rank, so a weaker scout cannot displace a stronger
  Filmot candidate merely to satisfy source-type diversity.
- Verification: focused false-positive, multilingual, repeated-passage, and
  depth-ranking regressions passed. Replay run `409aa849c6f6` found the same 10
  recent uploads and admitted zero; the two globally highest Filmot candidates
  occupied both requested slots. Neither historic scout false positive was
  fetched again.

#### F-003 — Probe extraction spends minutes on generic words and pollutes corpus

- Status: fixed after two iterations, regression-tested, and verified in the
  original workflow.

- Stage: `research --probe`.
- Same command/run as F-002.
- Expected: cross-source probes expose topic-specific relationships that are
  surprising enough to merit follow-up.
- Observed: extracted entities included `data`, `model`, `models`, `trying`,
  and `basically`. Queries such as `"data" NEAR/15 "model"` returned
  383,895 API hits; `"basically" NEAR/15 "model"` returned 534,120. The
  phase then saved a generic data-science course, a small-language-model talk,
  and a tropical-weather report into the active-inference corpus.
- Cognitive cost: several minutes of waiting, five low-information API calls,
  manual relevance auditing, and a now-contaminated corpus that cannot simply
  be trusted downstream.
- Classification: correctness / unnecessary repetition / poor default.
- Severity: critical; repeated within one run and directly degrades research
  state.
- Reproducible: captured in run `197c6cbd2800`.
- Iteration evidence: requiring multiword English terms and rejecting
  singular/plural self-pairs removed the original `data`/`model`/`basically`
  probes. In retry `e973ecd45997`, all automatically saved probe sources were
  topically relevant, and provenance linked both to `"free energy" NEAR/15
  "machine learning"`. However, Filmot still spent two long calls on generic
  `machine learning`/`neural network` and `deep learning`/`neural network`
  pairs (9,797 and 9,655 API results, zero scoped discoveries). The pollution
  failure improved; the generic-query cognitive and latency cost remains.
- Resolution: only selected/manual sources may seed an automatic probe; scout
  and prior probe discoveries cannot recursively shape the next frontier.
  Relationship pairs now use a source-normalized, language-neutral salience
  score rather than treating every bigram as equally specific. The phase stops
  after filling its three-discovery capacity, and a sampled high-cardinality
  query with no coherent returned candidate stops the lower-ranked tail while
  reporting sample coverage and an exact manual follow-up command. It does not
  claim that the global corpus contains no result.
- Verification: 98 CLI tests passed, including live-shaped long transcripts,
  multilingual terms, frontier quarantine, broad/coherent retention, sampled
  broad-zero stopping, and capacity stopping. Replay `409aa849c6f6` used four
  eligible selected sources, explicitly excluded seven automatic frontier
  sources, and executed five topic-specific queries with API totals of 54, 11,
  15, 7, and 11. It completed in about 13 seconds with no irrelevant download;
  the earlier six-figure queries and corpus pollution did not recur.

#### F-004 — Session summary cannot explain scout/probe source provenance

- Status: fixed, regression-tested, and verified against the original session.

- Stage: resumption after the research run.
- Command: `filmot sessions active-inference-reinforcement-learning --summary`.
- Expected: enough state to understand why eight sources were saved without
  replaying the raw ledger.
- Observed: the summary shows the three Filmot search stages and aggregate
  probe counts, but not the scout gate/candidates, probe queries, or which
  sources came from which probe. Explaining the irrelevant sources requires
  remembering terminal output or inspecting detailed events.
- Cognitive cost: mechanical state reconstruction during resumption.
- Classification: missing state / resumability.
- Severity: high when a compound run mixes selected, scout, and probe sources.
- Reproducible: yes.
- Resolution: session summaries now include bounded scout-run and gate rows,
  deduplicated probe queries with their basis and scope counts, saved-source
  origin totals, and a per-source discovery stage/query. New probe checkpoints
  preserve query/index/title/channel; older records explicitly say
  `query not recorded` instead of guessing a link.
- Verification: 18 focused provenance tests passed. Replaying the original
  session now identifies four proximity sources, one scout source, and three
  probe sources; the five generic historic probes and their six-figure API
  result counts are visible without reading JSONL or remembering terminal
  output.

#### F-005 — Citation output and claim input use incompatible timestamp formats

- Status: fixed, regression-tested, and verified in the original workflow.

- Stage: passage-to-evidence handoff.
- Commands:
  - `filmot library search "expected free energy" --topic
    active-inference-reinforcement-learning`
  - `filmot claims cite ... --video fWOuX8NuQck --at 2:23:18 ...`
- Expected: copy the displayed citation timestamp into the evidence command.
- Observed: discovery output uses `H:MM:SS`, while `claims cite --at` accepts
  only floating-point seconds. Click rejected `2:23:18`; the researcher had to
  calculate and enter `8598` manually.
- Cognitive cost: arithmetic and format translation at every citation, with a
  meaningful risk of attaching evidence to the wrong moment.
- Classification: inconsistent interfaces / manual transformation.
- Severity: high because this is on the core search-to-claim path.
- Reproducible: yes.
- Resolution: `claims cite --at` now accepts finite non-negative seconds,
  `M:SS`, or `H:MM:SS`, while continuing to persist canonical numeric seconds.
  Invalid human input remains an actionable usage error and raw mode returns a
  typed failure without touching claim state.
- Verification: focused claim and regression tests passed (54 total across the
  two files). Repeating the real citation with `--at 2:23:18` resolved to the
  existing `8598.0s` evidence and returned `Already attached`, proving that the
  display-form handoff is canonical and idempotent.

#### F-006 — Exact probing refetches an already saved transcript and still omits a link

- Status: fixed after two implementation passes, adversarial review, full-suite
  regression testing, and human/raw field replay.

- Stage: narrowing a local passage before citation.
- Command: `filmot transcript fWOuX8NuQck --grep '"expected free energy"
  NEAR/30 "policies"'` after the same video was already in the topic library.
- Expected: reuse the saved transcript and produce a citation-ready locator.
- Observed: Filmot ran the external transcript route ladder again (about seven
  seconds and a proxy dependency), then printed `[2:23:18]` without a clickable
  timestamped URL. `--grep` cannot be combined with `--raw`, while local
  library search does not accept the proximity expression used here.
- Cognitive cost: needless waiting/failure exposure plus manual locator work.
- Classification: unnecessary repetition / missing affordance.
- Severity: medium-high; likely to recur for every claim-bearing passage.
- Reproducible: yes.
- Resolution: `transcript --grep` now checks for one unambiguous compatible
  local transcript with trustworthy stored segments before configuring any
  external route. It falls back normally for ambiguity, language mismatch, or
  invalid legacy timing. Human matches print the literal timestamped YouTube
  URL as well as the readable timestamp.
- Verification: 139 broader CLI/search/transcript tests passed in the focused
  implementation run. Replaying the exact command used the local
  `active-inference-reinforcement-learning` record, made no external fetch,
  and printed `https://youtube.com/watch?v=fWOuX8NuQck&t=8598s` with the
  `[2:23:18]` match.
- Gaps found immediately afterward: cache ambiguity was decided
  before language/validity filtering; identical copies forced a refetch;
  non-monotonic stored segment times were trusted; malformed grep syntax could
  log `completed` before exiting with an error; local grep was not routed to
  its source session; and `--raw --grep` still had no machine-readable match
  contract. These are part of F-006 rather than new feature requests because
  ambiguity, recovery, raw output, and accurate resumability were present in
  the original friction report. These findings triggered a second pass.
- Second-pass resolution: one typed evaluator now feeds both renderers;
  language/integrity filtering precedes ambiguity, equivalent copies collapse,
  non-monotonic timing falls back with an explanation, and raw matches expose
  query/count/seconds/timestamp/deep-link/excerpt. Failed parsing is logged as
  failed and unique local hits route their compact activity to the source
  topic. The second pass produced 23 focused, 150 broader, and 547 full-suite
  passes. Independent human/raw replays returned the same local match at
  `8598.88s` / `2:23:18`; a malformed raw proximity expression returned a
  typed `InvalidGrepQuery` at `parse-query` with exit 1.

#### F-007 — Scout results cannot be reproduced or inspected through `yt-search`

- Status: fixed, regression-tested, and verified against the original scout.
- Stage: diagnosing why a recent scout candidate passed admission.
- Commands: the retry `research` run followed within minutes by
  `filmot yt-search "active inference reinforcement learning" --days 7
  --max-results 10 --show-description`.
- Expected: the direct scout command exposes the same recent candidate set and
  descriptions used by `research`, allowing the admission decision to be
  audited without source-code or storage inspection.
- Observed: `research` found 10 recent videos, while the immediately following
  direct command reported `No videos found`. `yt-search` also offers no raw
  result mode for inspecting candidate metadata mechanically.
- Cognitive cost: the displayed scout gate could not be reproduced at its
  documented lower-level command, so diagnosis required triangulating the
  saved transcript, session checkpoints, and implementation.
- Classification: inconsistent commands / ambiguous empty state /
  reproducibility / missing machine-readable inspection.
- Severity: medium if recurrent; one observation so far.
- Reproducible: yes. Diagnosis showed that `research` requested
  `order=relevance`, while the otherwise identical direct command used
  `yt-search`'s documented `order=date` default. Supplying `--order relevance`
  returned the same 10 candidates, establishing a hidden-parameter mismatch
  rather than an API inconsistency.
- Resolution: research scout checkpoints preserve days, order, maximum result
  count, and the actual YouTube channel filter separately from any later
  multi-channel post-filter. Session summaries render those parameters and one
  copyable inspection command. `yt-search --raw` now provides typed completed,
  empty, configuration-failure, request-failure, and serialization-failure
  results with the effective request and exact candidate metadata.
- Verification: 90 adjacent search/library tests passed. Run `b1325c1bef32`
  produced `7d / relevance / n=10` in the session summary and the exact command
  `filmot yt-search 'active inference reinforcement learning' --days 7
  --max-results 10 --order relevance --show-description --raw`. Executing that
  command returned the same 10 candidate IDs in the same order as the research
  scout.

#### F-008 — Scout-only lexical diagnostics appear on every candidate

- Status: fixed, regression-tested, and verified in a live preview.
- Stage: candidate inspection after the F-002/F-003 replay.
- Command: `filmot research "active inference reinforcement learning" --depth
  2 --dedupe --probe` (run `409aa849c6f6`).
- Expected: selection details expose only signals that apply to each candidate.
- Observed: all Filmot proximity candidates printed
  `lexical-spans=t0/d0/h0`. Those counters govern scout admission and were not
  the reason the Filmot candidates qualified, so the repeated zeros looked like
  missing relevance evidence.
- Cognitive cost: the researcher must remember which stage owns an otherwise
  unexplained diagnostic while screening a mixed candidate list.
- Classification: ambiguous output / unnecessary verbosity.
- Severity: low-medium; it appears on every candidate preview when scouting is
  enabled.
- Reproducible: yes.
- Resolution: the preview now emits one explicitly named
  `scout-lexical-spans` detail only for scout-origin candidates. Filmot rows
  retain their applicable relevance, density, and source-prior signals.
- Verification: the mixed-origin regression asserts one scout-specific detail
  and none on Filmot rows. Replay `b1325c1bef32` printed eight Filmot candidates
  without the misleading zero counters.

#### F-009 — Invalid grep can retrieve data before failing or become a full download

- Status: fixed, regression-tested, and verified with live raw commands.
- Stage: adversarial replay of the F-006 raw contract.
- Commands: `filmot transcript VIDEO --grep '' --raw` and a malformed
  `"alpha" NEAR/5 beta` proximity expression for an uncached video.
- Expected: query validation fails before any library, proxy, or network work,
  and every supplied `--grep` value retains the grep result schema.
- Observed: truthiness checks treated an empty string as if `--grep` were
  absent, fetched the full transcript, and emitted its raw contents. A malformed
  non-empty expression was parsed only after local or external retrieval.
- Cognitive cost: a typo could trigger unexpected latency and data volume, and
  the researcher had to know validation occurred late in order to predict
  whether a failing command was safe to retry.
- Classification: correctness / raw-contract violation / poor recovery.
- Severity: high; the empty case could expose a complete transcript where a
  bounded match result was requested.
- Reproducible: yes, in the integration audit.
- Resolution: any provided `--grep` value is preflighted through the shared
  parser before local lookup, route configuration, or retrieval. Blank and
  malformed values return the same typed `InvalidGrepQuery` contract and are
  logged as parse failures without attaching them to an unrelated topic.
- Verification: 25 transcript contract tests passed. Live blank and malformed
  raw commands both exited 1 in about 0.4 seconds, emitted zero matches with
  `stage=parse-query`, and displayed no route or fetch activity.

#### F-010 — `yt-search --raw --transcript` silently skips transcript search

- Status: fixed, regression-tested, and verified through the real CLI.
- Stage: machine-readable inspection added for F-007.
- Command: `filmot yt-search QUERY --transcript --transcript-query TERM --raw`.
- Expected: raw and human modes perform the same requested work and differ only
  in presentation.
- Observed: transcript searching lived inside the human renderer. Raw mode
  returned before that renderer, claimed success, and contained only video
  metadata despite preserving `transcript=true` in the payload.
- Cognitive cost: automation had to know that a visible option was inert in raw
  mode or repeat the work with separate commands.
- Classification: correctness / human-raw divergence / silent partial work.
- Severity: high for automated research.
- Reproducible: yes, in the integration audit.
- Resolution: transcript evaluation now precedes both renderers and attaches a
  per-video typed `transcript_search` object with query, status, count, matches,
  and bounded failure details. Optional transcript failures make the overall
  discovery result `partial` while preserving successful video metadata.
- Verification: 42 search contract tests passed. A real one-result raw search
  fetched generated English captions and returned an explicit `empty`
  transcript search for `expected free energy`, rather than silently omitting
  the operation.

#### F-011 — Session provenance turns deferred work into completed work

- Status: fixed and regression-tested.
- Stage: resuming the newly bounded probe workflow.
- Expected: the summary distinguishes executed, sampled, deferred, failed, and
  completed probe requests, and links saved sources to the stage query that
  selected them.
- Observed: append-only event normalization correctly stored noncanonical
  states in `detail_status`, but the summary read only the canonical status.
  `deferred` and `broad_sampled` rows therefore appeared `completed`, sometimes
  beside zero API results. Separately, search used `title+transcript` while
  legacy selected-source metadata used `title_transcript`, so an exact stage
  join falsely reported `origin_not_recorded`.
- Cognitive cost: resumption could not tell whether a query had run and could
  claim provenance was missing even though it existed.
- Classification: incorrect state / resumability / backward compatibility.
- Severity: medium-high because the summary is the intended memory substitute.
- Reproducible: yes, with folded synthetic events matching real checkpoints.
- Resolution: the bounded summary preserves the operational states
  `broad_sampled`, `deferred`, and `failed_closed`; it also canonicalizes the
  legacy title-stage alias while new research writes `title+transcript`.
- Verification: focused tests prove both state preservation and recovery of the
  legacy stage's recorded origin query.

#### F-012 — An “exact” manual probe command silently drops effective scope

- Status: fixed and regression-tested.
- Stage: recovery after the broad sampled-zero stop.
- Expected: the suggested manual command reproduces the same query universe the
  automatic probe sampled.
- Observed: it included the expression and language but omitted the active
  title and channel constraints, so scoped investigations received a broader
  command labeled `Exact manual query`.
- Cognitive cost: the researcher had to reconstruct hidden scope from earlier
  output and could draw conclusions from a different result universe.
- Classification: misleading recovery / missing state / reproducibility.
- Severity: medium; it occurs only on the protective broad-tail stop but that is
  precisely when careful manual inspection matters.
- Reproducible: yes, in the live-shaped broad-probe regression.
- Resolution: the command is built as an argument vector and shell-quoted with
  the effective query, language, title, and channel ID set. The obsolete weaker
  loose-relevance helpers were removed at the same time to leave one admission
  contract.
- Verification: the scoped regression asserts the complete copyable command,
  including `--title` and `--channel-id`.

### Early research evidence

`library compare "reinforcement learning" --topic
active-inference-reinforcement-learning --sort density` exposed several useful
passages despite the corpus contamination:

- Karl Friston distinguishes active inference by placing both state inference
  and learning of generative-model parameters inside the framework, rather
  than treating the system only as reward-backed action learning.
- Bert de Vries describes the conceptual contrast as variational free-energy
  minimization versus maximizing expected value over future states, while also
  making an implementation claim about graceful operation under fluctuating
  computational resources.
- The Applied Active Inference Symposium explicitly acknowledges that
  scalability remains challenging and has received far fewer engineering
  hours than reinforcement learning, while another speaker attributes sample
  efficiency to rapid global model restructuring after small prediction
  errors. These are claims to investigate, not established conclusions.
- Retry run `e973ecd45997` found two genuinely useful probe sources through
  `"free energy" NEAR/15 "machine learning"`: a Karl Friston interview and a
  Maxwell Ramstead interview. Both came from Machine Learning Street Talk, so
  their topical usefulness must not be mislabeled as independent channel-level
  corroboration.
- The claim register now preserves six exact statements and evidence links:
  expected-free-energy policy selection (`c-26ae9972421f`), De Vries's
  objective-function contrast (`c-5f1374d6d248`), the symposium scalability
  caveat (`c-179d0026b8d7`), the presenter's proposed sample-efficiency
  mechanism (`c-2467975069ed`), and Friston's information-gain/prior-preference
  framing of exploration (`c-138aa7d7236c`). A sixth claim records Friston's
  limiting-case description of reinforcement learning when uncertainty is
  removed, together with his warning that doing so removes information seeking
  (`c-878d777cf5df`). Assessments explicitly distinguish confidence in an
  attributed statement from confidence in empirical performance.
- A raw density-sorted concordance for `expected free energy` found 55 matches
  across four relevant sources. The speakers consistently connect it to some
  combination of policy selection, information gain, pragmatic value, prior
  preferences, or uncertainty reduction, but lexical consistency is not proof
  that the mathematical formulations or performance claims are equivalent.
- Persisted echo analysis compared all 11 stored transcripts (55 pairs) and
  found no pair above 0.5 five-gram Jaccard. This is only a reproducible absence
  of high whole-transcript overlap at that threshold—not evidence that the
  speakers, channels, institutes, or ideas are independent.

### Working synthesis

The sources support a mechanism-level distinction, but not a clean opposition.
In the active-inference accounts, policies are evaluated through expected free
energy, which puts epistemic value (information gain or uncertainty reduction)
and pragmatic value (prior preferences) into one objective. De Vries contrasts
that with reinforcement learning's value over desirable future states. Friston,
however, describes reinforcement learning as a limiting case obtained after
removing uncertainty, and immediately notes that the reduction also removes
the information-seeking term. The two statements are best read as different
levels of comparison rather than a contradiction: one emphasizes objective
structure, while the other describes a proposed mathematical containment under
a restrictive assumption.

The sample-efficiency material is weaker than the conceptual material. One
presenter proposes global model restructuring after small prediction errors as
the mechanism, but the stored sources provide no controlled benchmark or
independent replication. The symposium also openly describes scalability as an
engineering challenge and notes the much larger labor investment in
reinforcement learning. The defensible conclusion is therefore that the corpus
contains a coherent mechanistic story about exploration and planning, not
evidence that active inference currently outperforms model-based reinforcement
learning. The relevant voices are concentrated in the active-inference
community and two interviews from one channel, so source independence and
empirical generality remain unresolved.

## Investigation 2 — Deliberate practice and expertise

### Research question

What distinguishes deliberate practice from generic repetition, how much of
expert performance does the evidence actually attribute to it, and which parts
of the popular 10,000-hours story survive the qualifications researchers make?

### Why this question

This is a genuine interest because “practice matters” is simultaneously useful
and almost content-free. The stronger deliberate-practice claim has a specific
mechanism—targeted tasks, feedback, correction, and work near the edge of
current ability—but popular retellings often turn it into a universal hour
counter. The topic should exercise Filmot as an empirical-claim workflow rather
than another comparison between technical formalisms.

### Friction log

This pass began on the fully verified first-cycle CLI (`564 passed`) and
treated clean phases as evidence rather than inventing friction.

#### F-013 — `--depth` stops at the first nonempty search stage

- Status: fixed, regression-tested, and replayed in the original workflow.
- Stage: the initial `research` search ladder.
- Command: `filmot research "deliberate practice expertise" --depth 6
  --dedupe --probe` (run `c77cc2dc54aa`).
- Expected: six is a maximum target. When the strongest stage yields only two
  candidates, Filmot should retain them and continue through safe exact and
  proximity stages until it reaches the target or honestly exhausts the
  relationship-preserving ladder.
- Observed: title+transcript returned two candidates, so the implementation
  treated the search as successful and never tried the remaining stages. The
  output did not say whether two exhausted the topic or merely stopped the
  ladder early.
- Cognitive cost: the researcher had to inspect implementation behavior and
  manually run the exact/proximity queries before knowing whether the corpus
  was genuinely sparse.
- Classification: incomplete work / ambiguous scope / missing next action.
- Severity: high on niche topics; the requested target and actual search depth
  diverged silently.
- Resolution: relationship-preserving stages now accumulate one ordered,
  video-ID-deduplicated pool until the depth target is reached or the ladder is
  exhausted. First admission preserves the strongest origin/query. A nonempty
  underfilled safe pool never triggers the loose transcript-wide fallback just
  to fill a quota; Filmot reports remaining underfill and a targeted exact/NEAR
  next action. Depth zero keeps the Filmot ladder at the title-stage preview,
  downloads no selected candidates, and displays a preview count rather than a
  nonsensical `N/0` progress ratio. An enabled scout may still join that
  preview, and an explicit probe may use existing eligible library seeds.
- Verification: focused CLI tests cover accumulation, duplicate origin,
  stop-at-target API calls, depth zero, safe underfill, and broad non-entry.
  Live replay `258d1effcee3` retained two title candidates, found zero new exact
  candidates, then admitted 35 new proximity candidates for a 37-source safe
  pool. It selected the first six globally ranked rows, skipped three already
  saved transcripts, saved three, and never entered the loose stage.

#### F-014 — A zero-query probe has no human explanation

- Status: fixed, regression-tested, and replayed against the same corpus.
- Stage: `research --probe` after eight eligible seed transcripts.
- Command/run: the F-013 live replay `258d1effcee3`.
- Expected: if Filmot extracts terms but cannot form a supported relationship
  pair, it should say that no query was planned and why.
- Observed: the CLI printed twelve extracted entities and then jumped directly
  to the final `Probe: 0 saved / 0 failed` totals. A researcher could not tell
  whether pair construction was empty, probing was skipped, or an internal
  phase silently failed.
- Cognitive cost: raw-ledger or source inspection to distinguish a legitimate
  empty frontier from incomplete work.
- Classification: ambiguous empty state / resumability.
- Severity: medium; it naturally recurred in both deliberate-practice runs.
- Resolution: the human path now distinguishes `no_candidate_terms` from
  `no_cross_source_pairs`, states that zero queries ran, and persists a terminal
  probe checkpoint with explicit empty status, reason, seed/term counts, and
  query accounting. Session summaries expose bounded terminal probe outcomes
  rather than requiring raw-event inspection.
- Verification: the no-pair regression proves zero API calls and a typed empty
  checkpoint. Replay `11f24a5d8ea6` reported twelve terms, no relationship pair
  meeting cross-source/co-window requirements, and `0 queries run`.

#### F-015 — Successful claim publication leaks one temporary hard link

- Status: fixed and regression-tested; historic names were preserved.
- Stage: strict durable-state audit after claim creation.
- Expected: a successful append-only claim write leaves the immutable event,
  not its publication temporary.
- Observed: all 18 active-inference claim events had an identical hidden
  `.ce-*.tmp` hard-link sibling. `ClaimStore._append` cleaned the temporary only
  on exceptions, while the normal hard-link publisher intentionally left its
  complete source name in place.
- Cognitive cost: growing unexplained storage clutter and apparent duplicate
  claim records during any filesystem-level recovery audit.
- Classification: systematic persistence cleanup defect.
- Severity: medium; one leak per mutation, without corrupting replay.
- Resolution: cleanup now runs in `finally` after both successful and failed
  publication. The no-hardlink rename path remains safe when no source exists.
  Existing temporary names were not deleted because the goal forbids removing
  research data without separate authority.
- Cleanup errors remain operationally precise. If publication succeeded, an
  unlink failure reports the durable destination and exact retained temporary;
  it does not claim rollback. If publication and cleanup both fail, both errors
  are reported, the exact retained temporary is named, and the destination is
  explicitly not confirmed. Claim operations do not scan or delete historical
  or unrelated temporaries. Exact mutation retries are content-idempotent, but
  recovery follows the stated durable/not-confirmed outcome rather than
  guessing from an error alone.
- Verification: focused claim tests cover a successful publisher that retains
  its source, a failed publisher, and publication-plus-cleanup failure. After
  the fix, all deliberate-practice and origin-environment
  claim/evidence/assessment writes left the historic global temporary count
  unchanged at 18.

#### F-016 — Manually saved sources disappear from provenance summary

- Status: fixed, regression-tested, and verified in both later investigations.
- Stage: resuming after manually selecting stronger sources from scoped raw
  searches.
- Expected: all saved transcripts appear in the bounded saved-source table;
  manual choices should be explicit without Filmot inventing the query that
  motivated them.
- Observed: the session counted five transcripts but its provenance table
  showed only the two research-selected sources. The Ericsson, Hambrick, and
  Keep transcripts saved with `transcript --save-to` were omitted entirely.
- Cognitive cost: source selection has to be reconstructed from terminal
  history even though the named session contains the save events.
- Classification: missing state / incomplete resumability.
- Severity: high for the normal manual-screening path.
- Resolution: unmatched successful transcript saves now appear in bounded
  saved provenance as `manual` with an explicitly unrecorded query. Filmot
  never infers a link from preceding searches, and a recorded research/probe
  save takes precedence over a duplicate manual event. New successful
  `transcript --save-to` ledger events carry best-effort title and channel for
  the saved-source row; legacy manual events without those fields remain
  honestly `Unknown`. Terminal probe runs are likewise bounded and keep unknown
  legacy metrics explicit.
- Verification: focused precedence, deduplication, bounds, legacy, and human
  rendering tests passed. Live deliberate-practice summary now reconciles all
  eight transcripts as `manual 3, proximity 3, title+transcript 2` and prints
  `status=empty reason=no_cross_source_pairs` for the final probe. The
  origin-environment summary exposes all five manual sources without inventing
  discovery queries. Those prior live manual rows remain `Unknown` for title or
  channel where their historical events lacked metadata; future successful
  manual-save events carry the best-effort values.

### Research trail and evidence

- The first run stored two title-selected transcripts. Manual scoped searches
  then added a direct Anders Ericsson interview (`7gn3f8sEb8Y`), Zach
  Hambrick's MSU colloquium (`gAg1XBBsLq8`), and Benjamin Keep's methodological
  explainer (`3SZDj5TEhU0`). The F-013 replay added three broader practice
  examples, producing eight stored sources and 113,980 characters in that run.
- Direct local grep reused the saved Ericsson transcript at `5:47`. Ericsson
  contrasts recreational repetition with a teacher identifying one improvable
  aspect and prescribing a training activity for it.
- Hambrick's lecture distinguishes the 1993 violin result (the top groups
  averaged more than 10,000 hours of solitary improvement-oriented practice)
  from Gladwell's later “magic number” framing. The stored lecture is not a
  substitute for inspecting the 1993 paper.
- Hambrick reports roughly 34% of chess-performance variance and about 30% in
  music in studies he summarizes. Those are attributed, domain-specific,
  correlational estimates—not a universal fraction of expertise caused by
  practice.
- Primary-paper cross-checking sharpened both points. Ericsson et al. (1993)
  define deliberate practice as effortful activity designed to optimize
  improvement, while Macnamara, Hambrick, and Oswald's broader 2014
  meta-analysis reports 26% for games and 21% for music. The latter qualifies,
  rather than falsifies, the lecture's earlier subset estimates and reinforces
  why one headline percentage cannot be generalized across domains or
  operational definitions. Both papers are attached to the claim register by
  DOI with precise locators.
- Keep explains why the headline numbers remain disputed: retrospective or
  indirect practice measures depend on operational assumptions. He also makes
  a useful decision-theory distinction: even if practice explains only part of
  between-person variance, deliberate-practice design can still be the best
  controllable instructional intervention. That is a reason to seek comparative
  intervention evidence, not proof that it is always best.
- Five assessed claims preserve these distinctions:
  `c-a69f2918cbf6`, `c-4554e85824c9`, `c-63f95e6ec276`,
  `c-527220de0db4`, and `c-c5e170f5e4dc`. The two Keep citations are marked as
  echo-lineage evidence rather than independent corroboration of Hambrick.
- Persisted five-gram echo analysis compared eight transcripts (28 pairs) and
  found no pair above 0.5 Jaccard. This means only that no stored full-text pair
  crossed that lexical threshold; it does not override the explicit
  intellectual-lineage classification.
- A 164,099-character structured context artifact materializes the complete
  session library for resumption.

### Working synthesis

The durable part of deliberate practice is a design principle, not an hour
counter: diagnose a specific weakness, assign a task targeted at that weakness,
obtain informative feedback, and iterate. The 10,000-hour figure in the stored
history is a group average from one elite-music sample that popular retellings
converted into a threshold. It cannot serve as a universal dose-response law.

The variance debate does not reduce to “practice matters” versus “talent
matters.” Hambrick's reported estimates leave substantial variation unexplained,
while the measurement critique shows that the exact fraction depends on what
researchers count as deliberate practice and how reliably they reconstruct it.
Explained between-person variance also answers a different question from which
training design is most useful to a learner. The evidence here supports the
mechanism, the rejection of a universal hour threshold, and caution about
headline percentages. It does not establish one domain-general causal share or
show that a particular deliberate-practice intervention outperforms every
alternative.

## Investigation 3 — Competing environments for life's origin

### Research question

How do alkaline hydrothermal-vent and terrestrial warm-pond/hot-spring
scenarios solve the energy, concentration, and compartmentalization problems
differently, and which observations constrain rather than prove either
historical setting?

### Why this question

This is a genuine mechanistic interest. “Life began at vents” and “life began
in a warm little pond” sound like competing locations, but each is really a
bundle of proposed solutions to different chemical bottlenecks. Comparing the
mechanisms should reveal whether the evidence discriminates between histories
or only demonstrates that particular steps are possible.

### Friction and recovery log

- The umbrella command `filmot research "origin life environments" --depth 6
  --dedupe --probe` (run `2c36bc3ac573`) produced no relationship-preserving
  candidates and measured 157,021 loose results. It failed closed with an
  exact explanation to refine the topic or explicitly accept broad scope.
  This was a successful recovery contract, not a rough edge. Four narrower
  exact/NEAR searches were routed into `origin-life-environments` and made the
  next action obvious.
- Invalid remembered `search` option names during Investigation 2 were met by
  Click's exact “did you mean” suggestions, and command help contained the
  correct session-routing example. After consulting help once, all later raw
  searches were copyable and predictable; this did not justify another option
  or alias.

#### F-017 — Context export requires pre-creating its parent directory

- Status: fixed, regression-tested, and replayed for Investigations 2 and 3.
- Stage: materializing the final resumable context.
- Commands: `filmot library context TOPIC --format structured --output
  .filmot_data/context/TOPIC.md`.
- Expected: a persistence command creates the explicitly requested nested
  destination safely.
- Observed: both exports failed with `No such file or directory` because the
  parent did not exist. The researcher had to leave Filmot and prepare a
  filesystem path manually.
- Cognitive cost: unnecessary filesystem state management on the documented
  library-to-context handoff.
- Classification: missing safe default / poor persistence ergonomics.
- Severity: medium; any new nested output path reproduced it.
- Resolution: parent creation now occurs inside the existing guarded
  `write-output` block. Directory and file failures retain the same typed error
  and failed-delivery event; the command does not hide write errors.
- Verification: focused contracts cover nested success and parent-creation
  failure. Replaying the exact commands saved structured artifacts of 164,099
  and 206,666 characters without any preparatory shell operation.

#### F-018 — Legitimately empty discovery skips an explicit probe

- Status: fixed, regression-tested, and replayed through the live CLI.
- Stage: `research --probe` after the current discovery ladder returns no
  selectable candidates while eligible transcripts already exist in the topic
  library.
- Expected: record the current selection as empty, then honor the explicit
  probe request using eligible preexisting selected/manual seeds and emit its
  terminal query accounting.
- Observed: the empty-selection path returned before probe planning, so an
  explicitly requested probe was silently skipped even though its seed corpus
  was independent of the current selection.
- Cognitive cost: the researcher had to infer from missing output whether the
  probe frontier was empty, unsafe, or never entered.
- Classification: incomplete explicit work / ambiguous terminal state.
- Severity: high for resumed research over an existing library; a legitimate
  empty discovery concealed usable prior work.
- Resolution: a legitimate empty discovery now persists the empty selection
  and continues into an explicit probe over eligible preexisting topic-library
  seeds at any depth. Probe completion or zero-query terminal reasons remain
  visible. Fatal broad-scope safety gates still fail closed and do not continue
  into probe work.
- Verification: focused regressions cover the empty-selection continuation and
  terminal probe accounting. Live run `6a33217172d4` resumed the existing
  active-inference investigation with `--no-scout --depth 0 --probe --dedupe`.
  Its title+transcript scope legitimately returned zero, Filmot explicitly
  recorded/announced the empty current selection, admitted four saved eligible
  seeds while excluding seven automatic frontier sources, executed all five
  supported probes, found no new scoped candidate, and ended `completed` with
  zero query/download failures. The raw session summary independently exposes
  `filmot_total=0`, `selected=0`, and a terminal probe row with four seeds,
  twelve terms, five executed/planned queries, zero deferred/failures/saves.
  The two deliberate-practice depth-zero replays still had two preview
  candidates and are not misrepresented as evidence for this branch.

### Research trail and evidence

- Scoped Filmot searches found a direct Nick Lane Royal Society lecture
  (`PhPrirmk8F4`), a direct David Deamer interview (`3lJ_VFkKiqg`), Tara
  Djokic's geological talk (`idfv7Lw4Y_s`), Martin Van Kranendonk's geology
  plenary (`nHWHjc2gnGs`), and a NASA briefing (`4KgAfNIYlns`). All five
  transcripts are stored in the named session/library.
- Lane's vent account uses a naturally occurring proton gradient between
  alkaline vent fluid and the early ocean as an energy source. Porous mineral
  rock supplies reaction-scale spaces and hydrogen/carbon-dioxide feedstocks.
  This is a proposed continuity with the proton gradients used by cells, not an
  observation of abiogenesis.
- Deamer's surface account uses repeated wet/dry cycles as a concentration
  pump: dilute compounds become a thin organic film as water leaves, increasing
  reaction opportunity. Van Kranendonk presents dry-state dehydration as a
  route to polymer bonds and reports that the demonstrated fatty-acid vesicle
  process disperses in salty water. Both claims come from the same broad
  surface-origin lineage and are not independent refutations of all vent
  mechanisms.
- The primary literature matches the mechanism-level reading: Lane and Martin
  (2012) explicitly propose natural proton gradients across thin vent mineral
  barriers and protocells in pores; Damer and Deamer (2020) present the
  hydration/dehydration synthesis and encapsulation sequence as a testable hot
  spring hypothesis. These papers strengthen attribution without converting
  either scenario into observed history, and are attached by DOI.
- Djokic reports geyserite and evidence of life in a 3.5-billion-year-old
  terrestrial hot-spring setting. Her own conclusion is appropriately bounded:
  it makes a warm-pond setting reasonable while leaving life's actual origin
  debatable. Evidence that life inhabited an environment is not evidence that
  the first life originated there. The 2017 Nature Communications paper is
  attached as the primary geological source for that bounded claim.
- Four assessed claims preserve the mechanism and caveats:
  `c-4993d877311c`, `c-71edb1adbec6`, `c-f48f03de7900`, and
  `c-9684417d49ae`. Direct Lane, Deamer, and Djokic evidence is classified as
  primary for the attributed statements; Van Kranendonk's Deamer-lineage
  summary is explicitly secondary/echo.
- Persisted five-gram echo analysis compared five transcripts (10 pairs) and
  found no pair above 0.5 Jaccard. As before, this lexical result cannot prove
  intellectual independence. A 206,666-character structured context artifact
  preserves the complete stored library.

### Working synthesis

The hypotheses optimize different bottlenecks. The vent model starts with a
continuous geochemical energy gradient and mineral microstructure that can act
as primitive reaction spaces. The surface model starts with episodic
concentration and dehydration, which make condensation chemistry and fatty-acid
compartment formation easier in the demonstrated systems. Each model's strength
therefore exposes the other's question: vents need a convincing route for
concentration, polymers, membranes, and eventual independence from mineral
gradients; surface pools need credible feedstocks, cycling conditions,
protection/stability, and a route into sustained metabolism.

The stored talks establish plausible mechanisms and real early hot-spring
habitability, not the historical site of origin. The most defensible conclusion
is that “where” cannot be separated from “which chemical transition”: energy
capture, polymerization, and compartmentalization may impose different
environmental requirements, and the current Filmot corpus does not show that
one setting completed the entire sequence alone.

## 2026-08-30 verification and operational assessment (historical snapshot)

### Verification gate

- Final authoritative suite:
  `TMPDIR=/var/tmp PYTHONPATH=. .venv/bin/python -m pytest -q` — **584 passed
  in 34.37 seconds** on 2026-08-30.
- `PYTHONPATH=. .venv/bin/python -m compileall -q filmot tests main.py`
  completed without an error.
- A CRLF-aware semantic-file scan found no trailing spaces in the changed code,
  tests, or documentation. A changed-diff credential-pattern scan returned no
  match. Repository-wide `git diff --check` remains unsuitable in this Windows
  worktree because unchanged CRLF lines are reported as trailing whitespace.
- The three named sessions remain readable through `filmot sessions NAME
  --summary`. They expose 11, 8, and 5 unique saved transcripts respectively,
  6, 5, and 4 claim IDs, bounded discovery/probe provenance, and explicit
  failure/empty states. The two later structured context artifacts are 164,108
  and 206,676 UTF-8 bytes; all three persisted echo artifacts are present.
- The claim tree still contains exactly 18 historical publication temporaries,
  all from the first investigation. Later writes did not increase that count,
  and no old temporary was deleted or reinterpreted.
- User and agent guides, command help, feature plan, changelog, and this field
  log describe the same final contracts. No commit, push, release, publication,
  or research-data deletion was performed. The unrelated `.gitignore` edit was
  preserved.

### Completion audit

- Three meaningfully different investigations exercised staged and manual
  discovery, transcript storage and local reuse, citation-ready passages,
  primary-paper evidence, claims, assessment, lineage classification, echo
  analysis, context materialization, session resumption, raw output, and
  failure recovery.
- Routine resumption now starts from one session summary and the stored
  library/claim/context surfaces; it does not require opening ledger JSON or
  source code. Manual discovery queries that were never recorded remain
  explicitly unknown rather than being reconstructed speculatively.
- Broad unsafe scope, legitimate zero-query probes, empty selection, partial
  transport work, typed output failure, and completed runs are distinguishable.
  Recovery text normally includes the next exact command or decision.
- F-001 through F-018 are fixed and regression-tested. The last audit passes
  across help, scoped research, probe completion, session summary, context
  artifacts, durable claims, and the full suite produced no new recurring
  medium- or high-severity operational burden.

### Immersion and cognitive load

The first passes were mechanically expensive. Attention repeatedly left the
research to reason about candidate scope, query expansion, probe lineage,
timestamp conversion, refetch behavior, raw-ledger provenance, filesystem
parents, and ambiguous zero states. That was tool-imposed load rather than
subject-matter difficulty.

By the final passes, Filmot carried nearly all of that bookkeeping. The normal
sequence became scoped search or staged research → inspect/save → local grep →
claim/citation/assessment → echo/context → session summary. Help and output
made the next operational step visible, and stored state carried exact IDs,
locators, origin/status, counts, and recovery details. Most attention could stay
on whether a source was primary, whether two sources shared lineage, what a
percentage actually measured, and which chemical bottleneck an origin model
addressed. That is the intended cognitive load.

Remaining limits are mostly epistemic or corpus-level: Filmot cannot make
auto-captions accurate, force YouTube/Filmot coverage, prove source credibility
or independence from lexical overlap, or turn evidence of habitability into
evidence of historical origin. Primary-paper verification still leaves the
YouTube corpus when the transcript is only an attribution lead. Historical
manual-save events cannot acquire titles or originating queries that were never
recorded; future saves preserve best-effort title/channel, while old rows remain
honestly `Unknown`. These constraints remain visible instead of being hidden by
automation.

## Investigation 4 — A curated path through active inference — 2026-09-12

### Research question

What does Machine Learning Street Talk's own “Active Inference / CogSci”
playlist foreground about active inference, and do its opening sources directly
compare the framework with reinforcement learning?

### Why this question

The first investigation left a genuine conceptual interest in the boundary
between active inference as a broad account of adaptive systems and active
inference as an implementable alternative to reinforcement-learning control.
A channel-curated sequence also exercises a different discovery primitive:
intentional grouping and order rather than Filmot relevance ranking or a fresh
YouTube query. The tool should preserve that difference without making the
curator's choice look like independent evidence.

### Bounded live trail

The named session was `curated-active-inference-2026-09-12`. Transient retries
were disabled so the YouTube Data API ceiling was knowable before the run.

```bash
filmot --session curated-active-inference-2026-09-12 \
  yt-playlists UCMLtBahI5DMrt0NPvDSoIRQ \
  --pages 1 --max-results 25 --retries 0 --raw > shelf.json

filmot --session curated-active-inference-2026-09-12 \
  yt-playlist \
  "https://www.youtube.com/playlist?list=PLwFLAA-F1PgoUOiQagOczWYPFMQJSB08J&index=1&utm_source=field-test" \
  --pages 1 --max-results 10 --retries 0 --raw > playlist.json

filmot --session curated-active-inference-2026-09-12 \
  download -t curated-active-inference -n 2 --dedupe < playlist.json
```

- The exact-channel shelf returned all 20 reported public playlists in one
  page: one `channels.list` plus one `playlists.list` attempt. Its terminal
  reason was `exhausted`, with no duplicate, malformed, warning, or error row.
- “Active Inference / CogSci” was selected deliberately rather than silently
  taking the first shelf row. Its metadata reported 19 items. The bounded slice
  retained ten ordered items, ten distinct usable IDs, and ten current video
  resources, with no omission, duplicate, malformed row, or error. One
  `playlists.list`, one `playlistItems.list`, and one batched `videos.list`
  attempt brought the complete discovery budget to five calls. It stopped at
  the explicit result budget and exposed a continuation for the remaining
  items; the continuation was inspected but not executed.
- The playlist URL normalization path was exercised with harmless extra index
  and tracking parameters. The returned request and ledger retained only the
  playlist ID and canonical playlist URL.
- The unchanged `yt-playlist --raw` artifact passed directly into `download`.
  Both selected transcripts were saved successfully. Their records and session
  events retain the playlist ID, playlist-item ID, zero-based position, item
  addition time, content-addressed input reference, current YouTube metadata
  observation, and 30-day expiry. No manual field translation was required.
- Offline `library compare "active inference" --topic
  curated-active-inference --sort density --raw` found 17 exact phrase matches
  across both saved transcripts: 15 in `V_VXOdf1NMw` and two in
  `PNYWi996Beg`. The same comparison for `reinforcement learning` was empty.
  This small opening slice is useful active-inference material but does not yet
  answer the intended comparison; a counter-search outside the curated path is
  still necessary.

### Friction and recovery log

#### F-019 — Configuration preflight conflates two independent API keys

- Status: fixed and regression-tested.
- Stage: credential-safe live preflight.
- Command: `filmot config`.
- Expected: determine whether both the Filmot transcript-index API and YouTube
  Data API are ready without viewing either secret.
- Observed: the inventory showed one generic `API Key: configured` row. That
  referred to the Filmot/RapidAPI credential and said nothing about
  `YOUTUBE_API_KEY`, forcing an agent to inspect configuration internals before
  it could predict whether `yt-playlists` would run.
- Cognitive cost: a basic readiness question required remembering which key
  owned which command and leaving the supported status surface.
- Classification: ambiguous state / missing preflight signal.
- Severity: medium for any direct-YouTube workflow.
- Resolution: `filmot config` now reports `Filmot API Key` and `YouTube API
  Key` independently as only `configured` or `not configured`; no value or
  fragment is exposed.

#### F-020 — Rich can hard-wrap the copyable playlist continuation

- Status: fixed and regression-tested.
- Stage: bounded playlist continuation.
- Command: the continuation printed after the ten-item `yt-playlist` slice.
- Expected: copy one exact command containing the opaque token and matching
  page/result/retry controls.
- Observed: the long command was semantically complete, but normal Rich layout
  could insert hard wrapping into copied terminal text.
- Cognitive cost: the researcher had to distinguish a visual wrap from token
  content and repair a command the tool already knew exactly.
- Classification: copy/paste ergonomics / resumability.
- Severity: medium when an opaque token is present.
- Resolution: continuation text uses soft wrapping, and raw output also keeps
  the directly executable arguments as `continuation.argv`.

#### F-021 — Failure events rely on downstream redaction

- Status: fixed with credential-safety regressions.
- Stage: rejected references and provider/configuration failure logging.
- Expected: every diagnostic is credential-safe before it crosses into the
  session logger.
- Observed: final artifacts were protected by the ledger sanitizer, but some
  playlist command failure paths passed exception text to `log_event` before
  applying the command's explicit safe-summary boundary.
- Cognitive cost: verifying a failure path required reasoning about a second
  component's implementation rather than one local invariant.
- Classification: defense in depth / security auditability.
- Severity: high as an invariant, even though no credential exposure was
  observed in the live run.
- Resolution: failure text is now summarized and redacted before `log_event`;
  rejected identities and provider failures are detached from credential-
  bearing input and traceback state before propagation.

#### F-022 — Downloader help calls every compatible input “search results”

- Status: fixed and regression-tested.
- Stage: playlist-to-transcript handoff discovery.
- Command: `filmot download --help`.
- Expected: help should make the new `yt-playlist --raw` handoff discoverable.
- Observed: the argument description referred only to piped “search results,”
  even though the provider-neutral boundary already accepted exact-video and
  playlist candidate envelopes.
- Cognitive cost: a user could reasonably infer that playlist output needed a
  conversion step or was unsupported.
- Classification: stale help / hidden compatible path.
- Severity: low-medium; the working path itself was smooth once attempted.
- Resolution: help now says “discovery results” and includes an unchanged
  `yt-playlist --raw | filmot download ...` example.

### Working synthesis and cognitive load

The live workflow itself was smooth: exact channel → compact public shelf →
deliberate playlist choice → bounded ordered slice → unchanged raw download →
offline phrase comparison. Filmot carried the identity, quota accounting,
stopping reason, continuation, playlist position, and metadata lifecycle. The
only research judgment in the discovery handoff was choosing the relevant
playlist; no source code, API response shape, or manual metadata join was
needed.

The remaining load was appropriately epistemic. A channel title and curator
order do not show that two videos are independent or that the slice represents
the strongest counterarguments. The empty reinforcement-learning comparison
was a useful scope diagnosis, not evidence that the broader playlist never
discusses it. Continuing the playlist or running an explicit counter-search is
therefore a research decision rather than recovery from tool ambiguity.

### 2026-09-12 playlist-slice verification

- The authoritative session contains one completed shelf result (20 rows, two
  calls), one completed playlist result (ten items/videos, three calls), two
  completed transcript saves, two completed metadata-enrichment events, and a
  completed two-of-two bulk/download outcome with no failure or skip.
- Both saved records carry `filmot.result/v1` `yt-playlist` artifact provenance,
  exact playlist-item context, and YouTube metadata observations expiring on
  2026-10-12. The raw discovery files themselves are not managed by `yt-data`
  and must not be retained past their API-data window without refresh.
- F-001 through F-018 and the 584-pass suite above remain a historical
  2026-08-30 snapshot. F-019 through F-022 belong to this later playlist slice;
  current release verification is reported independently rather than rewriting
  the historical count.
- Final 2026-09-12 integration gate: `python3 -m compileall -q filmot tests
  main.py` completed successfully; the complete deterministic suite passed
  **928 tests** with one upstream Google Python 3.10 lifecycle warning; and
  `git diff --check`, command-help smoke checks, and the changed-diff Google
  API-key-shape scan passed.
