/*
 * Keeping the installed copy of Loom current.
 *
 * The whole feature rests on one fact: PJSR resolves #include at PARSE
 * time, so a running script cannot reload its own libraries. An update
 * can only ever take effect in a later execution -- which means there is
 * no reason to wait for it now. So nothing here is waited on. The update
 * is spawned detached, the dialog opens immediately, and the next launch
 * parses whatever landed.
 *
 * That single decision removes the relaunch, the restart prompt, the
 * cooldown and the loop guard an earlier design needed, along with every
 * way they could fail.
 *
 * See docs/superpowers/specs/2026-09-17-auto-update-design.md.
 */

var Update = {};

/*
 * The GitHub repository the zip fallback reads releases from.
 *
 * Deliberately NOT configuration: a URL the user can set is a URL an
 * attacker can set, and this one names the code that will be executed.
 *
 * Empty until the mirror exists, and empty means the zip path does
 * nothing at all rather than guessing a URL.
 */
Update.GITHUB_OWNER = "";
Update.GITHUB_REPO = "";

/*
 * Written by the zip installer into the directory it creates.
 *
 * The zip path REPLACES a directory, so it must never run against a
 * directory it did not create. Absence of .git is not enough to prove
 * that -- see Update.installKind.
 */
Update.RELEASE_MARKER = "RELEASE";

/*
 * Updater state lives here, NOT in the cache.
 *
 * Cache.clear deletes every non-directory file in the cache folder, so
 * "Clear cache" would eat the outcome record; the cache folder is also
 * user-selectable, so changing it would strand previous results. And a
 * zip update renames script/ out from under itself, so nothing inside the
 * installation can be state either.
 */
Update.stateDir = function()
{
   return File.homeDirectory + "/.loom";
};

Update.OUTCOME_FILE = "update-last.txt";
Update.HISTORY_FILE = "update.log";
Update.LOCK_DIR = "update.lock";

/*
 * How long a detached update may run before it is killed. A fetch that
 * sits on an unreachable host must not leave a worker behind for the rest
 * of the session.
 */
Update.TIMEOUT_SECONDS = 120;

/* ------------------------------------------------------------------ */
/* Versions                                                            */
/* ------------------------------------------------------------------ */

/*
 * "1.2.3", "1.2", "v1.2.3" -> [ 1, 2, 3 ]. Anything else -> null.
 *
 * A missing patch is zero, so "0.1" and "0.1.0" compare equal.
 */
Update.parseVersion = function( text )
{
   if ( text == null )
      return null;
   var t = String( text ).trim();
   if ( t.length > 0 && ( t[0] == "v" || t[0] == "V" ) )
      t = t.substring( 1 );
   if ( !/^\d+\.\d+(\.\d+)?$/.test( t ) )
      return null;
   var parts = t.split( "." );
   return [ parseInt( parts[0], 10 ),
            parseInt( parts[1], 10 ),
            parts.length > 2 ? parseInt( parts[2], 10 ) : 0 ];
};

/* -1, 0 or 1. Null for either side means "cannot tell", reported as 0. */
Update.compareVersions = function( a, b )
{
   var x = Update.parseVersion( a );
   var y = Update.parseVersion( b );
   if ( x == null || y == null )
      return 0;
   for ( var i = 0; i < 3; ++i )
      if ( x[i] != y[i] )
         return ( x[i] < y[i] ) ? -1 : 1;
   return 0;
};

/*
 * Is this release tag worth downloading? Unparseable tags are NOT newer:
 * an update is only ever taken on a positive answer.
 */
Update.isNewerTag = function( tag, current )
{
   if ( Update.parseVersion( tag ) == null )
      return false;
   return Update.compareVersions( tag, current ) > 0;
};

/* ------------------------------------------------------------------ */
/* Where and what this installation is                                 */
/* ------------------------------------------------------------------ */

/*
 * .git is a DIRECTORY in an ordinary clone and a FILE in a linked
 * worktree or a submodule checkout.
 *
 * Testing only for the directory reports "not a repository" for a real
 * checkout, which would route it into the zip path -- the path that
 * replaces the directory wholesale. That is precisely the outcome the
 * rule exists to prevent, so both forms count.
 */
Update.isGitManaged = function( dir, io )
{
   var g = dir + "/.git";
   return io.directoryExists( g ) || io.fileExists( g );
};

/*
 * "git", "release" or "unknown".
 *
 * "unknown" -- no .git and no marker -- is left alone. A directory is
 * only ever replaced if this feature created it.
 */
Update.installKind = function( dir, io )
{
   if ( Update.isGitManaged( dir, io ) )
      return "git";
   if ( io.fileExists( dir + "/" + Update.RELEASE_MARKER ) )
      return "release";
   return "unknown";
};

