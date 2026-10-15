# Releasing Loom

A release is signed on the maintainer's Mac and published by CI when the tag is pushed.

This repository also holds the Siril port, which is released together with it: one `v*` tag (for example `v0.5.0`) is both, and `siril/` takes the same version number. Everything here concerns the PixInsight scripts and the signing: the commands run from the `pixinsight/` folder, and `main`, `CHANGELOG.md`, `script/` and `repo/` are meant relative to it.

## One command

```
cd pixinsight && ci/release.sh <version> --key-dir <folder> [--push]
```

- `--key-dir`: a folder holding the Certified PixInsight Developer signing keys and their password: exactly one `<name>.xssk`, and its password in `<name>.password` (or in a file called `password` or `password.txt`). The script picks the right files and passes them to `ci/sign.sh`. Use `--keys` and `--password-file` instead to name the files directly (they win over what the folder gives).
- The password file is read by PixInsight itself and never copied. Keep it outside the repository and `chmod 600` it.
- `--push`: also push `main` and the tag to `origin` and `github`. Without it, nothing leaves the machine and the push command is printed.

Before running it, merge the work to `main` and describe it under `## [Unreleased]` in `CHANGELOG.md`.

Standing rules:

- Releases are ALWAYS made by the release script with `--push`: `cd pixinsight && ci/release.sh <version> --key-dir <folder> --push`. Never run it without `--push` and hand-push the tag or branch.
- Make sure CI on `main` is green and the tree is clean on `main` BEFORE starting. The script pushes `main` and the tag at its end, and a tag push triggers the publishing run and cannot be taken back.
- One `v*` tag releases both the PixInsight scripts and the Siril port.
- If ssh to `github.com:22` is blocked, set `GIT_SSH_COMMAND='ssh -o Hostname=ssh.github.com -o Port=443'` for the script (ssh over 443).
- The script's last push, to a remote named `github`, failing is harmless when `origin` is GitHub.

It then:

1. checks it is on a clean `main` and the tag is new;
2. turns `[Unreleased]` into the dated `[<version>]` section and sets `Util.LOOM_VERSION`;
3. runs the test suite as 1.9.5 and as 1.9.4;
4. signs `Loom.js`, `FrameSelector.js` and `FlyThrough.js` (`ci/sign.sh`), writing the `.xsgn` files and `ci/signed-tree.sha256`;
5. builds the update repository (`ci/repository.sh`), signs its `updates.xri` (`ci/sign.sh --xml`) and puts the result in `repo/`;
6. commits `Loom <version>` (scripts, signatures, `repo/`) and creates the annotated tag `v<version>`;
7. with `--push`, pushes. CI then checks the signatures and that `repo/` is signed and holds exactly the tagged sources, builds the release zip, and publishes `repo/` as the update repository. CI does not build or sign the repository.

## Why signing is local

A script signature covers the script and every file it includes, so any change to `script/` after signing (the version bump included) invalidates it, and PixInsight refuses a script whose signature is invalid. Signing needs PixInsight's `Security` object and the private keys, so it cannot run in CI. CI only checks (`ci/check-signatures.js`, on tags): the signature files exist and the sources match the hash recorded when they were signed.

`ci/sign.sh` can also be run alone, with the same `--keys` and `--password-file`. It signs in a separate PixInsight instance (slot 9, `--slot N` to change), never in the one in use for work. If the script started that instance, it closes it when signing is done; an instance that was already running is left alone.

## The update repository

`updates.xri` is the manifest PixInsight reads from `https://fcarucci.github.io/Loom/`. Its signature is embedded in the file and covers all of it, and the manifest holds the checksum of the package, which holds the signed scripts, so the order is fixed: sign the scripts, build the package and manifest, sign the manifest. `ci/release.sh` does that and commits the result in `repo/` (the manifest, the package zip and the licence); only the current release is kept there. Other pushes to `main` still build an unsigned repository in CI, just to test the layout.
