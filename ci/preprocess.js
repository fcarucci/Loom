/*
 * PJSR's preprocessor, as ci/run-tests.js and ci/check-refs.js both need it.
 *
 * One definition shared by the suite and the reference gate, so the gate
 * reads exactly the code the suite runs: the same live branches, the same
 * substituted defines, the same line numbers.
 *
 * createPreprocessor( env ) returns { defines, preprocess }. Each instance
 * has its OWN define table, shared by every file it preprocesses -- as one
 * PixInsight script run shares one table across its includes.
 */

function createPreprocessor( env )
{
   /*
    * A small stand-in for PJSR's preprocessor.
    *
    * Stripping the directives is not enough: Util.js selects its platform
    * with #ifeq on __PI_PLATFORM__ and then uses the macro it defines, so a
    * stripped file does not even load. This handles #define, #ifdef,
    * #ifndef, #ifeq, #else and #endif, and substitutes defined tokens.
    *
    * The define table is shared across files, exactly as PixInsight's is,
    * because that sharing is not a detail -- Steps.js must `#define VERSION`
    * for the bundled ImageSolver, and that define silently rewrote a
    * property access in a different file, which cost hours to find. A
    * faithful stand-in can catch that class of bug here, for free, on every
    * push. A stripping one cannot.
    *
    * Directive lines are replaced with comments rather than removed, so
    * every line number matches the real file and a stack trace points at it.
    */
   /*
    * The preprocessor symbols a real core defines.
    *
    * __PI_RELEASE__ matters as much as the platform now that Loom compiles
    * differently on 1.9.4 and 1.9.5: without it, every #ifoneof on the
    * release takes the 1.9.4 branch under node while PixInsight takes the
    * other one, and the suite would be testing a build nobody runs.
    *
    * Set to the version Loom is developed against. The 1.9.4 branch is
    * exercised by LOOM_TEST_CORE_RELEASE=4 rather than by leaving this unset.
    */
   const defines = { __PI_PLATFORM__: "MACOSX",
                     __PI_MAJOR__: env.LOOM_TEST_CORE_MAJOR || "1",
                     __PI_MINOR__: env.LOOM_TEST_CORE_MINOR || "9",
                     __PI_RELEASE__: env.LOOM_TEST_CORE_RELEASE || "5" };

   function valueOf( token )
   {
      const t = token.trim().replace( /^"|"$/g, "" );
      return ( defines[t] !== undefined ) ? String( defines[t] ).replace( /^"|"$/g, "" ) : t;
   }

   /* One conditional-compilation stack, true while each enclosing branch is live. */
   function isLive( stack ) { return stack.every( Boolean ); }

   /*
    * Apply one directive to the branch stack and the define table. Directives
    * with no effect here -- include, engine, feature-id, feature-info -- fall
    * through, but every one of them is still commented out by the caller.
    */
   function applyDirective( stack, directive, rest )
   {
      switch ( directive )
      {
      case "ifdef":  stack.push( defines[rest.trim()] !== undefined ); break;
      case "ifndef": stack.push( defines[rest.trim()] === undefined ); break;
      case "ifeq":
      {
         const parts = rest.trim().split( /\s+/ );
         stack.push( valueOf( parts[0] ) === valueOf( parts[1] ) );
         break;
      }
      case "ifoneof":
      {
         /*
          * #ifoneof NAME A B ... -- absent from the first version of
          * this stand-in, so its body ran unguarded and redefined the
          * platform the #ifeq above had just settled. A directive that
          * is not understood must still BALANCE, or every #endif after
          * it pops someone else's branch.
          */
         const parts = rest.trim().split( /\s+/ );
         const subject = valueOf( parts[0] );
         stack.push( parts.slice( 1 ).some( p => valueOf( p ) === subject ) );
         break;
      }
      case "else":   stack[stack.length - 1] = !stack[stack.length - 1]; break;
      case "endif":  stack.pop(); break;
      case "define":
      {
         const m = /^(\w+)\s*(.*)$/.exec( rest.trim() );
         if ( isLive( stack ) && m )
            defines[m[1]] = m[2].trim();
         break;
      }
      default: break;
      }
   }

   /* A code line with every defined token replaced, as the real core does. */
   function substituteDefines( line )
   {
      let code = line;
      for ( const name of Object.keys( defines ) )
         if ( name !== "__PI_PLATFORM__" )
            code = code.replace( new RegExp( "\\b" + name + "\\b", "g" ), defines[name] );
      return code;
   }

   /*
    * options.onInclude( target ), if given, is called at each LIVE
    * #include "..." -- at the point it occurs, so a caller can follow the
    * include with this same define table, as the real core does.
    */
   function preprocess( text, options )
   {
      const onInclude = options && options.onInclude;
      const out = [];
      const stack = [];
      let continued = false;      // the previous directive ended in a backslash
      for ( const line of text.split( "\n" ) )
      {
         /*
          * A directive continues onto the next line after a trailing
          * backslash, as #feature-info does. The continuation is part of the
          * directive, not code, and must be commented out with it.
          */
         const d = continued ? null : /^\s*#\s*(\w+)\s*(.*)$/.exec( line );
         if ( continued || d )
         {
            if ( d )
               applyDirective( stack, d[1], d[2] );
            if ( d && onInclude && d[1] === "include" && isLive( stack ) )
            {
               const m = /^"([^"]+)"/.exec( d[2].trim() );
               if ( m ) onInclude( m[1] );
            }
            continued = /\\\s*$/.test( line );
            out.push( "//" + line );
            continue;
         }
         // A line inside a dead branch must not run, but must still occupy
         // its line number.
         out.push( isLive( stack ) ? substituteDefines( line ) : "//" + line );
      }
      return out.join( "\n" );
   }

   return { defines, preprocess };
}

module.exports = { createPreprocessor };