/*
 * The short commit, read straight out of .git with no git binary.
 *
 * Handles the three shapes HEAD can take: a symbolic ref to a loose ref,
 * a symbolic ref resolved through packed-refs, and a detached HEAD
 * holding the id directly.
 */
Update.headCommit = function( dir, io )
{
   try
   {
      var headPath = dir + "/.git/HEAD";
      if ( !io.fileExists( headPath ) )
         return "";
      var head = String( io.readText( headPath ) ).trim();
      if ( /^[0-9a-f]{40}$/.test( head ) )
         return head.substring( 0, 7 );

      var m = /^ref:\s*(\S+)$/.exec( head );
      if ( m == null )
         return "";
      var refPath = dir + "/.git/" + m[1];
      if ( io.fileExists( refPath ) )
         return String( io.readText( refPath ) ).trim().substring( 0, 7 );

      var packed = dir + "/.git/packed-refs";
      if ( io.fileExists( packed ) )
      {
         var lines = String( io.readText( packed ) ).split( "\n" );
         for ( var i = 0; i < lines.length; ++i )
         {
            var p = lines[i].split( " " );
            if ( p.length == 2 && p[1].trim() == m[1] )
               return p[0].substring( 0, 7 );
         }
      }
   }
   catch ( e ) { /* identity is a nicety; never fail a launch for it */ }
   return "";
};

/*
 * "Loom 0.1 (a4c1f2e)", or "Loom 0.1" where there is no repository.
 *
 * The version alone does not identify the code -- the git path installs
 * branch commits, and many commits share one version number.
 */
Update.describeVersion = function( dir, io )
{
   var sha = Update.headCommit( dir, io );
   return "Loom " + Util.LOOM_VERSION + ( sha.length > 0 ? ( " (" + sha + ")" ) : "" );
};

/* ------------------------------------------------------------------ */
/* Finding git                                                         */
/* ------------------------------------------------------------------ */

/*
 * /usr/bin/git EXISTS on every Mac whether or not git is installed: it is
 * a stub that opens the "Install Command Line Developer Tools" dialog
 * when run. Probing by execution can therefore throw a modal system
 * installer in the user's face at startup.
 *
 * So each candidate carries a guard: another path that must also exist
 * before the candidate may be executed. Homebrew's git is a real binary
 * and needs none; /usr/bin/git is only trustworthy when a real git sits
 * behind it.
 */
Update.gitCandidates = function( platform, home )
{
   if ( Util.isWindows( platform ) )
   {
      if ( home === undefined )
         try { home = File.homeDirectory; } catch ( e ) { home = ""; }
      /*
       * Git for Windows has no /usr/bin stub problem, so no guards: these
       * paths exist only if git is really installed. The third is where
       * the installer puts it when it is run without elevation, which is
       * the common case on a managed machine.
       */
      var w = [ { path: "C:/Program Files/Git/cmd/git.exe", guards: [] },
                { path: "C:/Program Files (x86)/Git/cmd/git.exe", guards: [] } ];
      if ( home != null && String( home ).length > 0 )
         w.push( { path: home + "/AppData/Local/Programs/Git/cmd/git.exe",
                   guards: [] } );
      return w;
   }
   return [
      { path: "/opt/homebrew/bin/git", guards: [] },
      { path: "/usr/local/bin/git",    guards: [] },
      { path: "/usr/bin/git",
        guards: [ "/Library/Developer/CommandLineTools/usr/bin/git",
                  "/Applications/Xcode.app/Contents/Developer/usr/bin/git" ] }
   ];
};

/* First candidate that exists and whose guards (if any) are satisfied. */
Update.resolveGitPath = function( candidates, io )
{
   for ( var i = 0; i < candidates.length; ++i )
   {
      var c = candidates[i];
      if ( !io.fileExists( c.path ) )
         continue;
      if ( c.guards.length == 0 )
         return c.path;
      for ( var g = 0; g < c.guards.length; ++g )
         if ( io.fileExists( c.guards[g] ) )
            return c.path;
   }
   return null;
};

/* Does this `git --version` result name a working binary? */
Update.isWorkingGit = function( result )
{
   return result != null && result.exitCode == 0 &&
          /^git version /.test( String( result.output || "" ).trim() );
};

/*
 * The one synchronous call in the whole feature.
 *
 * It is local, cannot touch the network, and is the only way to know the
 * binary works rather than merely existing. Acknowledged as a bounded
 * exception to "startup never waits".
 */
Update.usableGit = function( io, platform, home )
{
   var path = Update.resolveGitPath( Update.gitCandidates( platform, home ), io );
   if ( path == null )
      return null;
   return Update.isWorkingGit( io.execute( path, [ "--version" ] ) ) ? path : null;
};

