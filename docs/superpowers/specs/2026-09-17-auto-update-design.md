# Loom auto-update — design

**Date:** 2026-09-17
**Status:** approved — revised after an adversarial review

## What this is for

Loom is edited on the machine that runs it, and the copy in
`~/PixInsight/scripts/Loom` drifts behind the repository whenever work happens
elsewhere. The script should notice and update itself without anyone typing
`git pull`.

Three facts shape the design.

**PJSR resolves `#include` at parse time.** A running script cannot reload its
own libraries. Any update takes effect in a *later* execution.

**Startup must not wait for the network.** An earlier draft ran `git fetch`
synchronously with a 5-second deadline — a cost on every launch, paid mostly
when off-network, in exchange for nothing, since the update could not apply to
the running script anyway.

**So Loom does not wait.** It spawns the update detached and opens the dialog
immediately. The pull takes about a second while you look at the dialog; the
next launch parses the new code. There is no relaunch, no dispatch, and no
restart prompt. The cost is a one-launch lag.

## Version and identity

`Util.VERSION`, a semver string, is the single source of truth for the release
number.

```js
Util.VERSION = "1.0.0";
```

A release is a bump of `Util.VERSION`, an annotated tag `v<version>`, and — for
the zip path — a GitHub release carrying a zip of `script/`.

**The version alone does not identify the running code.** The git path installs
whatever is on the branch, and many commits share one `Util.VERSION`. So Loom
reports both:

```
Loom 1.0.0 (a4c1f2)
```

The commit is read directly from `.git` (HEAD, then the ref it names) with no
git binary involved, and is omitted when there is no `.git`. This appears in the
dialog title and on the first line of every run log.

`Cache.FORMAT_VERSION` stays separate: it salts cache keys, and if it tracked
releases every release would invalidate every cached stage. **The standing rule
it implies is now explicit: a change to what a stage computes must bump
`Cache.FORMAT_VERSION`,** or an updated Loom will serve results cached by the
old code as current. Keying on resolved parameter values (see
`Pipeline.compositeSharpenParams`) covers parameter changes but not
implementation changes.

## Structure

One new file, `script/lib/Update.js`:

- **decision logic** — pure functions over strings and injected predicates:
  resolving the git binary, recognising a git-managed directory, parsing
  `git --version` and upstream names, comparing semver, verifying an extracted
  tree, formatting and parsing outcome records. No process, no network, no
  filesystem; all of it is unit-tested.
- **`Update.run` / the spawn seam** — stubbed in the selftest.
- **`Update.reportLast()`** and **`Update.start( config )`** — called from
  `main()`, in that order, both returning immediately.

## Flow

```
main()
  |
  +-- Update.reportLast()   -- console line about the PREVIOUS launch's update
  |
  +-- config.autoUpdate off? ......................... return
  |
  +-- git-managed?  (.git exists, as a directory OR a file)
  |     +-- yes -> git usable? --yes--> spawn detached git update
  |     |                      --no---> DO NOTHING
  |     +-- no  -> RELEASE marker present? --yes--> spawn detached zip update
  |                                        --no---> DO NOTHING
  |
  +-- open the dialog immediately
```

### Choosing a path, and why `.git` alone is not enough

**A git checkout is only ever updated by git.** The zip path swaps a directory
into place; doing that over a working copy would leave a repository whose tree
was replaced behind its back — permanently dirty and refused by every future
`--ff-only`, silently. If `.git` is present and git is unusable, Loom does
nothing.

`.git` is **a file, not a directory**, in linked worktrees and submodule
checkouts. A directory-only test therefore reports "not a repository" for a
genuine checkout and routes it into the destructive path — the precise outcome
the rule exists to prevent. Both forms count as git-managed.

Symmetrically, the **absence** of `.git` does not prove an install came from a
release. The zip path additionally requires a `RELEASE` marker file that the zip
installer itself writes. An unrecognised directory — no `.git`, no marker — is
left alone. Replacing a directory is only ever done to a directory this feature
created.

### Finding git

`/usr/bin/git` exists on every Mac whether or not git is installed: it is a stub
that opens the "Install Command Line Developer Tools" dialog when run. Probing
by execution can throw a modal system installer in the user's face at startup.
Detection is resolve-then-verify, and runs only on the git path:

1. Look for a binary **on disk, executing nothing**: `/opt/homebrew/bin/git`,
   `/usr/local/bin/git`, and `/usr/bin/git` **only if** a real git also exists at
   `/Library/Developer/CommandLineTools/usr/bin/git` or inside Xcode. On
   Windows: `C:\Program Files\Git\cmd\git.exe`.
2. Run `<path> --version`; require exit 0 and output beginning `git version`.
   This one call is synchronous. It is local, cannot touch the network, and is
   the only way to know the binary works — a bounded, acknowledged exception to
   "startup never waits".
3. Anything else: do nothing.

