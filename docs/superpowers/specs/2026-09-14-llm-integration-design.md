# Loom — LLM Integration Design

**Date:** 2026-09-14
**Status:** Design agreed, not implemented
**Supersedes:** the "Intent / Flow / Hard requirements" sections of
`docs/superpowers/plans/2026-09-12-sharpening-design.md`, which described the
LLM only in terms of sharpening. Those rules still hold; they are restated
here because they now govern three features rather than one.

## Scope

Three places where a language model earns its keep in Loom:

1. **Sharpening parameters** — semantic knobs plus measurements to concrete
   process parameters. (Phase 2 of the sharpening design.)
2. **SNR region selection** — choosing *where* in the frame to measure signal
   to noise, semantically.
3. **End-of-run report** — an assessment of the masters and the run.

They share one set of rules, below. What unites them is the division of
labour: **the model provides judgement, Loom provides arithmetic.** The model
never computes a number that ends up in the output, and never touches
full-resolution pixel data.

## Shared rules

These are not negotiable and apply to every call.

### Determinism

The same data plus the same knobs MUST produce the same result, or
reprocessing a dataset next month yields a different image and cross-session
comparison becomes meaningless.

- temperature 0
- every response is cached against a fingerprint of its inputs, reusing the
  chained-key machinery in `lib/Cache.js`
- for decisions that affect pixels, the cached decision is pinned into the
  stage cache key, so a re-run reuses the recorded choice rather than asking
  again

This matters most for SNR: the whole point of tracking SNR across sessions is
to decide what to capture next, and that comparison is worthless if the
regions move between runs.

### Bounded, validated responses

Every response is a declared schema with legal ranges. Anything out of range,
malformed, or missing is rejected and a safe default used instead. A
hallucinated value must never reach a process that touches the user's data.

### Never blocking, never fatal

No LLM call may fail a run. No API key, no network, a timeout, a malformed
response — each is a logged warning and the pipeline continues with defaults.
The report is skipped; sharpening falls back to the fixed table; SNR falls
back to deterministic regions (see below).

This is a hard requirement, not a nicety: Loom is a batch pipeline that runs
unattended, and an image that took forty minutes to build must not be lost to
a network hiccup.

### No approval gates

Established already and unchanged: when it runs, it runs. The user judges the
finished image. Rationales are logged as a record for when something comes
out wrong, never as a checkpoint.

---

## Feature 1 — Sharpening parameters

Unchanged from `2026-09-12-sharpening-design.md`. The agent replaces the
fixed level-to-parameter table, taking Loom's measurements plus the semantic
knobs (`starReduction`, `detail`).

**New input, from Feature 2:** per-channel SNR in the DSO. The sharpening
design already states that per-channel correction is "safe only if the wider
channel's SNR supports it" — that decision is currently made with no SNR
input at all. Feature 2 supplies it.

---

## Feature 2 — SNR region selection

### The problem

"What is the SNR of this master" is ambiguous. Measured over the whole frame
it is dominated by empty sky; measured on stars it says nothing about the
nebula; measured by thresholding it just finds bright pixels. The useful
question is semantic: *how good is the signal in the object, as opposed to
the background?*

### Division of labour

- **Loom** renders one downscaled, auto-stretched JPEG of a single rendition
  (the RGB composite, or L when there is no RGB group).
- **The model** returns named regions as **normalised fractions** of frame
  width and height — never pixels, so the coordinate round-trip from the
  downscaled JPEG back to full resolution is unambiguous.
- **Loom** maps those regions onto the full-resolution **linear** data and
  computes SNR itself.

### One image, not seven

Regions are chosen once, from one image, and applied identically to every
channel. If the model picked regions per channel the numbers would not be
comparable, and comparability is the entire point.

This forces the placement: **after registration and crop**, where every
channel is on L's grid and a region refers to the same sky in all of them.

### Why a model rather than a threshold

A threshold (background + k·σ after star rejection) is deterministic and
free, and would find "where there is signal". It cannot distinguish the
bright rim from the faint outer shell from a dust lane from genuinely empty
sky. The request must therefore name semantic regions:

```
bright_object   the brightest structured part of the DSO
faint_object    faint outer extension, where depth is actually tested
dust_lane       dark structure within the object, if present
background      genuinely empty sky, no nebulosity, for the noise reference
```

If the response is rejected, Loom falls back to deterministic regions — a
central box for signal and the frame's darkest quartile for background — and
logs that it did so. Degraded, not broken.

### Validating the regions, with a hard retry cap

