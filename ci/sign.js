#engine v8

/*
 * PixInsight only: signs Loom's entry scripts with a certified developer's
 * keys. Run by ci/sign.sh in a separate PixInsight instance -- never start it
 * by hand in the main one. It takes no input from the interface:
 *
 *    <dir>/request.json   { keys, passwordFile, files: [ absolute .js paths ], xml: [ absolute .xri paths ] }
 *    <dir>/result.json    written last: { ok, error, files: [ { file, signature, valid, developerId, scriptId, timestamp } ] }
 *
 * Scripts get a .xsgn file beside them; an XML document (the update
 * repository's updates.xri) carries its signature inside, so it is rewritten
 * in place -- sign it only when it is final.
 *
 * where <dir> is Sign.DIR. The password is read from passwordFile (its own
 * text, trailing newline dropped) and never copied anywhere; the request is
 * deleted as soon as it is read. Each script's signature goes next to it
 * (Loom.js -> Loom.xsgn) and is verified straight after, so a signature the
 * core would reject is reported here and not on a user's machine.
 */

var Sign = { DIR: "/tmp/agent-scratch/loom-sign" };

Sign.run = function()
{
   var result = { ok: false, error: "", files: [] };
   try
   {
      var request = JSON.parse( File.readTextFile( Sign.DIR + "/request.json" ) );
      File.remove( Sign.DIR + "/request.json" );
      var password = File.readTextFile( request.passwordFile ).replace( /[\r\n]+$/, "" );
      if ( password.length == 0 )
         throw new Error( "the password file is empty: " + request.passwordFile );
      var files = request.files || [];
      for ( var i = 0; i < files.length; ++i )
      {
         var script = files[i], signature = script.replace( /\.js$/, ".xsgn" );
         Security.generateScriptSignatureFile( signature, script, [], request.keys, password );
         var s = Security.getScriptSignature( script );
         result.files.push( { file: script, signature: signature, valid: s.valid, developerId: s.developerId, scriptId: s.scriptId, timestamp: s.timestamp } );
      }
      var xml = request.xml || [];
      for ( var j = 0; j < xml.length; ++j )
      {
         Security.generateXMLSignature( xml[j], request.keys, password );
         var x = Security.getXMLSignature( xml[j] );
         result.files.push( { file: xml[j], signature: xml[j], valid: x.valid, developerId: x.developerId, scriptId: "(xml)", timestamp: x.timestamp } );
      }
      password = "";
      result.ok = result.files.length > 0 && result.files.every( function( f ) { return f.valid; } );
      if ( !result.ok && !result.error )
         result.error = "a signature did not verify";
   }
   catch ( e )
   {
      result.error = String( e );
   }
   File.writeTextFile( Sign.DIR + "/result.json", JSON.stringify( result ) );
};

Sign.run();
