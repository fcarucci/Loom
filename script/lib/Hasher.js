/*
 * Whole-file SHA-1 through the system's own hasher, with PixInsight's
 * CryptographicHash behind it.
 *
 * The Frame Selector digests every frame whole, twice: before measuring and
 * again after. Through PJSR that is 175-700 ms a file, one at a time, on the
 * script's thread -- 85 s of "Reading frames" for a night of 126 subs.
 * /usr/bin/shasum, eight at a time, is 12-19 ms a file, and the hex is the
 * same, so the measurement cache and every manifest keep working.
 *
 * The digest authorises deleting someone's data, so the external tool is
 * trusted only as far as it proves itself:
 *
 *  - the first batch of a scan runs alone, and its first file is digested
 *    by PixInsight too; nothing else starts until the two agree;
 *  - output is read strictly: a line that is not exactly the expected
 *    shape, a hex that is not 40 lower-case digits, a line that names
 *    another file or an index that is unknown or repeated, or the wrong
 *    number of lines, and the batch is not believed at all;
 *  - any failure -- the tool will not start, exits non-zero, takes too
 *    long, says something odd, or disagrees with PixInsight -- turns it
 *    off for the rest of the scan, and PixInsight digests what is left.
 *
 * macOS and Windows only; Loom does not support Linux yet, so everything
 * else simply goes through PixInsight, as before.
 */
function Hasher() {}

Hasher.SHASUM = "/usr/bin/shasum";

/*
 * Frames per process and processes at once. The per-process cost is the
 * launch (small for shasum, a few hundred ms for powershell.exe, hence the
 * larger Windows batch); eight processes keep an SSD busy without
 * starving PixInsight of cores.
 */
Hasher.JOBS = 8;

Hasher.batchSize = function( platform )
{
   return Util.isWindows( platform ) ? 16 : 8;
};

/*
 * A batch is 8-16 frames of 50-100 MB each. Ten minutes is far past what
 * even a slow network share needs, and only there so a process that never
 * ends cannot hold the scan for ever; Cancel works throughout anyway.
 */
Hasher.TIMEOUT_MS = 10*60*1000;

/* Between polls of running processes: a spin would take a whole core from them. */
Hasher.PAUSE_MS = 5;

Hasher.pause = function()
{
   Util.sleep( Hasher.PAUSE_MS );
};

Hasher.supported = function( platform )
{
   var p = Util.platform( platform );
   return p == Util.PLATFORM_MACOS || p == Util.PLATFORM_WINDOWS;
};

/*
 * A path the external tool cannot be trusted to hand back unchanged.
 *
 * A control character would break the one-path-per-line output (and the
 * Windows list). On macOS shasum escapes a name containing a backslash
 * and marks the line with a leading "\", so the name it prints is not the
 * name asked for. A relative path depends on a working directory nobody
 * set. All of these are rare, and PixInsight digests them instead.
 */
Hasher.awkward = function( path, platform )
{
   var p = String( path );
   if ( /[\x00-\x1f]/.test( p ) )
      return true;
   if ( Util.isWindows( platform ) )
      return !/^([A-Za-z]:[\/\\]|[\/\\]{2}[^\/\\])/.test( p );
   return p.charAt( 0 ) != "/" || p.indexOf( "\\" ) >= 0;
};

/* Paths cut into external batches, and those PixInsight must digest itself. */
Hasher.batches = function( paths, platform )
{
   var out = { external: [], pjsr: [] }, open = [], size = Hasher.batchSize( platform );
   var usable = Hasher.supported( platform );
   for ( var i = 0; i < paths.length; ++i )
      ( usable && !Hasher.awkward( paths[i], platform ) ? open : out.pjsr ).push( paths[i] );
   for ( var j = 0; j < open.length; j += size )
      out.external.push( open.slice( j, j + size ) );
   return out;
};

/*
 * The Windows hasher. Pure ASCII on purpose: PowerShell 5.1 reads a .ps1
 * without a BOM as the ANSI code page, so a single non-ASCII character
 * here would depend on the machine's locale. The paths never appear in it:
 * they come from the list file, read as UTF-8, and each answer is printed
 * against its INDEX in that list, so the output is ASCII whatever the
 * names are.
 */
