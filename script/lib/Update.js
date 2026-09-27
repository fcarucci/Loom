/*
 * Keeping a git checkout of Loom current.
 *
 * Only a checkout updates itself: at startup, before the dialog, a
 * blocking check fast-forwards it to its upstream and, if that moved it,
 * Loom relaunches on the new code (PJSR resolves #include at parse time,
 * so the running script cannot pick it up). Every other install -- the
 * release zip or PixInsight's update repository -- is updated by
 * PixInsight's own update mechanism, and this file leaves it alone.
 *
 * See docs/superpowers/specs/2026-09-17-auto-update-design.md.
 */

var Update = {};


/*
 * Updater state lives in a SUBDIRECTORY of the cache, beside the run logs.
 *
 * Not a dotfile in the home directory: Loom keeps its working state in one
 * place the user chose, and scattering it is how a tool becomes something
 * you cannot fully uninstall.
 *
 * Not loose in the cache folder either -- Cache.clear deletes every
 * non-directory file there, so "Clear cache" would eat the record. It
 * skips directories, which is exactly why the run logs already live in
 * one. Nothing may live inside the installation, because a fast-forward
 * would see it as local changes and refuse to update.
 *
 * Changing the cache folder strands an unreported record. That is the
 * right trade: the state belongs to the cache the user pointed Loom at.
 */
Update.stateDir = function()
{
   return Cache.dir() + "/update";
};

Update.OUTCOME_FILE = "update-last.txt";
Update.HISTORY_FILE = "update.log";
Update.LOCK_DIR = "update.lock";

/* ------------------------------------------------------------------ */
/* Where and what this installation is                                 */
/* ------------------------------------------------------------------ */

/*
 * .git is a DIRECTORY in an ordinary clone and a FILE in a linked
 * worktree or a submodule checkout.
 *
 * Testing only for the directory reports "not a repository" for a real
 * checkout, which would then never be updated. Both forms count.
 */
Update.isGitManaged = function( dir, io )
{
   var g = dir + "/.git";
   return io.directoryExists( g ) || io.fileExists( g );
};

/*
 * Does this installation update itself? Only a git checkout does: the
 * dialog offers "Update Loom automatically" on that answer alone.
 */
