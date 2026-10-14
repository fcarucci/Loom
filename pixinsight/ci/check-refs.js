/*
 * Static reference and packaging gate.
 *
 * PJSR has no modules. A moved or renamed `Namespace.member` fails only
 * when the line that reads it RUNS, and the node suite never runs most of
 * UI.js, much of Steps.js, and none of FrameSelector.js, FlyThrough.js or
 * Loom.js. So "CI green, PixInsight broken" is one rename away. This reads
 * every script statically instead, with the same preprocessor the suite
 * uses, and fails on four things:
 *
 *   undefined-member  A read of NS.member where NS is a Loom namespace and
 *                     no live code defines member. Production reads must be
 *                     satisfied by production code: a stub the suite assigns
 *                     does not count.
 *   missing-include   An entry point (Loom.js, FrameSelector.js,
 *                     FlyThrough.js, selftest.js) whose include closure --
 *                     followed as PixInsight follows it, #ifndef
 *                     LOOM_LIBS_INCLUDED and all -- references a namespace
 *                     that no file in the closure defines.
 *   load-order        A statement that runs at load time (top level, or an
 *                     immediately invoked function) reading another file's
 *                     namespace before that file, in any include order that
 *                     holds both: each entry point's, and run-tests.js LIBS.
 *   packaging         Every script/lib/*.js is in run-tests.js LIBS, in
 *                     selftest.js's include block and in its LIB_FILES scan;
 *                     the entry points ci/repository.sh ships exist and are
 *                     all of them; both packaging scripts copy lib/ whole.
 *
 * Plus stale-allowlist: an allowlist or exception entry that no longer
 * matches anything must be removed, so the lists only ever shrink.
 *
 * Both cores are checked in every run (__PI_RELEASE__ 4 and 5), whatever
 * LOOM_TEST_CORE_RELEASE says: Loom compiles differently on each, and a
 * member defined in only one branch is exactly the bug this is for. Only
 * the macOS platform branch is read, as the suite reads it.
 *
 * Run by ci/run-tests.js before anything loads. Standalone:
 *   node ci/check-refs.js [repository root]
 * Its own tests: ci/check-refs.test.js.
 */

const fs = require( "fs" );
const path = require( "path" );
// acorn 8.18.0, vendored (MIT, see vendor/acorn.LICENSE): CI runs with no npm install.
const acorn = require( "./vendor/acorn.js" );
const { createPreprocessor } = require( "./preprocess.js" );

const ENTRY_POINTS = [ "Loom.js", "FrameSelector.js", "FlyThrough.js", "selftest.js" ];
const TEST_FILES = [ "selftest.js" ];
const CORES = [ "4", "5" ];

/*
 * Sites the gate cannot prove and a person has. Keyed by the file of the
 * reading site and the reference, not by line, so edits nearby do not
 * churn it. Every entry says why; an entry that matches nothing fails the
 * gate as stale-allowlist.
 */
const ALLOWLIST = [
];

/*
 * Libraries selftest.js's LIB_FILES scan (no library hardcodes the
 * PixInsight install path) may leave out. None: every library is scanned,
 * and a new one missing from the scan fails the gate.
 */
const LIB_FILES_EXCEPTIONS = [
];

// ---------------------------------------------------------------------------
// Parsing

const OBJECT_BUILTINS = [ "hasOwnProperty", "toString", "valueOf", "constructor",
                          "isPrototypeOf", "propertyIsEnumerable", "toLocaleString" ];
const FUNCTION_BUILTINS = [ "prototype", "name", "length", "call", "apply", "bind" ];

const astCache = new Map(), analysisCache = new Map();

function parse( file, text )
{
   let ast = astCache.get( text );
   if ( !ast )
   {
      try
      {
         ast = acorn.parse( text, { ecmaVersion: "latest", sourceType: "script", locations: true,
                                    allowReturnOutsideFunction: true, allowHashBang: true } );
      }
      catch ( e )
      {
         throw new Error( "check-refs: cannot parse " + file + ": " + e.message );
      }
      astCache.set( text, ast );
   }
   return ast;
}

function isFunction( n )
{
   return n.type === "FunctionDeclaration" || n.type === "FunctionExpression" ||
          n.type === "ArrowFunctionExpression";
}