Region selection by eye is unreliable and must be checked. Measured
2026-09-15 on a real HSO composite: of four regions chosen from one look at
a 1200 px preview, TWO were wrong.

- `dust_lane` came out at SNR +6.9 in H when it must be NEGATIVE -- the box
  had caught the bright rim around the trunk rather than the dark globule.
- `faint_object` read 0.0 to -0.3 across every channel: sitting at the noise
  floor, discriminating nothing.

So the model's first answer is not to be trusted, and the checks are cheap:

```
background   must have the LOWEST median of the four regions
dust_lane    must be NEGATIVE against background
faint_object must be positive but below bright_object
bright_object must be the highest
```

**At most ONE retry -- two attempts in total.** The failed checks are named
in the retry prompt so the second attempt is informed rather than a reroll.
If it still fails, fall back to the deterministic regions and log which
checks failed and what the values were.

The cap is not negotiable. This runs unattended; a loop that retries until
the checks pass is a run that may never finish, and the fallback is always
available. Two attempts is also what the evidence supports: if a model with
the failures spelled out cannot place a box on a dark lane, more attempts
will not help.

Only the ACCEPTED regions are cached. A rejected attempt is not worth
keeping and must never be reused.

### Broadband and narrowband need separate scales

Also measured on the same frame, bright-region SNR:

```
H 12.2   O 5.5   S 1.0        R 0.8   L 0.7   B 0.4   G 0.3
```

Broadband is uniformly an order of magnitude lower, because a narrow
emission line is diluted across a wide filter. That is correct and expected
for an emission nebula, NOT a fault in the data. Any threshold that decides
"this channel is thin" must therefore be per-group; a single cut-off would
condemn a perfectly good RGB set on every emission target.

### Stars must be excluded

A region over the nebula in a dense field is mostly stars: 1353 detections in
a 1200x900 ROI on the Elephant Trunk data. Without masking them the
measurement returns star flux, not object SNR.

Star rejection is **Loom's** job, not the model's, and is a prerequisite
rather than a refinement. Reuse `StarDetector` as elsewhere in `Steps`.

### Measurement

Per channel, per region, on linear registered data with stars masked:

```
signal = median(region) - median(background_region)
noise  = MAD(background_region) * 1.4826
SNR    = signal / noise
```

Reported per channel per region, and logged.

### Two cache entries, not one

The region choice and the SNR numbers computed from it are cached
**separately, as two chained stages**, not as a single entry:

```
stage "llm-snr-regions"    key = chain( previousKey, "llm-snr-regions",
                                        { compositeFingerprint, provider,
                                          model, promptVersion } )
                            value = { bright_object, faint_object,
                                      dust_lane, background }  (fractions)

stage "snr-measurement"    key = chain( regionsKey, "snr-measurement",
                                        { perChannelFingerprints,
                                          starMaskParams } )
                            value = { [channel]: { [region]: { signal,
                                      noise, snr } } }
```

**Why:** the two stages depend on different things and change at
different rates. The region choice depends only on the rendered
composite and the model/prompt version -- it is the expensive, networked
call. The SNR arithmetic depends on the per-channel linear data and the
star-masking parameters -- cheap, deterministic, and iterated on far more
often while tuning.

If these shared one cache key, tuning star-masking (free) would force a
wasted LLM re-call on every iteration. Worse: nothing guarantees a fresh
LLM call returns bit-identical regions even at temperature 0, so a
re-ask forced by an unrelated arithmetic change could silently shift the
regions between runs -- which breaks the entire point of Feature 2, since
cross-session SNR comparison is only meaningful if the regions did not
move. Chaining `snr-measurement` off of `llm-snr-regions`'s key means the
region choice stays frozen across any number of SNR recomputations, and
only a genuine upstream change (different composite image, different
model, different prompt version) invalidates it and asks again.

This mirrors `Cache.js`'s existing chain-key model exactly -- no new
caching mechanism is needed, just two stage names instead of one.

### What consumes it

A number that only reaches the log is a number read once. Two real
consumers, agreed:

**Capture guidance.** The primary value. "O sits at SNR 4 in the object, H at
22 — shoot O." This is a decision taken on every clear night, currently by
feel. It has already proved decisive once: measuring R/G/B as balanced
(median/MAD 18.3 / 21.0 / 20.3) is what established that G's problem was
seeing rather than depth, and stopped further G capture that would not have
helped.

**Gating sharpening.** Feature 1 above.

**Explicitly rejected:** weighting narrowband palette combination by SNR. A
palette is an aesthetic mapping of emission lines; weighting it by SNR would
make the same data render differently run to run as conditions change, which
is the opposite of what a palette is for.