Update.isCheckout = function( io )
{
   io = io || Update.io;
   return Update.isGitManaged( Update.installDir(), io );
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
       * for input in a process nobody can see.
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

/*
 * Written to a file and run as `/bin/sh <file>` rather than interpolated
 * into `sh -c`: paths and branch names cannot then break quoting or turn
 * into shell syntax.
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
      "trap 'rmdir \"$LOCK\" 2>/dev/null; rm -f \"$STATE/.err-$$\"' EXIT",
      "",
      "report() {",
      "  # git's own messages run to many lines -- a refused fast-forward",
      "  # prints a paragraph of hints -- and a record is ONE line. Folding",
      "  # them here keeps the file parseable and the log readable.",
      "  MSG=$(printf '%s' \"$5\" | tr '\\n\\r' '  ')",
      "  printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$1\" \"$2\" \"$3\" \"$4\" \\",
      "    \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" \"$MSG\" > \"$TMP\"",
      "  cat \"$TMP\" >> \"$STATE/" + Update.HISTORY_FILE + "\"",
      "  mv \"$TMP\" \"$OUT\"",       // atomic publication; no partial reads
      "}",
      "",
      "# status --porcelain, and not a plain diff against the index: that",
      "# misses staged changes and untracked files entirely.",
      "# stderr goes to its own file, NOT into the captured output. Merging",
      "# them made ANY git failure -- a missing binary, an unreadable repo --",
      "# look like uncommitted work, so the user was told they had local",
      "# changes they did not have and the real error was never reported.",
      "ERRF=\"$STATE/.err-$$\"",
      "DIRTY=$(\"$GIT\" -C \"$DIR\" status --porcelain=v1 --untracked-files=all 2>\"$ERRF\")",
      "RC=$?",
      "if [ $RC -ne 0 ]; then",
      "  report failed $RC - - \"$(cat \"$ERRF\" 2>/dev/null)\"",
      "  rm -f \"$ERRF\"",
      "  exit 0",
      "fi",
      "rm -f \"$ERRF\"",
      "if [ -n \"$DIRTY\" ]; then",
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
      "# Captured IMMEDIATELY: the [ ] test below would otherwise overwrite",
      "# $? with its own status, and every failure was reported as code 0.",
      "RC=$?",
      "if [ $RC -ne 0 ]; then report failed $RC \"$FROM\" - \"$ERR\"; exit 0; fi",
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
      "  # stderr is kept OUT of the captured output. Merging them made any",
      "  # git failure look like uncommitted work, so the user was told they",
      "  # had local changes they did not have and the real error was lost.",
      "  $ERRF = Join-Path $STATE (\".err-\" + $PID)",
      "  $ST = (& $GIT -C $DIR status --porcelain=v1 --untracked-files=all 2>$ERRF | Out-String)",
      "  if ($LASTEXITCODE -ne 0) {",
      "    $e = ''",
      "    if (Test-Path -LiteralPath $ERRF) { $e = (Get-Content -Raw -LiteralPath $ERRF) }",
      "    Remove-Item -LiteralPath $ERRF -Force -ErrorAction SilentlyContinue",
      "    Report 'failed' $LASTEXITCODE '-' '-' $e; exit 0 }",
      "  Remove-Item -LiteralPath $ERRF -Force -ErrorAction SilentlyContinue",
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

   /*
    * Output is accumulated in onStandardOutputDataAvailable and read from
    * `stdout`, which is how every other ExternalProcess in Loom does it
    * (see Steps.syqonRun). `standardOutput` does not exist -- reading it
    * returned undefined, so `git --version` came back empty and a
    * perfectly good git was rejected as unusable.
    *
    * The wait pumps the event loop, or the callback never fires.
    */
   execute: function( program, args, deadlineMs )
   {
      var P = new ExternalProcess;
      var buf = "";
      P.onStandardOutputDataAvailable = function() { buf += String( P.stdout ); };
      P.start( program, args );

      var deadline = ( new Date() ).getTime() +
                     ( ( deadlineMs == null ) ? 5000 : deadlineMs );
      while ( P.isStarting || P.isRunning )
      {
         CoreApplication.processEvents();
         if ( ( new Date() ).getTime() > deadline )
         {
            try { P.terminate(); } catch ( e ) {}
            return { exitCode: -1, output: "" };
         }
      }
      return { exitCode: P.exitCode, output: buf };
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
/*
 * Takes the outcome record, by RENAME rather than read-then-delete, so
 * two readers cannot both claim the same one.
 */
Update.claimOutcome = function( io )
{
   io = io || Update.io;
   var path = Update.stateDir() + "/" + Update.OUTCOME_FILE;
   if ( !io.fileExists( path ) )
      return null;
   var claimed = path + ".reading";
   io.rename( path, claimed );
   var outcome = Update.parseOutcome( io.readText( claimed ) );
   io.remove( claimed );
   return outcome;
};

Update.reportLast = function( io )
{
   io = io || Update.io;
   try
   {
      var outcome = Update.claimOutcome( io );
      if ( outcome === null && !io.fileExists( Update.stateDir() + "/" +
                                               Update.OUTCOME_FILE ) )
         return null;
      if ( outcome == null )
      {
         Util.warn( "update", "the last update left an incomplete record; " +
                              "it may have been interrupted" );
         return null;
      }
      /*
       * "Nothing to do" is reported too. Silence on the happy path meant
       * a working updater and a broken one looked exactly alike, which is
       * precisely the complaint that prompted this.
       */
      if ( outcome.status == "unchanged" )
      {
         Util.log( "update", "last check: already up to date" );
         return outcome;
      }
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
 * What a copy that is not a git checkout is told, once, at each launch.
 * The release zip and PixInsight's update repository both install such a
 * copy, and PixInsight's own update mechanism is what keeps it current.
 */
Update.NOT_A_CHECKOUT_MESSAGE =
   "Loom updates itself only in a git checkout; this copy is updated " +
   "through PixInsight's update repository (Resources > Updates)";

/*
 * Works out what to run, writes the helper, and returns the command --
 * without running it. Returns null, having said why, when there is
 * nothing to do.
 *
 * Only a git checkout is ever updated here, and only by a fast-forward.
 * Anything else is left to PixInsight: nothing is looked for, written,
 * run or spawned for it, whatever the setting says -- the dialog does
 * not even offer the setting there.
 */
Update.prepareHelper = function( config, io )
{
   io = io || Update.io;
   try
   {
      var dir = Update.installDir();
      if ( !Update.isGitManaged( dir, io ) )
      {
         Util.log( "update", Update.NOT_A_CHECKOUT_MESSAGE );
         return null;
      }

      if ( !config || !config.autoUpdate )
      {
         Util.log( "update", "automatic updating is off" );
         return null;
      }

      var platform = io.platform();
      var git = Update.usableGit( io, platform );
      if ( git == null )
      {
         Util.warn( "update", "no usable git was found, so " + dir +
                              " cannot be updated" );
         return null;
      }

      var state = Update.stateDir();
      if ( !io.directoryExists( state ) )
         io.makeDirectory( state );
      var path = state + "/" + Update.helperFileName( platform );
      io.writeText( path, Update.gitScript( { git: git, dir: dir, stateDir: state,
                                              platform: platform } ) );
      var cmd = Update.helperCommand( platform, path );
      return { kind: "git", dir: dir, program: cmd.program, args: cmd.args };
   }
   catch ( e )
   {
      // An updater that cannot start is not a reason not to start Loom.
      Util.warn( "update", "the update check could not be started: " + e );
      return null;
   }
};

/*
 * How long the check may take before it is abandoned.
 *
 * It runs on the main thread now, so this is time the user waits at
 * startup. Long enough for a fetch against a LAN server with the VPN in
 * the way; short enough that an unreachable one is a pause rather than a
 * hang.
 */
Update.CHECK_DEADLINE_MS = 15000;

/*
 * Checks NOW, blocking, and reports the outcome to the console.
 *
 * The first design ran this detached and reported at the next launch,
 * which is correct in the narrow sense -- #include is resolved at parse
 * time, so an update can never apply to the run that fetched it -- and
 * useless in practice: "the result is reported at the next launch" is not
 * an answer to "is there a new version?". So the check is synchronous,
 * and a launch that finds one relaunches itself rather than running the
 * code it has just superseded.
 *
 * Returns the outcome record, or null if nothing was checked.
 */
Update.checkNow = function( config, io )
{
   io = io || Update.io;
   var cmd = Update.prepareHelper( config, io );
   if ( cmd == null )
      return null;

   Util.log( "update", "checking " + cmd.dir + " for a newer Loom..." );
   var r = io.execute( cmd.program, cmd.args, Update.CHECK_DEADLINE_MS );
   if ( r != null && r.exitCode == -1 )
   {
      Util.warn( "update", "the check did not finish within " +
                           Math.round( Update.CHECK_DEADLINE_MS / 1000 ) +
                           "s and was abandoned; continuing on this version" );
      return null;
   }

   var outcome = Update.claimOutcome( io );
   if ( outcome == null )
   {
      Util.warn( "update", "the check left no result; continuing on this version" );
      return null;
   }
   if ( Update.isFailure( outcome ) )
      Util.warn( "update", Update.outcomeMessage( outcome ) );
   else
      Util.log( "update", Update.outcomeMessage( outcome ) );
   return outcome;
};

/*
 * Re-runs Loom in this same PixInsight instance, so the newly updated
 * #includes are parsed afresh.
 *
 * The dispatch is the one PixInsight offers for handing a script to a
 * running instance; `instance` is the slot this one occupies and
 * `filePath` the executable. Detached, because the script that asks for
 * it is about to end.
 *
 * Returns false if it could not be started, so the caller can carry on
 * with the version in hand rather than leaving the user with nothing.
 */
Update.relaunch = function( io )
{
   io = io || Update.io;
   try
   {
      if ( !Update.SCRIPT_FILE )
         return false;
      io.spawnDetached( CoreApplication.filePath,
                        [ "-x=" + CoreApplication.instance + ":" +
                          Update.SCRIPT_FILE ] );
      return true;
   }
   catch ( e )
   {
      Util.warn( "update", "could not restart Loom automatically (" + e +
                           "); start it again to use the new version" );
      return false;
   }
};

/*
 * Loom's own root, from the folder its scripts sit in.
 *
 * A checkout (and the release zip) keeps Loom.js in <root>/script, so the
 * root is the parent of a folder named "script"; the update repository
 * installs Loom.js at the root itself. Only a .git exactly HERE makes a
 * checkout. Nothing walks further up: a Loom installed inside some other
 * repository -- dotfiles in the home folder, a versioned scripts folder --
 * would otherwise have THAT repository fetched and fast-forwarded, and its
 * commit shown as Loom's.
 */
Update.rootOf = function( scriptDir )
{
   var dir = String( scriptDir || "" ).replace( /\/+$/, "" );
   var slash = dir.lastIndexOf( "/" );
   if ( slash > 0 && dir.substring( slash + 1 ) == "script" )
      return dir.substring( 0, slash );
   return dir;
};

/* The installation root of the running script; see Update.rootOf. */
Update.installDir = function()
{
   return Update.rootOf( Update.SCRIPT_DIR );
};