function patternNames( p, into )
{
   if ( !p ) return;
   switch ( p.type )
   {
   case "Identifier": into.add( p.name ); break;
   case "AssignmentPattern": patternNames( p.left, into ); break;
   case "RestElement": patternNames( p.argument, into ); break;
   case "ArrayPattern": p.elements.forEach( e => patternNames( e, into ) ); break;
   case "ObjectPattern": p.properties.forEach( q => patternNames( q.type === "RestElement" ? q : q.value, into ) ); break;
   default: break;
   }
}

/* Every name a function binds locally: params, var/let/const, inner declarations, catch params. */
function localNames( fn )
{
   const names = new Set();
   fn.params.forEach( p => patternNames( p, names ) );
   if ( fn.type === "FunctionExpression" && fn.id ) names.add( fn.id.name );
   ( function scan( n )
   {
      if ( !n || typeof n.type !== "string" ) return;
      if ( n.type === "VariableDeclaration" ) n.declarations.forEach( d => patternNames( d.id, names ) );
      if ( ( n.type === "FunctionDeclaration" || n.type === "ClassDeclaration" ) && n.id ) names.add( n.id.name );
      if ( n.type === "CatchClause" ) patternNames( n.param, names );
      if ( isFunction( n ) ) return;   // its own scope
      for ( const k in n )
      {
         const v = n[k];
         if ( Array.isArray( v ) ) v.forEach( scan );
         else if ( v && typeof v.type === "string" ) scan( v );
      }
   } )( fn.body );
   return names;
}

/*
 * What one preprocessed file declares, defines and references.
 *   decls:  top-level names it declares, with the kind (object/function/class)
 *   defs:   [ns, member] assigned anywhere, or keys of a top-level object literal
 *   reads:  NS.member reads { ns, member, line }
 *   refs:   namespace identifier references { ns, line, loadTime }
 * Namespaces are resolved later; every capitalised global is a candidate.
 */
