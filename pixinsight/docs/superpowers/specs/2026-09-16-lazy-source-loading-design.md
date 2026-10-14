# Loom — Lazy Source Loading Design

**Date:** 2026-09-16
**Status:** Proposed, awaiting review

## Problem

A fully cached run still reads every source master off disk. On a seven
channel set at roughly 1.1 GB each that is about 8 GB read from an
external drive to produce results that were already on disk, and it
happens before the first cache lookup.

There are two separate causes, one trivial and one structural.

### 1. Preflight opens masters to read one keyword

`Pipeline.preflight` validates that each broadband channel has a `FILTER`
keyword. It does so through `Pipeline.readKeywords`, which is a full
`ImageWindow.open` — pixels decoded and discarded — for four masters on
every run, cached or not.

`Pipeline.readImageInfo` sits immediately above it in the same file and
reads keywords and geometry from the header alone, via
`FileFormatInstance.open` without the `readImage` call. Its own comment
explains exactly why it exists:

> For a master folder that is ruinous: ~51 masterLight files at 300-850 MB
> each is tens of gigabytes read off an external drive to learn FILTER,
> XPIXSZ and the dimensions.

That lesson was learned for the dialog and never applied to the run.

### 2. The load loop opens every source before any cache lookup

`Pipeline.js:875` opens each channel unconditionally:

```js
var ws = ImageWindow.open( config.paths[key] );
...
sourceKey = Cache.fingerprintFile( config.paths[key] );
```

Three things are taken from that open, and none of them needs pixel data:

| Needed | Actually requires |
|---|---|
| `sourceKey` | `Cache.fingerprintFile` — path, size, mtime. A `stat`. |
| `filter`, `instrume` | FITS keywords. Header only. |
| the `ImageWindow` | Only if a stage actually has to run. |

## The rule that makes this safe

`Pipeline.processChain` already computes `hitIndex`, the index of the
latest cached stage in a channel's chain, before running anything. Its
existing behaviour:

- stages before `hitIndex` are skipped (companions still loaded),
- at `hitIndex` the cached window **replaces** `chan.window` and the
  previous one is closed,
- stages after `hitIndex` operate on that loaded window.

So the source window is consumed in exactly one case: **`hitIndex == -1`**,
when nothing in the chain is cached and stage 0 runs on the source itself.
Every other case already discards it untouched.

That gives a single, checkable condition rather than a scattering of
"is it loaded yet" tests.

## Design

### Channel records become lazy

`chans[key]` is built without opening anything:

```
{ key, path | viewId, sourceKey, filter, instrume,
  window: null, view: null, load() }
```

- `sourceKey` from `Cache.fingerprintFile( path )` — a `stat`.
- `filter` and `instrume` from `Pipeline.readImageInfo( path ).keywords`.
- `load()` opens the file, assigns the working id, registers the window
  with `reg`, sets `window`/`view`, and returns the window. Calling it
  twice returns the same window.

Every current consumer of `chan.window` / `chan.view` goes through
`chan.load()`. There are 67 `.window`/`.view` references in `Pipeline.js`;
the ones inside stage runners are the ones that matter, and they run only
when a stage runs.

### Where `load()` is called

`processChain` calls `chan.load()` once, immediately before invoking the
first runner, and only when `hitIndex == -1`. Nothing else calls it
implicitly.

The existing `old = chan.window; ... if ( old != null )` guard at the
`hitIndex` branch already tolerates a null window — it was added for the
RGB `combine` stage, whose chain creates its window rather than
transforming one. Lazy loading makes that the normal case rather than the
exception, which is a good sign the shape is right.

### Views are treated the same way

A channel sourced from an open view (`config.views[key]`) needs
`Cache.fingerprintView`, which reads `median()` and `MAD()` from the
*existing* view. That is in memory already and costs no disk read, so the
fingerprint is computed from the user's view directly and the working
**duplicate** is what gets deferred. No disk saving here, but the code
path stays uniform instead of forking on source type.

### Preflight

`Pipeline.readKeywords` is replaced at its only call site by
`Pipeline.readImageInfo( path ).keywords`. The function itself is then
unused and is deleted rather than left as a loaded gun.

## What this does not change

- Cache keys. No stage's parameters change, so nothing is invalidated and
  the existing cache stays valid.
- Stage order, stage semantics, or any output.
- Behaviour on a cache miss: the file is opened exactly as before, one
  step later.

## Risks

**Lost windows.** This codebase has twice produced bugs where a window was
not registered with `reg` before `reg.closeAll()`, losing a result. `load()`
is the single place a source window is created, and it registers before
returning — one site to audit rather than seven.

**A missed `load()` call.** A consumer reached without loading sees
`chan.view == null` and throws a `TypeError` deep inside a runner. Mitigated
by making `window`/`view` start as `null` (not `undefined`) and by the
selftest below; a stage that needs its input and does not get it fails
loudly on the first run rather than producing a subtly wrong image.

**A file that changed between preflight and load.** Today the open happens
early, so a vanished file fails early. With deferred loading it fails at
first use. `Pipeline.preflight` already checks `File.exists` for every
path, so the window between check and use widens but does not open.

## Testing

`Pipeline.processChain` is not reachable from `selftest.js` without a
running PixInsight, so the tests target the decision rather than the I/O:

1. A fake channel record whose `load()` increments a counter, driven
   through a chain where every stage is cached: asserts `load()` is called
   **zero** times.
2. The same with an empty cache: asserts `load()` is called exactly once.
3. The same with only the final stage cached: asserts zero.
4. `Pipeline.readKeywords` no longer exists (guards against a future
   caller reintroducing the full open).

These need `Cache.lookup` to be injectable or stubbable for the duration
of the test; the cleanest route is a module-level `Cache.lookup` that the
test temporarily replaces and restores in a `finally`, matching how the
ladder-remap tests already save and restore `Steps.SHARPEN_LEVELS`.

## Expected result

A fully cached seven-channel run opens **no** source masters and reads
only the cached stage results it actually needs. A run with nothing cached
reads exactly what it reads today.

## Out of scope

Storing keywords or fingerprints as JSON beside the cache entries. It was
the first idea considered and it is not needed: `stat` plus a header read
already supply everything the key chain requires, without introducing a
second source of truth that can disagree with the file on disk.
