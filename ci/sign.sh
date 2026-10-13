#!/usr/bin/env bash
#
# Signs Loom's entry scripts (script/Loom.js, FrameSelector.js, FlyThrough.js)
# with a certified PixInsight developer's keys, writes the .xsgn signature
# files next to them, and records the hash of the signed sources so CI can
# tell, on a tag, whether they changed afterwards (ci/check-signatures.js).
#
# Signing needs PixInsight (its Security object) and the private keys, so it
# runs on the maintainer's Mac and never in CI. The script is signed in a
# SEPARATE PixInsight instance (slot 9, started if need be), never in the
# one in use for work.
#
# Usage: ci/sign.sh ( --key-dir <folder> | --keys <file.xssk> --password-file <file> ) [--slot N] [--dry-run]
#
#   --key-dir        a folder holding the keys and their password: exactly one
#                    <name>.xssk, and its password in <name>.password (or, when
#                    that is missing, in a file called password or password.txt). Explicit
#                    --keys / --password-file win over what the folder gives.
#   --keys           the secure signing keys file (.xssk)
#   --password-file  a file holding that file's password; it is read by
#                    PixInsight itself and never copied. Keep it out of the
#                    repository and chmod 600 it.
#   --slot           PixInsight instance number to use (default 9)
#   --dry-run        print the keys and password files that would be used, and stop
#
# Every source file under script/ must be final: a signature covers the
# script and everything it includes, so any later edit invalidates it.

set -euo pipefail

root="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$root"

keys="" ; password_file="" ; key_dir="" ; slot=9 ; dry_run=0
usage() { sed -n '/^# Usage:/,/^# Every/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed '$d' >&2; exit 2; }
while [ $# -gt 0 ]; do
   case "$1" in
      --key-dir)        key_dir="${2:-}"; shift 2 ;;
      --keys)           keys="${2:-}"; shift 2 ;;
      --password-file)  password_file="${2:-}"; shift 2 ;;
      --slot)           slot="${2:-}"; shift 2 ;;
      --dry-run)        dry_run=1; shift ;;
      -h|--help)        usage ;;
      *) echo "sign: unknown argument: $1" >&2; usage ;;
   esac
done
if [ -n "$key_dir" ]; then
   [ -d "$key_dir" ] || { echo "sign: not a folder: $key_dir" >&2; exit 1; }
   if [ -z "$keys" ]; then
      found=( "$key_dir"/*.xssk )
      [ -e "${found[0]}" ] || { echo "sign: no .xssk keys file in $key_dir" >&2; exit 1; }
      [ "${#found[@]}" -eq 1 ] || { echo "sign: more than one .xssk in $key_dir; name one with --keys" >&2; exit 1; }
      keys="${found[0]}"
   fi
   if [ -z "$password_file" ]; then
      for c in "${keys%.xssk}.password" "$key_dir/password" "$key_dir/password.txt"; do
         [ -f "$c" ] && { password_file="$c"; break; }
      done
      [ -n "$password_file" ] || { echo "sign: no password file in $key_dir (expected $( basename "${keys%.xssk}" ).password, password or password.txt)" >&2; exit 1; }
   fi
fi
[ -n "$keys" ] && [ -n "$password_file" ] || { echo "sign: give --key-dir, or --keys and --password-file" >&2; usage; }
[ -f "$keys" ]          || { echo "sign: keys file not found: $keys" >&2; exit 1; }
[ -s "$password_file" ] || { echo "sign: password file missing or empty: $password_file" >&2; exit 1; }
# A password is one line. A file with more is a note, a copy of a message or
# the wrong file, and PixInsight would take all of it as the password and
# report only "wrong password".
[ "$( LC_ALL=C awk 'NF { n++ } END { print n + 0 }' "$password_file" )" -le 1 ] \
   || { echo "sign: $password_file has more than one line; it must hold only the password" >&2; exit 1; }
case "$slot" in ''|*[!0-9]*) echo "sign: --slot must be a number" >&2; exit 2 ;; esac

if [ "$dry_run" = 1 ]; then
   echo "keys:     $keys"
   echo "password: $password_file"
   exit 0
fi

pi="${LOOM_PIXINSIGHT:-/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight}"
[ -x "$pi" ] || { echo "sign: PixInsight not found at $pi (set LOOM_PIXINSIGHT)" >&2; exit 1; }

dir=/tmp/agent-scratch/loom-sign        # must match Sign.DIR in ci/sign.js
mkdir -p "$dir" ; chmod 700 "$dir"
rm -f "$dir/request.json" "$dir/result.json"
trap 'rm -f "$dir/request.json"' EXIT

abs() { ( cd "$( dirname "$1" )" && printf '%s/%s' "$PWD" "$( basename "$1" )" ); }
python3 - "$dir/request.json" "$( abs "$keys" )" "$( abs "$password_file" )" \
   "$root/script/Loom.js" "$root/script/FrameSelector.js" "$root/script/FlyThrough.js" <<'PY'
import json, os, sys
out, keys, pw, *files = sys.argv[1:]
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"keys": keys, "passwordFile": pw, "files": files}, f)
PY

if ! pgrep -f "PixInsight -n=$slot" >/dev/null; then
   echo "sign: starting PixInsight instance $slot"
   open -n -a "$( dirname "$pi" )/../.." --args -n="$slot" --automation-mode --no-splash
   sleep 15
fi
"$pi" -x="$slot:$root/ci/sign.js" >/dev/null 2>&1 || true

for _ in $( seq 1 120 ); do
   [ -f "$dir/result.json" ] && break
   sleep 1
done
[ -f "$dir/result.json" ] || { echo "sign: PixInsight did not answer within 2 minutes (is instance $slot running?)" >&2; exit 1; }

python3 - "$dir/result.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
for f in r["files"]:
    print("%s  %-22s %s  %s" % ("OK " if f["valid"] else "BAD", f["scriptId"] or "?", f["developerId"], f["signature"]))
if not r["ok"]:
    sys.exit("sign: FAILED: " + (r["error"] or "a signature is not valid"))
PY

node ci/check-signatures.js --write
echo "sign: done. Commit the .xsgn files and ci/signed-tree.sha256 with the release."