Hasher.POWERSHELL = [
   "param([string]$List)",
   "$ErrorActionPreference = 'Stop'",
   "$i = 0",
   "foreach ($p in @(Get-Content -LiteralPath $List -Encoding UTF8)) {",
   "   try {",
   "      $h = (Get-FileHash -LiteralPath $p -Algorithm SHA1).Hash",
   "      if (-not $h) { throw 'no hash' }",
   "      Write-Output ('' + $i + ' ' + $h)",
   "   } catch {",
   "      Write-Output ('' + $i + ' ERR')",
   "   }",
   "   $i++",
   "}",
   "" ].join( "\r\n" );

/*
 * How one batch is run: { program, args } and, on Windows, `list`, the
 * text the runner writes to files.list. No shell anywhere, so no name can
 * become syntax; "--" keeps a name that starts with "-" a name.
 */
Hasher.command = function( batch, platform, files )
{
   var p = Util.platform( platform );
   if ( p == Util.PLATFORM_MACOS )
      return { program: Hasher.SHASUM, args: [ "-a", "1", "--" ].concat( batch ) };
   if ( p != Util.PLATFORM_WINDOWS )
      return null;
   var c = Util.powerShellFile( files.script, [ files.list ] );
   c.list = batch.join( "\n" );
   return c;
};

/*
 * A name with its non-ASCII parts blurred, so the same name compares equal
 * whether it is composed or decomposed. shasum prints a macOS name
 * decomposed ("u" and a combining diaeresis) however it was asked, and
 * PixInsight's JavaScript has no String.normalize that works to compose
 * it back. A letter with its combining marks, and any run of non-ASCII,
 * each become one "?".
 */
Hasher.nameShape = function( name )
{
   return String( name ).replace( /[\x00-\x7f]?[\u0300-\u036f]+/g, "\ufffd" )
                        .replace( /[^\x00-\x7f]+/g, "?" );
};

/*
 * Line `i` of shasum's output: "<40 lower-case hex><two spaces><path i>".
 * shasum answers its arguments strictly in order, so the line belongs to
 * batch[i]; the name has to agree, blurred as above where it is not ASCII.
 */
Hasher.macLine = function( line, batch, i )
{
   var m = /^([0-9a-f]{40})  (.*)$/.exec( line );
   if ( m == null || Hasher.nameShape( m[2] ) != Hasher.nameShape( batch[i] ) )
      return null;
   return { index: i, hex: m[1] };
};

/* One Windows line: "<index> <40 hex>" or "<index> ERR" for a file it could not read. */
Hasher.windowsLine = function( line, batch )
{
   var m = /^(0|[1-9][0-9]*) ([0-9A-Fa-f]{40}|ERR)$/.exec( line );
   if ( m == null || Number( m[1] ) >= batch.length )
      return null;
   return { index: Number( m[1] ), hex: m[2] == "ERR" ? null : m[2].toLowerCase() };
};

/*
 * { path: hex } for a batch, or null when anything about the output is
 * odd -- then none of it is believed. Case is folded on Windows only:
 * Get-FileHash prints upper case, and shasum never does, so upper case
 * from shasum is not shasum talking.
 */
Hasher.parse = function( stdout, batch, platform )
{
   var lines = String( stdout == null ? "" : stdout ).split( /\r?\n/ );
   if ( lines[lines.length - 1] == "" )
      lines.pop();
   if ( lines.length != batch.length )
      return null;
   var read = Util.isWindows( platform ) ? Hasher.windowsLine : Hasher.macLine;
   var out = {}, seen = {};
   for ( var i = 0; i < lines.length; ++i )
   {
      var r = read( lines[i], batch, i );
      if ( r == null || seen[r.index] )
         return null;
      seen[r.index] = true;
      if ( r.hex != null )
         out[batch[r.index]] = r.hex;
   }
   return out;
};

/* ------------------------------------------------------------------ */
/* The runner                                                          */
/* ------------------------------------------------------------------ */

/*
 * One per scan. o.fallback( path ) is PixInsight's own digest (hex, or null
 * for a file it cannot read); o.Process is ExternalProcess unless a test
 * supplies a stand-in. The self-check and the switch-off are per scan, so
 * every job the scan starts shares them.
 */
Hasher.forScan = function( o )
{
   return new Hasher.Scan( o || {} );
};

Hasher.Scan = function( o )
{
   this.platform = Util.platform( o.platform );
   this.fallback = o.fallback;
   this.Process = o.Process || ExternalProcess;
   this.jobs = o.jobs || Hasher.JOBS;
   this.disabled = !Hasher.supported( this.platform );
   this.verified = false;
};