### Star SNR — a separate, deterministic measurement

DSO SNR and star SNR answer different questions and neither substitutes
for the other:

```
signal = median(peak flux of detected stars)
noise  = MAD(background_region) * 1.4826      -- same noise reference as DSO SNR
SNR    = signal / noise
```

**No LLM involved.** `StarDetector` already runs to produce the mask that
excludes stars from the DSO regions (see "Stars must be excluded" above),
so star positions and flux are a free by-product, not a new expensive
step. The background noise reference is reused from Feature 2's
LLM-selected `background` region when available, falling back to the
same deterministic dark-quartile region Feature 2 falls back to when
there is no LLM at all.

**What it's for:** registration and plate-solve confidence, and
photometric calibration quality (SPCC needs decently-detected stars to
color-calibrate well) — signals about the *data*, not the *target*. This
is why it is not folded into capture guidance or the sharpening gate,
which are both about the DSO.

**Cache:** its own stage, `star-snr`, chained off the per-channel
fingerprint and star-detection parameters — same chaining rationale as
`snr-measurement`, and independent of `llm-snr-regions` since no LLM call
feeds it, though it does reuse `llm-snr-regions`'s `background` result as
an input when present.

---

## Feature 2b — Empirical noise-reduction tuning

Finds the best `mtfTarget` and denoise `strength` for an image by measuring
actual output, using the regions Feature 2 already chose.

### Why this exists

Loom currently picks the pre-stretch target deterministically
(`Steps.prismMtfTarget`) by checking whether the image's contrast falls in
the range Prism's paper reports for its training corpus (BT.709 luminance
std 0.0474-0.1288). That rule reproduces every observation available, and it
is still a proxy: it reasons about the INPUT, never the output.

For flat data it cannot even reach the range. Measured on a real HSO
composite: std 0.0244 at the 0.15 default, 0.0352 at 0.30, peaking at 0.0401
at 0.50 -- below the corpus minimum everywhere. The rule returns 0.50 as the
closest reachable value and warns that it will still over-smooth. That
warning is exactly the case this feature settles.

**Excluded, by measurement, so it is not re-investigated:** quantisation is
not involved. `prism_cli` preserves full 32-bit float through the FITS path
(a 512-px smooth float ramp went in and came back with 512 distinct values,
32-bit both directions). The paper's "16-bit output" describes its PNG/TIFF
pipeline, not this one.

### It reuses Feature 2's regions

No new LLM call. Feature 2 already asked for semantic regions on the
composite -- `bright_object`, `faint_object`, `dust_lane`, `background` --
and cached them against the image fingerprint. Those are precisely the zones
a denoiser has to be judged in, and they are far better than an arbitrary
crop:

- `background` is where noise removal is measured
- `faint_object` is where over-smoothing does its damage, because faint
  structure is what a denoiser destroys first
- `dust_lane` gives real edges, so edge preservation is measurable
- `bright_object` catches highlight flattening

**The LLM contributes only the semantics.** The sweep, the metrics and the
choice are deterministic arithmetic. That keeps the whole feature
reproducible and means it still works, on the deterministic fallback
regions, when no model is configured.

### The patch

One crop containing all four regions -- their bounding box, clamped to a
size the CLI turns round quickly (512-1024 px square; Prism's tiles are
512). The full composite takes minutes per run; a patch takes seconds, and
the parameters do not depend on frame size.

### The sweep

A small grid, run through the same `Steps.prismExecuteStage` path as a real
denoise so it is measuring the real pipeline, not a simulation of it:

```
mtfTarget  0.15, 0.25, 0.35, 0.50      (the deterministic pick always included)
strength   0.50, 0.70, 0.85            (the existing Low/Medium/High)
```

Twelve runs on a small patch. The grid is deliberately coarse: the point is
to find the region of the space that works, not to over-fit a number.

### The metrics, all computed by Loom

For each output, in the regions:

- **noise removed** -- MADN in `background`. Lower is better. This is the
  entire purpose of the tool.
- **structure retained** -- high-frequency energy in `faint_object`,
  measured against the SAME region of the undenoised patch. Falling far
  below the input means faint detail was erased.
- **edges retained** -- gradient magnitude across `dust_lane`, likewise
  relative to the input.
- **plateau fraction** -- share of pixels in `background` and
  `faint_object` whose 3x3 neighbourhood has zero variance. This is the
  direct, objective measure of the patchy "posterised" output that prompted
  the whole investigation: real astronomical data is never locally flat, so
  any plateau is manufactured.

### Choosing