function analyse( file, text )
{
   const ast = parse( file, text );
   const decls = new Map(), defs = [], reads = [], refs = [];
   const isName = s => /^[A-Z]/.test( s );

   for ( const s of ast.body )
      if ( s.type === "VariableDeclaration" )
      {
         for ( const d of s.declarations )
         {
            if ( d.id.type !== "Identifier" ) continue;
            decls.set( d.id.name, "object" );
            if ( d.init && d.init.type === "ObjectExpression" )
               for ( const p of d.init.properties )
                  if ( p.type === "Property" && !p.computed )
                     defs.push( [ d.id.name, p.key.type === "Identifier" ? p.key.name : String( p.key.value ) ] );
         }
      }
      else if ( s.type === "FunctionDeclaration" && s.id )
         decls.set( s.id.name, "function" );
      else if ( s.type === "ClassDeclaration" && s.id )
      {
         decls.set( s.id.name, "class" );
         for ( const m of s.body.body )
            if ( m.static && !m.computed && m.key && ( m.key.type === "Identifier" || m.key.type === "Literal" ) )
               defs.push( [ s.id.name, m.key.name !== undefined ? m.key.name : String( m.key.value ) ] );
      }

   const shadowed = ( scopes, name ) => scopes.some( s => s.has( name ) );
   const nsMember = ( n, scopes ) =>
      n.type === "MemberExpression" && n.object.type === "Identifier" && isName( n.object.name ) &&
      !shadowed( scopes, n.object.name ) &&
      ( !n.computed ? n.property.type === "Identifier"
                    : n.property.type === "Literal" && typeof n.property.value === "string" );
   const memberName = n => n.computed ? n.property.value : n.property.name;

   function defineTarget( t, scopes )
   {
      if ( nsMember( t, scopes ) ) { defs.push( [ t.object.name, memberName( t ) ] ); return true; }
      return false;
   }

   function isIIFE( fn, anc )
   {
      const p = anc[anc.length - 1], g = anc[anc.length - 2];
      if ( p && ( p.type === "CallExpression" || p.type === "NewExpression" ) && p.callee === fn ) return true;
      return !!( p && p.type === "MemberExpression" && p.object === fn && !p.computed &&
                 /^(call|apply)$/.test( p.property.name ) && g && g.type === "CallExpression" && g.callee === p );
   }

   function walk( n, scopes, loadTime, anc )
   {
      if ( !n || typeof n.type !== "string" ) return;
      const down = ( c, lt ) =>
      {
         anc.push( n );
         walk( c, scopes, lt === undefined ? loadTime : lt, anc );
         anc.pop();
      };

      if ( isFunction( n ) )
      {
         const inner = scopes.concat( [ localNames( n ) ] );
         const lt = loadTime && isIIFE( n, anc );
         anc.push( n );
         n.params.forEach( p => walkPattern( p, inner, lt, anc ) );
         walk( n.body, inner, lt, anc );
         anc.pop();
         return;
      }
      switch ( n.type )
      {
      case "Identifier":
         if ( isName( n.name ) && !shadowed( scopes, n.name ) )
            refs.push( { ns: n.name, line: n.loc.start.line, loadTime } );
         return;
      case "MemberExpression":
         // NS["x"] and NS[k] are skipped: the gate proves names, not values.
         if ( !n.computed && nsMember( n, scopes ) )
            reads.push( { ns: n.object.name, member: memberName( n ), line: n.loc.start.line } );
         down( n.object );
         if ( n.computed ) down( n.property );
         return;
      case "AssignmentExpression":
         if ( defineTarget( n.left, scopes ) ) down( n.left.object );
         else if ( n.left.type === "MemberExpression" || n.left.type === "Identifier" ) down( n.left );
         else { anc.push( n ); walkPattern( n.left, scopes, loadTime, anc, true ); anc.pop(); }
         down( n.right );
         return;
      case "UpdateExpression":
         if ( defineTarget( n.argument, scopes ) ) down( n.argument.object );
         else down( n.argument );
         return;
      case "UnaryExpression":
         // typeof X / typeof X.y is a feature test and never throws; delete X.y is not a read.
         if ( n.operator === "typeof" && n.argument.type === "Identifier" ) return;
         if ( ( n.operator === "typeof" || n.operator === "delete" ) && nsMember( n.argument, scopes ) )
         {
            anc.push( n ); down( n.argument.object ); anc.pop();
            return;
         }
         down( n.argument );
         return;
      case "Property":
      case "MethodDefinition":
      case "PropertyDefinition":
         if ( n.computed ) down( n.key );
         if ( n.value ) down( n.value, n.type === "PropertyDefinition" && !n.static ? false : undefined );
         return;
      case "VariableDeclarator":
         anc.push( n ); walkPattern( n.id, scopes, loadTime, anc ); anc.pop();
         if ( n.init ) down( n.init );
         return;
      case "ClassDeclaration":
      case "ClassExpression":
         if ( n.superClass ) down( n.superClass );
         down( n.body );
         return;
      case "CatchClause":
      {
         const bound = new Set();
         patternNames( n.param, bound );
         anc.push( n ); walk( n.body, scopes.concat( [ bound ] ), loadTime, anc ); anc.pop();
         return;
      }
      case "LabeledStatement":
         down( n.body );
         return;
      case "BreakStatement":
      case "ContinueStatement":
         return;
      default:
         for ( const k in n )
         {
            if ( k === "loc" ) continue;
            const v = n[k];
            if ( Array.isArray( v ) ) v.forEach( c => down( c ) );
            else if ( v && typeof v.type === "string" ) down( v );
         }
      }
   }

   /* Bindings are not references, but their default values and member targets are code. */
   function walkPattern( p, scopes, loadTime, anc, assigning )
   {
      if ( !p ) return;
      switch ( p.type )
      {
      case "Identifier": return;
      case "AssignmentPattern":
         walkPattern( p.left, scopes, loadTime, anc, assigning );
         walk( p.right, scopes, loadTime, anc );
         return;
      case "RestElement": walkPattern( p.argument, scopes, loadTime, anc, assigning ); return;
      case "ArrayPattern": p.elements.forEach( e => walkPattern( e, scopes, loadTime, anc, assigning ) ); return;
      case "ObjectPattern":
         p.properties.forEach( q =>
         {
            if ( q.computed ) walk( q.key, scopes, loadTime, anc );
            walkPattern( q.type === "RestElement" ? q : q.value, scopes, loadTime, anc, assigning );
         } );
         return;
      case "MemberExpression":
         if ( assigning && defineTarget( p, scopes ) ) walk( p.object, scopes, loadTime, anc );
         else walk( p, scopes, loadTime, anc );
         return;
      default: walk( p, scopes, loadTime, anc );
      }
   }

   for ( const s of ast.body )
      walk( s, [], true, [ ast ] );
   return { decls, defs, reads, refs };
}

