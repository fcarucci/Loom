/*
 * Shared by the two drivers beside it. Each loads ONE entry script the way
 * PixInsight runs it -- its own #include block, not the selftest's -- with
 * only its under-test define, which suppresses main(). The selftest cannot
 * show this: it includes every library itself before the entry scripts,
 * so a library an entry script forgot to include is still in scope there.
 *
 * What is asserted: loading did not throw, every namespace the script
 * uses is defined, and -- for a script that includes Steps, `fixturePath`
 * given -- the Steps namespace it sees has every member, of the same type,
 * that the single-file Steps.js gave it (ci/fixtures/steps-members.json).
 * FrameSelector.js includes no Steps file at all. The result goes where
 * the selftest's does, so /tmp/agent-scratch/pi-suite.sh can dispatch a
 * driver and wait for it:
 *
 *    /tmp/agent-scratch/pi-suite.sh <repo>/ci/pi-entry-alone/FrameSelector.js
 *    /tmp/agent-scratch/pi-suite.sh <repo>/ci/pi-entry-alone/FlyThrough.js
 *    /tmp/agent-scratch/pi-suite.sh <repo>/ci/pi-entry-alone/Loom.js
 */
function entryAloneCheck( entry, fixturePath, namespaces )
{
   var failures = [];
   var run = 0;
   function expect( name, ok ) { ++run; if ( !ok ) failures.push( name ); }

   if ( fixturePath != null )
   {
      var fixture = JSON.parse( File.readTextFile( fixturePath ) );
      var want = fixture.core5;
      var n = 0;
      for ( var k in want )
      {
         ++n;
         var type = want[k].split( " " )[0];
         expect( entry + ": Steps." + k + " is a " + type, typeof Steps[k] == type );
      }
      expect( entry + ": the fixture lists every member (" + n + ")", n == 295 );
   }
   for ( var i = 0; i < namespaces.length; ++i )
      expect( entry + ": " + namespaces[i] + " is defined",
              eval( "typeof " + namespaces[i] ) != "undefined" );

   var summary = ( failures.length == 0 ? "PASS" : "FAIL" ) + " ENTRY-ALONE[" + entry + "] " +
                 run + " run, " + failures.length + " failed\n" + failures.join( "\n" ) + "\n";
   console.writeln( summary );
   if ( !File.directoryExists( "/tmp/agent-scratch" ) )
      File.createDirectory( "/tmp/agent-scratch", true );
   var f = new File;
   f.createForWriting( "/tmp/agent-scratch/lhso-selftest.txt" );
   f.outText( summary );
   f.close();
}