The resolved absolute path is used for every command. PixInsight's environment
is not the user's shell and `PATH` cannot be relied on.

### The git update

The command is written to a helper script and invoked as
`/bin/sh <script>`, **not** interpolated into `sh -c`. Paths, branch names and
URLs cannot then break quoting or become shell syntax.

```sh
set -e
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -o BatchMode=yes"
# single-flight: mkdir is atomic
mkdir "$LOCK" || exit 0
trap 'rmdir "$LOCK"' EXIT

# refuse anything but a clean, attached branch with an upstream
[ -z "$(git -C "$DIR" status --porcelain=v1 --untracked-files=all)" ] || exit 3
UP=$(git -C "$DIR" rev-parse --abbrev-ref --symbolic-full-name @{u}) || exit 4

git -C "$DIR" -c merge.autoStash=false fetch -q "${UP%%/*}" "${UP#*/}"
git -C "$DIR" -c merge.autoStash=false merge --ff-only -q @{u}
```

Wrapped in `timeout` so a stalled fetch cannot leave a worker running forever.

Each guard exists for a reason the review made concrete:

- **`status --porcelain --untracked-files=all`, not `diff --quiet`.** `diff
  --quiet` compares tracked files against the index: it misses staged changes
  and untracked files entirely. It is not the guard the earlier draft claimed.
- **`merge.autoStash=false`** so an inherited global config cannot stash and
  reapply the user's work unattended.
- **The upstream is resolved, then fetched.** `fetch origin <branch>` followed by
  `merge @{u}` can target different refs, or different remotes: a local branch
  may track something other than its own name. The fetch then succeeds while
  `@{u}` stays stale. Detached HEAD and a branch with no upstream exit here.
- **`GIT_TERMINAL_PROMPT=0` and SSH `BatchMode`.** A background fetch that hits
  an authentication prompt would otherwise wait forever, invisibly, accumulating
  one worker per launch.
- **`mkdir` lock.** Two launches cannot both update. `mkdir` is atomic on POSIX;
  a stale lock is cleared by the `trap`.
- **`--ff-only`** so a diverged branch fails cleanly, leaving the tree untouched.

### The zip fallback

Runs when there is no `.git` and a `RELEASE` marker is present. Detached, in a
helper script, with the same lock and timeout.

1. `GET https://api.github.com/repos/<owner>/<repo>/releases/latest`.

   **GitHub, not Gitea.** Gitea on `git.local.carucci.studio` refuses anonymous
   API calls — `403 {"message":"Only signed in user is allowed to call APIs."}`,
   verified — so it would need a stored token, and Loom's config is serialised
   into the draggable process instances, meaning a token would travel inside any
   instance handed to someone else. GitHub's release API is anonymous.

   Host, owner and repo are constants in `Update.js`, not configuration.

2. Compare `tag_name` to `Util.VERSION` by semver. Not newer, or unparseable:
   stop.
3. Download `browser_download_url` with `curl -fsSL --proto '=https'`. **`-f`
   matters**: without it curl writes an HTML error page to the file and exits 0.
4. Extract with `tar -xf` into an **empty** staging directory on the same
   filesystem as the target. macOS `/usr/bin/tar` and Windows `tar.exe` are both
   bsdtar, which reads zip, so one command covers both platforms.

   `curl` and `tar` rather than PJSR's in-process `NetworkTransfer` because this
   runs detached; nothing here may run on the main thread.
5. **Verify before swapping**, against the *new* release's own manifest — the
   earlier draft required every library the *old* copy had, which would reject a
   legitimate release that renamed or removed one.
6. Swap: rename `script/` to `script.old`, rename staging to `script/`, write the
   `RELEASE` marker, remove `script.old`. Same filesystem, so these are renames
   and not copies.

**Residual risk, accepted:** the swap is two renames, not an atomic
publication. A process killed between them leaves `script/` missing, and the
rollback cannot run because the killer killed the rollback too. Recovery is
manual: `script.old` is left in place and renaming it back restores the previous
version. A crash-safe design needs an immutable release directory plus a
launcher that resolves a symlink before parsing — correct for shipped software,
disproportionate for a personal script whose recovery is one `mv`.

**This path is initially tested against a GitHub repository** and reads GitHub
releases while `origin` is Gitea. Nothing in this design keeps the two in step;
until a release process does, the zip path serves only what is pushed to GitHub
by hand.

## Reporting what happened

The update is detached, so the launch that starts it cannot know the outcome.
Reporting is deferred one launch, like the update itself.

**State lives in `~/.loom/`, not in the cache.** `Cache.clear` deletes every
non-directory file in the cache folder, so "Clear cache" would eat the outcome
record; and the cache folder is user-selectable, so changing it would strand
previous results. `~/.loom/` also survives a zip swap of `script/`.

The helper script writes **a structured single line** — not a shell redirect of
whatever git printed. A redirect records text, not outcome: `>` truncates at
spawn, so an empty file cannot distinguish success from a dirty-tree refusal
from still-running from killed.