// ---------------------------------------------------------------------------
// Load contexts: what PixInsight (or the node suite) actually reads, in order

function substituteFile( file, text )
{
   return text.replace( /#__FILE__/g, JSON.stringify( "/script/" + file ) );
}

/*
 * An entry point as PixInsight loads it: preprocessed with one define
 * table, following each live #include "..." at the point it occurs. A
 * file's code runs once all its includes have, so the order is the order
 * in which files FINISH.
 */
function expandEntry( tree, entry, core, problems )
{
   const pp = createPreprocessor( { LOOM_TEST_CORE_RELEASE: core } );
   const order = [], seen = new Set();
   ( function visit( file, from )
   {
      if ( seen.has( file ) ) return;
      seen.add( file );
      if ( tree.files[file] === undefined )
      {
         problems.push( { rule: "packaging", file: from, line: null,
                          text: "includes " + file + ", which does not exist" } );
         return;
      }
      const text = pp.preprocess( tree.files[file], { onInclude: target =>
         visit( path.posix.normalize( path.posix.join( path.posix.dirname( file ), target ) ), file ) } );
      order.push( { file, text: substituteFile( file, text ) } );
   } )( entry, entry );
   return order;
}

/*
 * The node suite: LIBS in order with one define table, then any entry
 * points run-tests.js evaluates itself (Loom.js, under the defines it sets
 * first), then selftest.js. Includes are not followed, as run-tests.js
 * does not follow them.
 */
function expandNode( tree, core )
{
   const pp = createPreprocessor( { LOOM_TEST_CORE_RELEASE: core } );
   const order = [];
   const push = file => { if ( tree.files[file] !== undefined )
      order.push( { file, text: substituteFile( file, pp.preprocess( tree.files[file] ) ) } ); };
   tree.runTestsLibs.forEach( push );
   const extra = tree.runTestsExtra || { files: [], defines: [] };
   extra.defines.forEach( d => { pp.defines[d] = "1"; } );
   extra.files.forEach( push );
   tree.testFiles.forEach( push );
   return order;
}

// ---------------------------------------------------------------------------
// The check

function coreName( core ) { return "1.9." + core; }

function check( tree )
{
   const allowlist = tree.allowlist || [];
   const allowUsed = new Set();
   const found = new Map();     // rule|file|line|text -> { v, cores }
   const allowedSites = new Set();
   const stats = { files: 0, reads: 0 };
   const isTest = f => tree.testFiles.includes( f );

   function report( v, core )
   {
      const key = v.rule + "|" + v.file + "|" + v.line + "|" + v.text;
      if ( !found.has( key ) ) found.set( key, { v, cores: new Set() } );
      if ( core ) found.get( key ).cores.add( core );
   }

   function allowedBy( rule, file, ref, entry, siteKey )
   {
      const i = allowlist.findIndex( a => a.rule === rule && a.file === file && a.ref === ref &&
                                          ( !a.entry || a.entry === entry ) );
      if ( i < 0 ) return false;
      allowUsed.add( i );
      allowedSites.add( siteKey );
      return true;
   }

   const packagingProblems = [];
   const allFiles = new Set();
   for ( const core of CORES )
   {
      const contexts = tree.entryPoints.filter( e => tree.files[e] !== undefined ).map( e =>
         ( { label: e + "'s include order", entry: e, order: expandEntry( tree, e, core, packagingProblems ) } ) );
      contexts.push( { label: "the ci/run-tests.js LIBS order", entry: null, order: expandNode( tree, core ) } );

      // Every distinct preprocessed text of every file, analysed once.
      const variants = new Map();
      for ( const c of contexts )
         for ( const o of c.order )
         {
            const k = o.file + "\0" + o.text;
            if ( !variants.has( k ) )
            {
               // Memoised across calls: the gate's tests and the gate read the same tree.
               if ( !analysisCache.has( k ) ) analysisCache.set( k, analyse( o.file, o.text ) );
               variants.set( k, { file: o.file, info: analysisCache.get( k ) } );
            }
            o.info = variants.get( k ).info;
            allFiles.add( o.file );
         }

      // Namespaces: capitalised top-level declarations of production files.
      const nsKind = new Map();
      for ( const { file, info } of variants.values() )
         if ( !isTest( file ) )
            for ( const [ name, kind ] of info.decls )
               if ( /^[A-Z]/.test( name ) ) nsKind.set( name, kind );

      // Rule 1: defined members.
      const prodDefs = new Map(), allDefs = new Map();
      const add = ( m, ns, member ) => { if ( !m.has( ns ) ) m.set( ns, new Set() ); m.get( ns ).add( member ); };
      for ( const [ ns, kind ] of nsKind )
         for ( const b of OBJECT_BUILTINS.concat( kind === "object" ? [] : FUNCTION_BUILTINS ) )
         {
            add( prodDefs, ns, b );
            add( allDefs, ns, b );
         }
      for ( const { file, info } of variants.values() )
         for ( const [ ns, member ] of info.defs )
         {
            add( allDefs, ns, member );
            if ( !isTest( file ) ) add( prodDefs, ns, member );
         }
      const seenReads = new Set();
      for ( const { file, info } of variants.values() )
         for ( const r of info.reads )
         {
            if ( !nsKind.has( r.ns ) ) continue;
            const ref = r.ns + "." + r.member;
            const siteKey = "m|" + file + "|" + r.line + "|" + ref;
            if ( seenReads.has( siteKey ) ) continue;
            seenReads.add( siteKey );
            if ( core === CORES[CORES.length - 1] ) ++stats.reads;
            if ( ( isTest( file ) ? allDefs : prodDefs ).get( r.ns ).has( r.member ) ) continue;
            if ( allowedBy( "undefined-member", file, ref, null, siteKey ) ) continue;
            report( { rule: "undefined-member", file, line: r.line,
                      text: ref + " is read but " + r.member + " is defined nowhere" +
                            ( isTest( file ) ? "" : " in production code" ) }, core );
         }

      // Rule 2: each entry point's closure defines every namespace it references.
      for ( const c of contexts )
      {
         if ( !c.entry ) continue;
         const defined = new Set();
         for ( const o of c.order ) for ( const name of o.info.decls.keys() ) defined.add( name );
         const firstSite = new Map();
         for ( const o of c.order )
            for ( const r of o.info.refs )
            {
               if ( !nsKind.has( r.ns ) || defined.has( r.ns ) ) continue;
               const siteKey = "i|" + c.entry + "|" + o.file + "|" + r.line + "|" + r.ns;
               if ( allowedBy( "missing-include", o.file, r.ns, c.entry, siteKey ) ) continue;
               if ( !firstSite.has( r.ns ) ) firstSite.set( r.ns, o.file + ":" + r.line );
            }
         for ( const [ ns, site ] of firstSite )
            report( { rule: "missing-include", file: c.entry, line: lastIncludeLine( tree.files[c.entry] ),
                      text: ns + " is referenced (first at " + site + ") but no file " + c.entry +
                            " includes defines it" }, core );
      }

      // Rule 3: load-time reads come after the file that defines the namespace.
      for ( const c of contexts )
      {
         const definer = new Map();
         c.order.forEach( ( o, i ) =>
         {
            for ( const name of o.info.decls.keys() )
               if ( !definer.has( name ) ) definer.set( name, { i, file: o.file } );
         } );
         c.order.forEach( ( o, i ) =>
         {
            const done = new Set();
            for ( const r of o.info.refs )
            {
               if ( !r.loadTime || !nsKind.has( r.ns ) || o.info.decls.has( r.ns ) ) continue;
               const d = definer.get( r.ns );
               if ( !d || d.i < i || done.has( r.line + "|" + r.ns ) ) continue;
               done.add( r.line + "|" + r.ns );
               report( { rule: "load-order", file: o.file, line: r.line,
                         text: r.ns + " is read at load time before " + d.file + " defines it, in " +
                               c.label }, core );
            }
         } );
      }
   }
   stats.files = allFiles.size;

   // Rule 4: packaging lists agree.
   packagingProblems.forEach( p => report( p ) );
   packaging( tree, report );

   allowlist.forEach( ( a, i ) =>
   {
      if ( !allowUsed.has( i ) )
         report( { rule: "stale-allowlist", file: a.file, line: null,
                   text: "allowlist entry " + a.rule + " " + a.ref + " matches nothing: remove it" } );
   } );

   const violations = [];
   for ( const { v, cores } of found.values() )
   {
      const only = cores.size && cores.size < CORES.length
         ? " [" + [ ...cores ].map( coreName ).join( ", " ) + " only]" : "";
      violations.push( Object.assign( {}, v, { text: v.text + only } ) );
   }
   violations.sort( ( a, b ) => a.rule.localeCompare( b.rule ) || a.file.localeCompare( b.file ) ||
                                ( a.line || 0 ) - ( b.line || 0 ) );
   return { violations, allowed: allowedSites.size, stats };
}

function lastIncludeLine( text )
{
   let at = null;
   text.split( "\n" ).forEach( ( l, i ) => { if ( /^\s*#\s*include\s+"/.test( l ) ) at = i + 1; } );
   return at;
}

function lineOf( text, re )
{
   const i = text.split( "\n" ).findIndex( l => re.test( l ) );
   return i < 0 ? null : i + 1;
}

function packaging( tree, report )
{
   const libs = Object.keys( tree.files ).filter( f => /^lib\/[^/]+\.js$/.test( f ) ).sort();
   const top = Object.keys( tree.files ).filter( f => !f.includes( "/" ) );
   const suite = tree.testFiles[0];
   const suiteText = tree.files[suite] || "";
   const libsLine = tree.runTestsLibsLine || null;

   for ( const lib of libs )
      if ( !tree.runTestsLibs.includes( lib ) )
         report( { rule: "packaging", file: "ci/run-tests.js", line: libsLine,
                   text: lib + " is not in LIBS, so the node suite never loads it" } );
   for ( const lib of tree.runTestsLibs )
      if ( !libs.includes( lib ) )
         report( { rule: "packaging", file: "ci/run-tests.js", line: libsLine,
                   text: "LIBS names " + lib + ", which does not exist" } );

   const included = new Set();
   suiteText.split( "\n" ).forEach( l =>
   {
      const m = /^\s*#\s*include\s+"(lib\/[^"]+)"/.exec( l );
      if ( m ) included.add( m[1] );
   } );
   for ( const lib of libs )
      if ( !included.has( lib ) )
         report( { rule: "packaging", file: suite, line: lastIncludeLine( suiteText ),
                   text: lib + " is not #included by " + suite } );

   const m = /LIB_FILES\s*=\s*\[([^\]]*)\]/.exec( suiteText );
   const scanned = m ? ( m[1].match( /"[^"]+"/g ) || [] ).map( s => s.slice( 1, -1 ) ) : [];
   const scanLine = lineOf( suiteText, /LIB_FILES\s*=/ );
   const exceptions = tree.libFilesExceptions || [];
   if ( !m )
      report( { rule: "packaging", file: suite, line: null, text: "no LIB_FILES list found" } );
   for ( const lib of libs )
   {
      const base = lib.slice( 4 );
      if ( !scanned.includes( base ) && !exceptions.includes( base ) )
         report( { rule: "packaging", file: suite, line: scanLine,
                   text: base + " is missing from LIB_FILES, the hardcoded-install-path scan" } );
   }
   for ( const e of exceptions )
      if ( scanned.includes( e ) || !libs.includes( "lib/" + e ) )
         report( { rule: "stale-allowlist", file: suite, line: scanLine,
                   text: "LIB_FILES exception " + e + " is no longer needed: remove it" } );

   const shipped = tree.repositoryEntries || [];
   const shipLine = tree.repositoryEntriesLine || null;
   for ( const e of shipped )
      if ( tree.files[e] === undefined )
         report( { rule: "packaging", file: "ci/repository.sh", line: shipLine,
                   text: "ships script/" + e + ", which does not exist" } );
   for ( const e of tree.entryPoints )
   {
      if ( tree.files[e] === undefined )
         report( { rule: "packaging", file: "ci/check-refs.js", line: null,
                   text: "the entry point " + e + " does not exist" } );
      else if ( !tree.testFiles.includes( e ) && !shipped.includes( e ) )
         report( { rule: "packaging", file: "ci/repository.sh", line: shipLine,
                   text: "does not ship the entry point " + e } );
   }
   for ( const f of top )
      if ( !tree.entryPoints.includes( f ) )
         report( { rule: "packaging", file: "script/" + f, line: null,
                   text: "is not an entry point this gate knows: add it to ENTRY_POINTS in ci/check-refs.js" } );
   if ( !tree.repositoryCopiesLib )
      report( { rule: "packaging", file: "ci/repository.sh", line: null,
                text: "no longer copies script/lib whole (cp -R script/lib)" } );
   if ( !tree.packageCopiesScript )
      report( { rule: "packaging", file: "ci/package.sh", line: null,
                text: "no longer copies script/ whole (cp -R script)" } );
}

// ---------------------------------------------------------------------------
// The real tree

function readTree( root )
{
   const scriptDir = path.join( root, "script" );
   const files = {};
   ( function scan( dir, rel )
   {
      for ( const e of fs.readdirSync( dir, { withFileTypes: true } ) )
      {
         if ( e.name.startsWith( "." ) ) continue;
         const r = rel ? rel + "/" + e.name : e.name;
         if ( e.isDirectory() ) scan( path.join( dir, e.name ), r );
         else if ( e.name.endsWith( ".js" ) ) files[r] = fs.readFileSync( path.join( dir, e.name ), "utf8" );
      }
   } )( scriptDir, "" );

   const runTests = fs.readFileSync( path.join( root, "ci", "run-tests.js" ), "utf8" );
   const libsDecl = /const LIBS\s*=\s*\[([^\]]*)\]/.exec( runTests );
   const repository = fs.readFileSync( path.join( root, "ci", "repository.sh" ), "utf8" );
   const packageSh = fs.readFileSync( path.join( root, "ci", "package.sh" ), "utf8" );
   const entryCopy = repository.split( "\n" ).find( l => /^\s*cp\s+(script\/[^\s/]+\.js\s+)+/.test( l ) ) || "";

   return {
      files,
      entryPoints: ENTRY_POINTS,
      testFiles: TEST_FILES,
      runTestsLibs: libsDecl ? ( libsDecl[1].match( /"[^"]+"/g ) || [] ).map( s => s.slice( 1, -1 ) ) : [],
      runTestsLibsLine: lineOf( runTests, /const LIBS\s*=/ ),
      // Entry points run-tests.js evaluates after LIBS, and the defines it sets for them.
      runTestsExtra: {
         files: ( runTests.match( /\bload\( "[^"\/]+\.js" \)/g ) || [] ).map( s => s.slice( 7, -3 ) )
                   .filter( f => f !== TEST_FILES[0] ),
         defines: ( runTests.match( /^defines\.\w+\s*=/gm ) || [] ).map( s => s.slice( 8 ).replace( /\s*=$/, "" ) )
      },
      repositoryEntries: ( entryCopy.match( /script\/[^\s/]+\.js/g ) || [] ).map( s => s.slice( 7 ) ),
      repositoryEntriesLine: lineOf( repository, /^\s*cp\s+script\/[^\s/]+\.js/ ),
      repositoryCopiesLib: /^\s*cp -R script\/lib\s/m.test( repository ),
      packageCopiesScript: /^\s*cp -R script\s/m.test( packageSh ),
      allowlist: ALLOWLIST,
      libFilesExceptions: LIB_FILES_EXCEPTIONS
   };
}

function format( result )
{
   const lines = result.violations.map( v =>
      "check-refs: " + v.rule + ": " + v.file + ( v.line != null ? ":" + v.line : "" ) + " " + v.text );
   const n = result.violations.length;
   lines.push( "check-refs: " + ( n ? n + " violation" + ( n === 1 ? "" : "s" ) : "OK, 0 violations" ) +
               "; " + result.stats.files + " files, " + result.stats.reads + " member reads, " +
               result.allowed + " allowlisted sites, cores " + CORES.map( coreName ).join( " and " ) );
   return lines;
}

function main( root )
{
   const result = check( readTree( root ) );
   const lines = format( result );
   if ( result.violations.length )
   {
      lines.forEach( l => console.error( l ) );
      return 1;
   }
   console.error( lines[lines.length - 1] );
   return 0;
}

module.exports = { check, format, readTree, main, ALLOWLIST, LIB_FILES_EXCEPTIONS };

if ( require.main === module )
   process.exit( main( path.resolve( process.argv[2] || path.join( __dirname, ".." ) ) ) );