/* ------------------------------------------------------------------ */
/* Outcome records                                                     */
/* ------------------------------------------------------------------ */

/*
 * One line, structured, written by the helper script.
 *
 * NOT a shell redirect of whatever git printed. A redirect records text,
 * not outcome: `>` truncates the file when the process starts, so an
 * empty file cannot distinguish success from a refusal from a process
 * still running from one that was killed.
 */
Update.STATUSES = [ "updated", "unchanged", "skipped-dirty",
                    "skipped-no-upstream", "failed" ];

Update.formatOutcome = function( o )
{
   return [ o.status, String( o.exitCode ), o.from || "-", o.to || "-",
            o.when || "-", ( o.message || "" ).replace( /[\r\n]+/g, " " )
          ].join( "\t" );
};

/*
 * Parses a record, or returns null for anything malformed.
 *
 * A truncated line is reported as incomplete rather than as success: a
 * worker killed mid-write must not be mistaken for one that finished.
 */
Update.parseOutcome = function( line )
{
   if ( line == null )
      return null;
   var f = String( line ).replace( /[\r\n]+$/, "" ).split( "\t" );
   if ( f.length < 5 )
      return null;
   if ( Update.STATUSES.indexOf( f[0] ) < 0 )
      return null;
   return { status: f[0], exitCode: parseInt( f[1], 10 ),
            from: f[2], to: f[3], when: f[4],
            message: ( f.length > 5 ) ? f.slice( 5 ).join( "\t" ) : "" };
};

/* What the user is told, in one line. */
Update.outcomeMessage = function( o )
{
   switch ( o.status )
   {
   case "updated":
      return "updated " + o.from + " -> " + o.to;
   case "unchanged":
      return "already up to date";
   case "skipped-dirty":
      return "not updated: there are local changes";
   case "skipped-no-upstream":
      return "not updated: no upstream branch to update from";
   default:
      return "update failed (" + o.exitCode + "): " + o.message;
   }
};

Update.isFailure = function( o )
{
   return o.status == "failed" || o.status == "skipped-dirty" ||
          o.status == "skipped-no-upstream";
};

/* ------------------------------------------------------------------ */
/* The helper scripts                                                  */
/* ------------------------------------------------------------------ */

/*
 * There are two of everything below, one per shell, because there is no
 * shell both platforms have.
 *
 * The POSIX half is the original and is unchanged. The Windows half is
 * PowerShell rather than cmd.exe, for one decisive reason: PJSR hands out
 * paths with forward slashes on Windows too ("C:/Users/..."), and cmd.exe
 * reads a leading "/" as the start of a switch. PowerShell takes those
 * paths as they come. It is also the only one of the two that can write a
 * file without a BOM and rename it atomically without a helper.
 *
 * The two must stay behaviourally identical: same guards, same outcome
 * records, same single-flight lock. The selftest asserts every guard
 * separately for each.
 */