```
<status> <exit> <old-sha> <new-sha> <timestamp> <message>
```

`status` is one of `updated`, `unchanged`, `skipped-dirty`, `skipped-no-upstream`,
`failed`. It is written to a unique temp file and **atomically renamed** into
`~/.loom/update-last.txt`, so a reader never sees a partial record and two
workers cannot interleave.

At the start of the next launch `Update.reportLast()` renames the file away,
reads it, reports it, and deletes it — claiming by rename, so two launches
cannot both report the same record.

- `updated` / `failed` / `skipped-*`: a console line, `Util.log` or
  `Util.warn` as appropriate, carrying git's own message rather than one of
  Loom's invention.
- Nothing to report: silent.

**This console line does not reach the run log.** `console.beginLog` opens at
`Loom.js:344`, after dialog acceptance and preflight, and not at all for a
cancelled or validate-only run. An earlier draft claimed otherwise; it was
wrong. The record is written to `~/.loom/update.log` as well, so there is a
dated trail independent of whether a pipeline ran.

## Configuration

`autoUpdate: true` in `defaultConfig()`, persisted in Settings, with a checkbox
beside the cache row. Off means nothing is spawned and nothing is reported.

## Error handling

Every failure ends the same way: Loom runs the version on disk.

| Situation | Behaviour |
|---|---|
| `autoUpdate` off | nothing spawned |
| `.git` present (dir or file), git unusable | nothing — a checkout is only updated by git |
| No `.git`, no `RELEASE` marker | nothing — the directory is not ours to replace |
| Another updater holds the lock | second one exits immediately |
| Dirty tree, staged or untracked | `skipped-dirty`, tree untouched |
| Detached HEAD, or no upstream | `skipped-no-upstream` |
| No network, server down, auth required | `failed`, with git's message, reported next launch |
| Branch diverged | `failed` — `--ff-only` refuses, tree untouched |
| Zip verify fails | staging discarded, nothing swapped |
| Killed between the two renames | `script.old` remains; recovery is one `mv` |

Nothing in this feature can delay or abort startup, because nothing is waited on
except the local `git --version` probe.

## Known limitation, accepted

**Two PixInsight instances launching during a pull could parse a mixture of old
and new files.** Git does not publish a working tree atomically. The lock means
only one updater runs, narrowing the window to roughly the second the merge
takes, and Loom is launched one instance at a time. The full fix — staged
immutable releases and a launcher that picks one before parsing — is
disproportionate here. Recorded as accepted rather than overlooked.

## Threat model

The trusted publication channel is the repository and the GitHub release. A
compromised account could deliver JavaScript that runs with PixInsight's access
to files and network.

On a single-user Mac this buys an attacker little that they do not already have:
anyone able to write to `~/PixInsight/scripts/Loom` as this user can replace
Loom directly, without going near the updater. Signed manifests and a separately
protected signing key would defend against hosting-account compromise; that is
disproportionate for a personal script and is deliberately not built.

Transport is HTTPS, and `--proto '=https'` prevents a redirect downgrading it.

## Testing

In `selftest.js`, against injected predicates and a stubbed spawn:

- git resolution: homebrew present; **only the macOS stub — must return
  nothing**; stub plus Command Line Tools; nothing at all.
- `git --version` parsing: valid, empty, non-zero exit, garbage.
- git-managed detection: `.git` directory; **`.git` as a file** (worktree);
  neither.
- path selection: git-managed picks git; git-managed with git unusable spawns
  **nothing**; no `.git` with a `RELEASE` marker picks zip; no `.git` and no
  marker spawns **nothing**.
- upstream parsing: `origin/main`; a differently-named upstream; empty (detached
  HEAD).
- semver comparison: newer, older, equal, malformed, `v` prefix.
- the generated helper script contains `--ff-only`, `--untracked-files=all`,
  `merge.autoStash=false` and `GIT_TERMINAL_PROMPT=0`, so a later edit cannot
  quietly drop a guard.
- outcome records: each status formats and parses; a truncated line is reported
  as incomplete rather than as success; reporting deletes the file; a second
  launch is silent.
- `autoUpdate` false spawns nothing.
- `Util.VERSION` is well-formed semver.
- the zip verifier: complete tree; missing manifest entry; empty directory.

A real `git fetch`, the detached spawn, and the directory swap are not
unit-testable. They are verified by hand against a disposable repository, and
that division is stated here so the suite is not mistaken for covering them.

## Deliberately not included

- Downgrading or pinning to a version.
- Any check during a run. Startup only.
- Submodules. Loom has none; a fast-forward that advanced a gitlink would leave
  the dependency's files stale, so such a repository is refused rather than
  half-updated.
- Signed release manifests. See the threat model.
- Keeping GitHub and Gitea in step. A release-process decision, not this
  feature's.