Hasher.Scan.prototype.start = function( paths )
{
   return new Hasher.Job( this, paths );
};

Hasher.Scan.prototype.disable = function( reason )
{
   if ( this.disabled )
      return;
   this.disabled = true;
   Util.warn( "frames", "The system SHA-1 tool is off for this scan (" + reason +
                        "); PixInsight reads the files itself." );
};

/*
 * A set of paths being digested. Non-blocking until wait(): add() queues,
 * poll() moves things along, so the digests are under way while the
 * caller is still reading headers.
 */
Hasher.Job = function( scan, paths )
{
   this.scan = scan;
   this.pending = [];      // added, not yet cut into batches
   this.queue = [];        // external batches not started
   this.running = [];
   this.pjsr = [];         // for PixInsight
   this.digests = {};
   this.asked = {};
   this.temp = [];
   this.script = null;
   this.stamp = Date.now() + "-" + Math.floor( Math.random()*1e6 );
   this.seq = 0;
   ( paths || [] ).forEach( function( p ) { this.add( p ); }, this );
};

Hasher.Job.prototype.add = function( path )
{
   if ( this.asked[path] )
      return;
   this.asked[path] = true;
   this.pending.push( path );
   if ( this.pending.length >= Hasher.batchSize( this.scan.platform ) )
      this.cut();
};

Hasher.Job.prototype.cut = function()
{
   var b = this.scan.disabled ? { external: [], pjsr: this.pending }
                              : Hasher.batches( this.pending, this.scan.platform );
   this.queue = this.queue.concat( b.external );
   this.pjsr = this.pjsr.concat( b.pjsr );
   this.pending = [];
};

/*
 * True once every path has an answer. A partial batch is cut only when
 * nothing is running: held back otherwise, it fills up and costs one
 * launch instead of several.
 */
Hasher.Job.prototype.poll = function()
{
   this.collect();
   if ( this.scan.disabled )
      this.abandon();
   if ( this.running.length == 0 && this.queue.length == 0 )
      this.cut();
   this.launch();
   return this.pending.length + this.queue.length + this.running.length + this.pjsr.length == 0;
};

/* Until the self-check has passed, one process; after it, up to scan.jobs. */
Hasher.Job.prototype.launch = function()
{
   var limit = this.scan.verified ? this.scan.jobs : 1;
   while ( !this.scan.disabled && this.running.length < limit && this.queue.length > 0 )
      this.running.push( this.spawn( this.queue.shift() ) );
};

Hasher.Job.prototype.tempName = function( extension )
{
   return File.systemTempDirectory + "/loom-sha1-" + this.stamp + "-" + ( ++this.seq ) + "." + extension;
};

/* The Windows script (once per job) and this batch's list, both on disk. */
Hasher.Job.prototype.writeFiles = function( cmd, files )
{
   if ( !File.exists( files.script ) )
   {
      this.temp.push( files.script );
      File.writeTextFile( files.script, Hasher.POWERSHELL );
   }
   this.temp.push( files.list );
   File.writeTextFile( files.list, cmd.list );
};

Hasher.Job.prototype.spawn = function( batch )
{
   var run = { batch: batch, P: null, started: false, errored: false, timedOut: false,
               deadline: Date.now() + Hasher.TIMEOUT_MS, program: "the SHA-1 tool", list: null };
   try
   {
      var files = null;
      if ( Util.isWindows( this.scan.platform ) )
      {
         this.script = this.script || this.tempName( "ps1" );
         files = { script: this.script, list: this.tempName( "txt" ) };
         run.list = files.list;
      }
      var cmd = Hasher.command( batch, this.scan.platform, files );
      run.program = cmd.program;
      if ( files != null )
         this.writeFiles( cmd, files );
      run.P = new this.scan.Process;
      run.P.onError = function() { run.errored = true; };
      run.started = run.P.start( cmd.program, cmd.args ) !== false;
   }
   catch ( e ) { run.started = false; }
   return run;
};

Hasher.Job.prototype.finished = function( run )
{
   if ( !run.started || !( run.P.isStarting || run.P.isRunning ) )
      return true;
   if ( Date.now() <= run.deadline )
      return false;
   run.timedOut = true;
   try { run.P.terminate(); } catch ( e ) {}
   return true;
};

Hasher.Job.prototype.collect = function()
{
   var still = [];
   for ( var i = 0; i < this.running.length; ++i )
      if ( this.finished( this.running[i] ) )
         this.settle( this.running[i] );
      else
         still.push( this.running[i] );
   this.running = still;
};