/* Quoting for each shell, so a path or branch name cannot become syntax. */
Update.quotePosix = function( s )
{
   return "'" + String( s ).replace( /'/g, "'\\''" ) + "'";
};

/* PowerShell single-quoted literals: nothing expands; '' is a literal '. */
Update.quotePowerShell = function( s )
{
   return "'" + String( s ).replace( /'/g, "''" ) + "'";
};

/*
 * The helper's filename and how it is launched. Kept together because the
 * extension is not cosmetic: PowerShell -File refuses anything that is not
 * .ps1.
 */
Update.helperFileName = function( platform )
{
   return Util.isWindows( platform ) ? "update-run.ps1" : "update-run.sh";
};

Update.helperCommand = function( platform, path )
{
   if ( Util.isWindows( platform ) )
      /*
       * -ExecutionPolicy Bypass because the default policy on a fresh
       * Windows install (Restricted) refuses to run a script FILE at all;
       * -NoProfile so a user profile cannot change git's environment or
       * slow the launch; -NonInteractive so nothing can ever sit waiting
       * for input in a detached process nobody can see.
       */
      return { program: "powershell.exe",
               args: [ "-NoProfile", "-NonInteractive",
                       "-ExecutionPolicy", "Bypass", "-File", path ] };
   return { program: "/bin/sh", args: [ path ] };
};

/* Dispatches on o.platform, defaulting to the platform Loom is running on. */
Update.gitScript = function( o )
{
   return Util.isWindows( o.platform ) ? Update.gitScriptPowerShell( o )
                                       : Update.gitScriptPosix( o );
};

Update.zipScript = function( o )
{
   return Util.isWindows( o.platform ) ? Update.zipScriptPowerShell( o )
                                       : Update.zipScriptPosix( o );
};

/*
 * Written to a file and run as `/bin/sh <file>` rather than interpolated
 * into `sh -c`: paths, branch names and URLs cannot then break quoting or
 * turn into shell syntax.
 *
 * Every guard below earned its place by being wrong in an earlier draft;
 * the selftest asserts each one is still present.
 */
Update.gitScriptPosix = function( o )
{
   var q = Update.quotePosix;
   return [
      "#!/bin/sh",
      "GIT=" + q( o.git ),
      "DIR=" + q( o.dir ),
      "STATE=" + q( o.stateDir ),
      "LOCK=$STATE/" + Update.LOCK_DIR,
      "OUT=$STATE/" + Update.OUTCOME_FILE,
      "TMP=$STATE/.update-$$",
      "",
      "# A background fetch that hits an authentication prompt would wait",
      "# for ever, invisibly, one worker per launch.",
      "GIT_TERMINAL_PROMPT=0; export GIT_TERMINAL_PROMPT",
      "GIT_SSH_COMMAND='ssh -o BatchMode=yes'; export GIT_SSH_COMMAND",
      "",
      "# mkdir is atomic: two launches cannot both update.",
      "mkdir \"$LOCK\" 2>/dev/null || exit 0",
      "trap 'rmdir \"$LOCK\" 2>/dev/null' EXIT",
      "",
      "report() {",
      "  printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$1\" \"$2\" \"$3\" \"$4\" \\",
      "    \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" \"$5\" > \"$TMP\"",
      "  cat \"$TMP\" >> \"$STATE/" + Update.HISTORY_FILE + "\"",
      "  mv \"$TMP\" \"$OUT\"",       // atomic publication; no partial reads
      "}",
      "",
      "# status --porcelain, and not a plain diff against the index: that",
      "# misses staged changes and untracked files entirely.",
      "if [ -n \"$(\"$GIT\" -C \"$DIR\" status --porcelain=v1 --untracked-files=all 2>&1)\" ]; then",
      "  report skipped-dirty 0 - - 'local changes present'",
      "  exit 0",
      "fi",
      "",
      "# Resolve the upstream and fetch THAT. 'fetch origin <branch>' then",
      "# 'merge @{u}' can target different refs, or different remotes.",
      "UP=$(\"$GIT\" -C \"$DIR\" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)",
      "if [ -z \"$UP\" ]; then",
      "  report skipped-no-upstream 0 - - 'detached HEAD or no upstream'",
      "  exit 0",
      "fi",
      "REMOTE=${UP%%/*}",
      "BRANCH=${UP#*/}",
      "FROM=$(\"$GIT\" -C \"$DIR\" rev-parse --short HEAD 2>/dev/null)",
      "",
      "ERR=$(\"$GIT\" -C \"$DIR\" -c merge.autoStash=false fetch -q \"$REMOTE\" \"$BRANCH\" 2>&1)",
      "if [ $? -ne 0 ]; then report failed $? \"$FROM\" - \"$ERR\"; exit 0; fi",
      "",
      "ERR=$(\"$GIT\" -C \"$DIR\" -c merge.autoStash=false merge --ff-only -q '@{u}' 2>&1)",
      "RC=$?",
      "TO=$(\"$GIT\" -C \"$DIR\" rev-parse --short HEAD 2>/dev/null)",
      "if [ $RC -ne 0 ]; then report failed $RC \"$FROM\" \"$TO\" \"$ERR\"; exit 0; fi",
      "if [ \"$FROM\" = \"$TO\" ]; then report unchanged 0 \"$FROM\" \"$TO\" ''; exit 0; fi",
      "report updated 0 \"$FROM\" \"$TO\" ''",
      ""
   ].join( "\n" );
};

/*
 * The Windows git updater. Line for line the same decisions as the POSIX
 * one above; only the language differs. Read them side by side.
 *
 * $ErrorActionPreference is 'Continue', NOT 'Stop'. Under 'Stop',
 * PowerShell 5.1 turns anything a native program writes to stderr into a
 * terminating NativeCommandError -- so a perfectly ordinary `git fetch`
 * progress line would abort the update. Every failure here is therefore
 * detected the way git reports it, through $LASTEXITCODE, and the whole
 * body sits in a try/catch so an unexpected throw still leaves a record.
 *
 * Every file this writes goes through [System.IO.File], never Set-Content
 * or Out-File: in PowerShell 5.1 those write ANSI or UTF-16-with-BOM, and
 * a BOM in front of the status word makes Update.parseOutcome reject the
 * record as malformed.
 */
Update.gitScriptPowerShell = function( o )
{
   var q = Update.quotePowerShell;
   return [
      "# Loom updater. Generated; do not edit.",
      "$ErrorActionPreference = 'Continue'",
      "$GIT   = " + q( o.git ),
      "$DIR   = " + q( o.dir ),
      "$STATE = " + q( o.stateDir ),
      "$LOCK  = $STATE + '/" + Update.LOCK_DIR + "'",
      "$OUT   = $STATE + '/" + Update.OUTCOME_FILE + "'",
      "$HIST  = $STATE + '/" + Update.HISTORY_FILE + "'",
      "$TMP   = $STATE + '/.update-' + $PID",
      "",
      "# A background fetch that hits an authentication prompt would wait",
      "# for ever, invisibly, one worker per launch.",
      "$env:GIT_TERMINAL_PROMPT = '0'",
      "$env:GIT_SSH_COMMAND = 'ssh -o BatchMode=yes'",
      "",
      "function Report($status, $code, $from, $to, $message) {",
      "  $when = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')",
      "  $msg  = ([string]$message) -replace '[\\r\\n]+', ' '",
      "  $line = @($status, [string]$code, $from, $to, $when, $msg) -join \"`t\"",
      "  [System.IO.File]::WriteAllText($TMP, $line + \"`n\")",
      "  [System.IO.File]::AppendAllText($HIST, $line + \"`n\")",
      "  Move-Item -LiteralPath $TMP -Destination $OUT -Force",  // atomic publication
      "}",
      "",
      "# Creating a directory is atomic and fails if it already exists:",
      "# two launches cannot both update.",
      "try { New-Item -ItemType Directory -Path $LOCK -ErrorAction Stop | Out-Null }",
      "catch { exit 0 }",
      "",
      "try {",
      "  # status --porcelain, and not a plain diff against the index: that",
      "  # misses staged changes and untracked files entirely.",
      "  $ST = (& $GIT -C $DIR status --porcelain=v1 --untracked-files=all 2>&1 | Out-String)",
      "  if ($ST.Trim() -ne '') {",
      "    Report 'skipped-dirty' 0 '-' '-' 'local changes present'; exit 0 }",
      "",
      "  # Resolve the upstream and fetch THAT. 'fetch origin <branch>' then",
      "  # 'merge @{u}' can target different refs, or different remotes.",
      "  $UP = (& $GIT -C $DIR rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null |",
      "         Select-Object -First 1)",
      "  if (-not $UP) {",
      "    Report 'skipped-no-upstream' 0 '-' '-' 'detached HEAD or no upstream'; exit 0 }",
      "  $UP = ([string]$UP).Trim()",
      "  # A branch name may contain '/', a remote name may not: split once.",
      "  $REMOTE = $UP.Substring(0, $UP.IndexOf('/'))",
      "  $BRANCH = $UP.Substring($UP.IndexOf('/') + 1)",
      "  $FROM = (& $GIT -C $DIR rev-parse --short HEAD 2>$null | Select-Object -First 1)",
      "  if (-not $FROM) { $FROM = '-' } else { $FROM = ([string]$FROM).Trim() }",
      "",
      "  $ERR = (& $GIT -C $DIR -c merge.autoStash=false fetch -q $REMOTE $BRANCH 2>&1 | Out-String)",
      "  if ($LASTEXITCODE -ne 0) { Report 'failed' $LASTEXITCODE $FROM '-' $ERR; exit 0 }",
      "",
      "  $ERR = (& $GIT -C $DIR -c merge.autoStash=false merge --ff-only -q '@{u}' 2>&1 | Out-String)",
      "  $RC = $LASTEXITCODE",
      "  $TO = (& $GIT -C $DIR rev-parse --short HEAD 2>$null | Select-Object -First 1)",
      "  if (-not $TO) { $TO = '-' } else { $TO = ([string]$TO).Trim() }",
      "  if ($RC -ne 0) { Report 'failed' $RC $FROM $TO $ERR; exit 0 }",
      "  if ($FROM -eq $TO) { Report 'unchanged' 0 $FROM $TO ''; exit 0 }",
      "  Report 'updated' 0 $FROM $TO ''",
      "}",
      "catch {",
      "  # Nothing may end without a record: a launch that finds neither an",
      "  # outcome nor a lock has no way to tell 'never ran' from 'died'.",
      "  Report 'failed' 1 '-' '-' ([string]$_)",
      "}",
      "finally {",
      "  Remove-Item -LiteralPath $LOCK -Recurse -Force -ErrorAction SilentlyContinue",
      "}",
      ""
   ].join( "\n" );
};

/*
 * The zip fallback, for installations that did not come from git.
 *
 * curl and tar rather than PJSR's in-process NetworkTransfer, because
 * this runs detached and nothing here may touch the main thread.
 *
 * The swap is two renames on one filesystem. A process killed between
 * them leaves script.old in place; recovery is one mv, which is the
 * accepted trade against building an immutable-release launcher.
 */
Update.zipScriptPosix = function( o )
{
   var q = Update.quotePosix;
   var api = "https://api.github.com/repos/" + o.owner + "/" + o.repo +
             "/releases/latest";
   return [
      "#!/bin/sh",
      "DIR=" + q( o.dir ),
      "STATE=" + q( o.stateDir ),
      "CURRENT=" + q( o.version ),
      "LOCK=$STATE/" + Update.LOCK_DIR,
      "OUT=$STATE/" + Update.OUTCOME_FILE,
      "TMP=$STATE/.update-$$",
      "WORK=$STATE/staging-$$",
      "",
      "mkdir \"$LOCK\" 2>/dev/null || exit 0",
      "trap 'rmdir \"$LOCK\" 2>/dev/null; rm -rf \"$WORK\"' EXIT",
      "",
      "report() {",
      "  printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$1\" \"$2\" \"$3\" \"$4\" \\",
      "    \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" \"$5\" > \"$TMP\"",
      "  cat \"$TMP\" >> \"$STATE/" + Update.HISTORY_FILE + "\"",
      "  mv \"$TMP\" \"$OUT\"",
      "}",
      "",
      "# -f so an HTML error page is an error, not a 200-byte 'release'.",
      "# --proto '=https' so a redirect cannot downgrade the transport.",
      "JSON=$(curl -fsSL --proto '=https' " + q( api ) + " 2>&1) || {",
      "  report failed 1 \"$CURRENT\" - \"$JSON\"; exit 0; }",
      "TAG=$(printf '%s' \"$JSON\" | sed -n 's/.*\"tag_name\"[ ]*:[ ]*\"\\([^\"]*\\)\".*/\\1/p' | head -1)",
      "URL=$(printf '%s' \"$JSON\" | sed -n 's/.*\"browser_download_url\"[ ]*:[ ]*\"\\([^\"]*\\)\".*/\\1/p' | head -1)",
      "if [ -z \"$TAG\" ] || [ -z \"$URL\" ]; then",
      "  report failed 1 \"$CURRENT\" - 'no release asset published'; exit 0; fi",
      "if [ \"${TAG#v}\" = \"$CURRENT\" ]; then",
      "  report unchanged 0 \"$CURRENT\" \"$CURRENT\" ''; exit 0; fi",
      "",
      "mkdir -p \"$WORK\" || exit 0",
      "curl -fsSL --proto '=https' -o \"$WORK/release.zip\" \"$URL\" || {",
      "  report failed 1 \"$CURRENT\" \"$TAG\" 'download failed'; exit 0; }",
      "mkdir \"$WORK/tree\" || exit 0",
      "tar -xf \"$WORK/release.zip\" -C \"$WORK/tree\" || {",
      "  report failed 1 \"$CURRENT\" \"$TAG\" 'archive would not extract'; exit 0; }",
      "",
      "# Verify against the NEW release: requiring every file the OLD copy",
      "# had would reject a release that legitimately renamed one.",
      "if [ ! -f \"$WORK/tree/Loom.js\" ] || [ ! -d \"$WORK/tree/lib\" ]; then",
      "  report failed 1 \"$CURRENT\" \"$TAG\" 'release is missing Loom.js or lib/'; exit 0; fi",
      "",
      "touch \"$WORK/tree/" + Update.RELEASE_MARKER + "\"",
      "rm -rf \"$DIR.old\"",
      "mv \"$DIR\" \"$DIR.old\" || { report failed 1 \"$CURRENT\" \"$TAG\" 'could not move the old copy aside'; exit 0; }",
      "mv \"$WORK/tree\" \"$DIR\" || {",
      "  mv \"$DIR.old\" \"$DIR\"",     // the one rollback that can still run
      "  report failed 1 \"$CURRENT\" \"$TAG\" 'install failed, rolled back'; exit 0; }",
      "rm -rf \"$DIR.old\"",
      "report updated 0 \"$CURRENT\" \"$TAG\" ''",
      ""
   ].join( "\n" );
};

/*
 * The Windows zip fallback. Same shape as the POSIX one: fetch the
 * release metadata, refuse anything that is not newer, stage into a
 * scratch directory, and swap by rename with a rollback.
 *
 * Invoke-RestMethod and Expand-Archive rather than curl and tar. Windows
 * does ship both of those now, but only on recent builds, and tar.exe
 * reading a .zip is a bsdtar detail rather than a guarantee; the
 * PowerShell calls are present on every machine that can run the rest of
 * this file.
 *
 * TLS 1.2 is forced because PowerShell 5.1 inherits .NET's default, which
 * on an un-updated machine still offers TLS 1.0 -- and GitHub refuses it,
 * producing the unhelpful "the underlying connection was closed".
 */
Update.zipScriptPowerShell = function( o )
{
   var q = Update.quotePowerShell;
   var api = "https://api.github.com/repos/" + o.owner + "/" + o.repo +
             "/releases/latest";
   return [
      "# Loom updater. Generated; do not edit.",
      "$ErrorActionPreference = 'Stop'",
      "$DIR     = " + q( o.dir ),
      "$STATE   = " + q( o.stateDir ),
      "$CURRENT = " + q( o.version ),
      "$LOCK  = $STATE + '/" + Update.LOCK_DIR + "'",
      "$OUT   = $STATE + '/" + Update.OUTCOME_FILE + "'",
      "$HIST  = $STATE + '/" + Update.HISTORY_FILE + "'",
      "$TMP   = $STATE + '/.update-' + $PID",
      "$WORK  = $STATE + '/staging-' + $PID",
      "",
      "function Report($status, $code, $from, $to, $message) {",
      "  $when = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')",
      "  $msg  = ([string]$message) -replace '[\\r\\n]+', ' '",
      "  $line = @($status, [string]$code, $from, $to, $when, $msg) -join \"`t\"",
      "  [System.IO.File]::WriteAllText($TMP, $line + \"`n\")",
      "  [System.IO.File]::AppendAllText($HIST, $line + \"`n\")",
      "  Move-Item -LiteralPath $TMP -Destination $OUT -Force",
      "}",
      "",
      "try { New-Item -ItemType Directory -Path $LOCK -ErrorAction Stop | Out-Null }",
      "catch { exit 0 }",
      "",
      "try {",
      "  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12",
      "  $JSON = Invoke-RestMethod -UseBasicParsing -Uri " + q( api ) +
                " -Headers @{ 'User-Agent' = 'Loom' }",
      "  $TAG = [string]$JSON.tag_name",
      "  $URL = ''",
      "  if ($JSON.assets -and $JSON.assets.Count -gt 0) {",
      "    $URL = [string]$JSON.assets[0].browser_download_url }",
      "  if (-not $TAG -or -not $URL) {",
      "    Report 'failed' 1 $CURRENT '-' 'no release asset published'; exit 0 }",
      "  # The transport cannot be downgraded by whatever the API hands back.",
      "  if (-not $URL.StartsWith('https://')) {",
      "    Report 'failed' 1 $CURRENT $TAG 'release asset is not served over https'; exit 0 }",
      "  if ($TAG.TrimStart('v') -eq $CURRENT) {",
      "    Report 'unchanged' 0 $CURRENT $CURRENT ''; exit 0 }",
      "",
      "  New-Item -ItemType Directory -Path $WORK -Force | Out-Null",
      "  New-Item -ItemType Directory -Path ($WORK + '/tree') -Force | Out-Null",
      "  Invoke-WebRequest -UseBasicParsing -Uri $URL -OutFile ($WORK + '/release.zip')",
      "  Expand-Archive -LiteralPath ($WORK + '/release.zip') -DestinationPath ($WORK + '/tree') -Force",
      "",
      "  # Verify against the NEW release: requiring every file the OLD copy",
      "  # had would reject a release that legitimately renamed one.",
      "  if (-not (Test-Path -LiteralPath ($WORK + '/tree/Loom.js')) -or",
      "      -not (Test-Path -LiteralPath ($WORK + '/tree/lib'))) {",
      "    Report 'failed' 1 $CURRENT $TAG 'release is missing Loom.js or lib/'; exit 0 }",
      "",
      "  [System.IO.File]::WriteAllText($WORK + '/tree/" + Update.RELEASE_MARKER + "', '')",
      "  Remove-Item -LiteralPath ($DIR + '.old') -Recurse -Force -ErrorAction SilentlyContinue",
      "  Move-Item -LiteralPath $DIR -Destination ($DIR + '.old') -Force",
      "  try { Move-Item -LiteralPath ($WORK + '/tree') -Destination $DIR -Force }",
      "  catch {",
      "    Move-Item -LiteralPath ($DIR + '.old') -Destination $DIR -Force",  // the one rollback that can still run
      "    Report 'failed' 1 $CURRENT $TAG 'install failed, rolled back'; exit 0 }",
      "  Remove-Item -LiteralPath ($DIR + '.old') -Recurse -Force -ErrorAction SilentlyContinue",
      "  Report 'updated' 0 $CURRENT $TAG ''",
      "}",
      "catch { Report 'failed' 1 $CURRENT '-' ([string]$_) }",
      "finally {",
      "  Remove-Item -LiteralPath $WORK -Recurse -Force -ErrorAction SilentlyContinue",
      "  Remove-Item -LiteralPath $LOCK -Recurse -Force -ErrorAction SilentlyContinue",
      "}",
      ""
   ].join( "\n" );
};

/* ------------------------------------------------------------------ */
/* Real-world plumbing, isolated so everything above can be tested      */
/* ------------------------------------------------------------------ */

Update.io = {
   fileExists:      function( p ) { return File.exists( p ); },
   directoryExists: function( p ) { return File.directoryExists( p ); },
   readText:        function( p ) { return File.readTextFile( p ); },
   writeText:       function( p, t ) { File.writeTextFile( p, t ); },
   remove:          function( p ) { File.remove( p ); },
   rename:          function( a, b ) { File.move( a, b ); },
   makeDirectory:   function( p ) { File.createDirectory( p, true ); },
   /*
    * Util.PLATFORM, not CoreApplication.platform. The core does expose a
    * platform string at runtime ("macOS" here), but nothing documents
    * what it says on Windows, so comparing against it would be a guess.
    * Util.PLATFORM comes from the preprocessor's __PI_PLATFORM__, whose
    * spellings PixInsight's own bundled scripts rely on.
    */
   platform:        function() { return Util.PLATFORM; },

   execute: function( program, args )
   {
      var P = new ExternalProcess;
      P.start( program, args );
      P.waitForFinished( 5000 );
      return { exitCode: P.exitCode,
               output: P.standardOutput ? P.standardOutput.toString() : "" };
   },

   spawnDetached: function( program, args )
   {
      ExternalProcess.startDetached( program, args );
   }
};

/* ------------------------------------------------------------------ */
/* Orchestration                                                       */
/* ------------------------------------------------------------------ */

/*
 * Reports what the PREVIOUS launch's update did, then forgets it.
 *
 * Claimed by RENAME rather than by read-then-delete, so two launches
 * cannot both report the same record.
 *
 * Note this reaches the Process console only: console.beginLog opens
 * later, after the dialog and preflight, and not at all for a cancelled
 * or validate-only run. The durable trail is ~/.loom/update.log, written
 * by the helper itself.
 */
Update.reportLast = function( io )
{
   io = io || Update.io;
   try
   {
      var path = Update.stateDir() + "/" + Update.OUTCOME_FILE;
      if ( !io.fileExists( path ) )
         return null;
      var claimed = path + ".reading";
      io.rename( path, claimed );
      var outcome = Update.parseOutcome( io.readText( claimed ) );
      io.remove( claimed );
      if ( outcome == null )
      {
         Util.warn( "update", "the last update left an incomplete record; " +
                              "it may have been interrupted" );
         return null;
      }
      if ( outcome.status == "unchanged" )
         return outcome;          // nothing happened; say nothing
      if ( Update.isFailure( outcome ) )
         Util.warn( "update", Update.outcomeMessage( outcome ) );
      else
         Util.log( "update", Update.outcomeMessage( outcome ) );
      return outcome;
   }
   catch ( e )
   {
      // Reporting an update must never be able to stop Loom starting.
      return null;
   }
};

/*
 * Decides what to spawn. Returns the kind spawned, or null, which is what
 * the selftest asserts on.
 */
Update.start = function( config, io )
{
   io = io || Update.io;
   try
   {
      if ( !config || !config.autoUpdate )
         return null;

      var dir = Update.installDir();
      var state = Update.stateDir();
      if ( !io.directoryExists( state ) )
         io.makeDirectory( state );

      var kind = Update.installKind( dir, io );
      var platform = io.platform();
      var script = null;

      if ( kind == "git" )
      {
         /*
          * A checkout is only ever updated by git. Falling through to the
          * zip path here would replace a working tree behind git's back:
          * permanently dirty, and refused by every later --ff-only.
          */
         var git = Update.usableGit( io, platform );
         if ( git == null )
            return null;
         script = Update.gitScript( { git: git, dir: dir, stateDir: state,
                                      platform: platform } );
      }
      else if ( kind == "release" )
      {
         if ( Update.GITHUB_OWNER.length == 0 || Update.GITHUB_REPO.length == 0 )
            return null;          // no mirror configured; guess nothing
         script = Update.zipScript( { dir: dir, stateDir: state,
                                      version: Util.LOOM_VERSION,
                                      owner: Update.GITHUB_OWNER,
                                      repo: Update.GITHUB_REPO,
                                      platform: platform } );
      }
      else
         return null;             // not ours to touch

      var path = state + "/" + Update.helperFileName( platform );
      io.writeText( path, script );
      var cmd = Update.helperCommand( platform, path );
      io.spawnDetached( cmd.program, cmd.args );
      return kind;
   }
   catch ( e )
   {
      // An updater that cannot start is not a reason not to start Loom.
      return null;
   }
};

/*
 * The installation directory: the folder holding Loom.js, found from the
 * script's own path rather than from configuration.
 */
Update.installDir = function()
{
   return Update.SCRIPT_DIR || "";
};