Reject any candidate whose plateau fraction exceeds the undenoised patch's
by a set margin -- that artefact is disqualifying regardless of how good the
noise number looks. Among the survivors, take the one with the lowest
background MADN whose structure and edge retention stay above a floor
(e.g. 90% of the input's). That is the knee: the most noise removed before
detail starts going.

Report all twelve rows in the log, not just the winner. When the choice is
marginal the numbers should be visible, and they are the evidence for
revisiting the thresholds later.

### Determinism and caching

The result is cached against the image fingerprint plus the region set, and
pinned into the denoise stage key, exactly as the sharpening decision is.
A re-run reuses the recorded parameters rather than sweeping again -- twelve
CLI runs is not something to repeat for a cache hit.

### Failure

Falls back to `Steps.prismMtfTarget` and the dialog's Strength setting, with
a logged warning. As with every other LLM-adjacent feature here, it must
never fail a run.

### What it would settle

Whether the flat-narrowband warning is real: if the sweep finds a target
where the HSO patch denoises cleanly, the training-contrast rule is wrong
and should be replaced by whatever the measurement shows. If it does not,
the warning is correct and the honest advice is to denoise after stretching
instead.

---

## Feature 3 — End-of-run report

### What it is

A window shown at the end of a run with two parts:

```
Measurements   a table of everything Loom measured -- FWHM/sigma, axis
               ratio, star count, median, MAD, DSO SNR per channel per
               region (Feature 2), star SNR per channel (above), white
               balance factors, halo-matching sigmas, warnings raised.
               Always present. No LLM required.

Verdict        the model's narrative assessment, built from the
               Measurements table above. Present only when an LLM
               provider is selected (see Provider selection).
```

This is a change from treating the whole window as an LLM feature: the
Measurements table is Loom's own arithmetic and has value with `None`
selected in the provider dropdown. The Verdict section is what actually
needs a model.

### Inputs

Only measurements Loom has already computed. The model is given numbers and
one small JPEG; it is never given full-resolution data.

```
per channel:  FWHM / sigma, axis ratio, star count, median, MAD,
              DSO SNR per region (Feature 2), star SNR (Feature 2),
              filter name, exposure,
              frame count and integration time if available
global:       pixel scale, drizzle factor, image dimensions,
              crop overlap as % of reference, camera
processing:   white balance factors applied and their source
              (measured on uncorrected channels, or direct SPCC),
              saturated-pixel counts introduced by aberration correction,
              halo matching sigmas applied per channel,
              narrowband linear-fit reference,
              every warning raised during the run
```

That last one matters: the warnings Loom already emits are the most
diagnostic input available, and today they scroll past in the console.

### Hard constraint on content

**Every number in the report must come from the supplied measurements.** The
model may not invent, estimate, or infer a figure. This is stated in the
request and checked on the way out: any numeric token in the response that
does not appear in the inputs is grounds for rejecting the report.

This is the highest-risk feature in the document. A confident, wrong
assessment of someone's data is worse than no assessment, and a language
model asked to comment on image quality will happily produce plausible
numbers. The constraint is what makes the feature safe.

### Structure

```
Verdict          one paragraph: is this a good set, and what limits it
Per channel      one line each: state, and whether it is a limiting factor
Issues           concrete problems, each tied to the measurement showing it
Strengths        what is genuinely good, so it is not lost in a re-shoot
Next capture     what to shoot more of, and why -- driven by SNR and FWHM
```

### Enable/disable

A **"Show end-of-run report" checkbox** in the dialog, persisted like the
other dialog state, **independent of the provider dropdown**. It gates
the whole window, Measurements table included — someone who wants the
numbers logged to the console only, or who finds a modal dialog at the
end of every run annoying, can turn the window off entirely regardless
of whether an LLM provider is configured. Default on, since the
Measurements table costs nothing and Loom's own warnings are otherwise
easy to miss scrolling past in the console.

When the checkbox is off, every measurement in the table is still
computed and logged (SNR, star SNR, FWHM etc. are consumed elsewhere —
capture guidance, sharpening gate — regardless of report visibility);
only the window itself is suppressed.

### Presentation

A `Dialog` shown after outputs are produced and named, before the window
sweep, when the checkbox above is on. `Dialog` is always application-modal
in PixInsight, which is acceptable here precisely because the run is
finished — there is nothing left to block.

The report is **also written to disk** alongside the outputs when an output
folder is set (`<outputDir>/loom-report-<timestamp>.md`), so it survives the
dialog being dismissed. When no output folder is set it exists only in the
window.

**It must not interfere with cleanup.** The sweep that closes working windows
runs regardless of whether the dialog was shown, dismissed, or failed to
appear. A report that leaks windows would undo a fix this project has already
had to make once.

### Advisory only

The report never gates anything, never modifies an image, and never re-runs a
stage. It is the last thing that happens.

---

## What the model never does

- see full-resolution pixel data
- compute a number that appears in an output image
- decide whether the result *looks* right — ringing is measurable,
  "over-processed" is the user's eye at print scale
- gate, block, or re-run any stage
- write to disk directly

## Provider selection (v1)

No API keys in Loom. v1 shells out to the CLIs the user has already
authenticated — `claude` and `codex` — and lets them choose in the dialog.

### Detection

Look for each executable on `PATH`, then at known locations. Verified on this
machine 2026-09-14:

```
claude   /Users/francescocarucci/.local/bin/claude     2.1.268
codex    /opt/homebrew/bin/codex                       codex-cli 0.153.4
```

Same pattern as the SyQon detection already in `Steps`: absent means the
option is not offered, never a hard failure. A manual path override belongs
in the dialog for the case where a CLI lives somewhere unusual.

### Model enumeration — what is actually possible

**Neither CLI can list its models.** Verified: `claude` only accepts
`--model <name>`, and `codex` has no `models` subcommand. So enumeration has
to come from each tool's own state files:

```
codex   ~/.codex/config.toml
            model = "gpt-6-astra"                      (configured default)
        ~/.codex/.codex-global-state.json
            .../seen-model-upgrade-list
              ["gpt-5.5", "gpt-5.6-sol", "gpt-6-astra"]
            .../composer-recent-model-configurations-v1[].model

claude  ~/.claude/settings.json
            model = "opus[1m]"                         (configured default)
        ~/.claude.json
            additionalModelOptionsCache[]
              { value, label, description }
        `claude --help` documents the aliases: fable, opus, sonnet
```

**These are internal application state, not a public interface.** They may
move or change format between CLI versions. The design must therefore treat
enumeration as best-effort:

1. read whatever is readable from the files above
2. union with the documented aliases for that CLI
3. always include the configured default, marked as such
4. if nothing can be read, fall back to the documented aliases alone
5. always allow a manually typed model name

A dropdown that silently empties because a vendor renamed a JSON key would
make the whole feature look broken. It must degrade to something usable.

### Dropdown

One flat list, provider-prefixed, as requested:

```
None (LLM features off)          <- default
Codex - gpt-6-astra (configured)
Codex - gpt-5.6-sol
Codex - gpt-5.5
Claude - opus (configured)
Claude - fable
Claude - sonnet
```

The selection is persisted in Loom's settings like the other dialog state.
`None` is the default and disables all three features; Loom must be fully
usable with no CLI present at all.

### Invocation

Both CLIs support non-interactive use:

```
claude -p "<prompt>" --model <model> --output-format json
codex exec "<prompt>" -c model=<model>
```

Exact flags are to be confirmed against the installed versions before
implementation, and recorded in `docs/verified-parameters.md` — the same rule
that exists because a process parameter was once assumed wrong.

Run through `Steps.syqonRunProcessBlocking` or a sibling of it: that wrapper
already handles a blocking external process with a hard timeout and tolerates
a spurious non-zero exit code, which is exactly the shape needed here.

### Health check

The selected provider is probed once with a trivial prompt and a short
timeout, and the result cached. An unauthenticated or broken CLI is reported
in the dialog rather than discovered mid-run.

### Why this is the right v1

It inherits the user's existing authentication, subscription and model
access; it adds no secret for Loom to store; and it lets the same design be
pointed at a direct API later by swapping the transport, since everything
above the invocation is provider-agnostic.

## Open questions

- **Enumeration fragility — DECIDED 2026-09-14: accept it.** The state files
  above are undocumented and version-dependent, and that is the trade being
  taken knowingly: they are the only real source, since neither CLI can list
  its models. Revisit if either gains a `models list` command.

  What this obliges the implementation to do:
  - treat every field as optional and every file as possibly absent
  - never throw on a parse failure; log and continue with what was read
  - keep the documented aliases as the floor, so the dropdown is never empty
  - surface the source in the UI, so a stale or empty list is diagnosable
    rather than mysterious
- **Whether the report should also be shown when the run fails.** A report on
  a failed run would be useful for diagnosis, but the inputs would be partial
  and the risk of a confidently wrong assessment is higher. Defer.
- **Cost per run.** Three calls (sharpening, SNR regions, report), two of
  them with a small image attached. Expected to be negligible, but should be
  measured rather than assumed before this is on by default.