/*
 * A process's output as text. PixInsight hands it over as a ByteArray,
 * and String() of that is Latin-1: every non-ASCII name came back mangled,
 * which read as odd output and switched the tool off for the scan.
 */
Hasher.text = function( out )
{
   if ( out == null )
      return "";
   return typeof out.utf8ToString == "function" ? out.utf8ToString() : String( out );
};

/* Why a finished run cannot be believed, or null; a believable one gets run.map. */
Hasher.Job.prototype.failure = function( run )
{
   if ( !run.started || run.errored )
      return run.program + " could not be run";
   if ( run.timedOut )
      return run.program + " took too long";
   var code = null, out = "";
   try { code = run.P.exitCode; out = Hasher.text( run.P.stdout ); } catch ( e ) {}
   if ( code !== 0 )
      return run.program + " exited with code " + code;
   run.map = Hasher.parse( out, run.batch, this.scan.platform );
   return run.map == null ? "unexpected output from " + run.program : null;
};

/*
 * The first batch of the scan: its first file digested by PixInsight too.
 * That digest is the file's answer either way; it is the one the check
 * trusts.
 */
Hasher.Job.prototype.selfCheck = function( run )
{
   var first = run.batch[0], mine = this.scan.fallback( first );
   if ( mine != null )
      this.digests[first] = mine;
   if ( mine == null || mine !== run.map[first] )
      return run.program + " and PixInsight disagree on " + first;
   this.scan.verified = true;
   return null;
};

Hasher.Job.prototype.settle = function( run )
{
   this.dropTemp( run.list );
   var failure = this.failure( run );
   if ( failure == null && !this.scan.verified )
      failure = this.selfCheck( run );
   if ( failure != null )
      this.scan.disable( failure );
   for ( var i = 0; i < run.batch.length; ++i )
   {
      var p = run.batch[i];
      if ( this.digests[p] !== undefined )
         continue;
      if ( failure == null && run.map[p] !== undefined )
         this.digests[p] = run.map[p];
      else
         this.pjsr.push( p );
   }
};

/* Everything not yet answered goes to PixInsight; running processes are stopped. */
Hasher.Job.prototype.abandon = function()
{
   for ( var i = 0; i < this.running.length; ++i )
   {
      this.stop( this.running[i] );
      this.pjsr = this.pjsr.concat( this.running[i].batch );
   }
   for ( var j = 0; j < this.queue.length; ++j )
      this.pjsr = this.pjsr.concat( this.queue[j] );
   this.running = [];
   this.queue = [];
};

Hasher.Job.prototype.stop = function( run )
{
   if ( run.started )
      try { run.P.terminate(); } catch ( e ) {}
   this.dropTemp( run.list );
};

/* One file through PixInsight, if one is waiting; false when none was. */
Hasher.Job.prototype.digestOne = function()
{
   if ( this.pjsr.length == 0 )
      return false;
   var p = this.pjsr.shift(), d = this.scan.fallback( p );
   if ( d != null )
      this.digests[p] = d;
   return true;
};

/*
 * Waits for every answer. Events are pumped and `cancelled` asked on every
 * pass, so the scan window stays live and Cancel stops the processes, not
 * just the wait; PixInsight's own digests are taken one per pass, in
 * between.
 */
Hasher.Job.prototype.wait = function( cancelled )
{
   var stop = cancelled || function() { return false; };
   try
   {
      this.cut();
      while ( !this.poll() )
      {
         CoreApplication.processEvents();
         if ( stop() )
         {
            this.kill();
            return { digests: this.digests, cancelled: true };
         }
         if ( !this.digestOne() )
            Hasher.pause();
      }
      return { digests: this.digests, cancelled: false };
   }
   finally { this.cleanup(); }
};

Hasher.Job.prototype.kill = function()
{
   for ( var i = 0; i < this.running.length; ++i )
      this.stop( this.running[i] );
   this.running = [];
   this.queue = [];
   this.pending = [];
   this.pjsr = [];
   this.cleanup();
};

Hasher.Job.prototype.dropTemp = function( path )
{
   if ( path == null )
      return;
   try { if ( File.exists( path ) ) File.remove( path ); } catch ( e ) {}
   this.temp = this.temp.filter( function( t ) { return t != path; } );
};

Hasher.Job.prototype.cleanup = function()
{
   this.temp.slice().forEach( this.dropTemp, this );
};
