/*
 * Parsing ASIAIR frame names.
 *
 * The grammar published on forums and in third-party parsers is
 *
 *    {TYPE}_[{TARGET}_]{EXP}_Bin{N}_{FILTER}_gain{G}_{STAMP}_{TEMP}C_{SEQ}
 *
 * and it is WRONG for real hardware. An actual frame off a 2600MM reads
 *
 *    Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit
 *
 * -- a SPACE in the target, a camera token and a rotation token, none of
 * which the published grammar has. A positional parser built from it
 * reads "2600MM" as the filter on every single frame, and nothing ever
 * tells you: the frames group by a filter that is really a camera model.
 *
 * So: no positional parsing. Anchor on the timestamp -- the one token
 * that cannot be mistaken for anything else -- and work OUTWARD from it.
 * The tail of the name is rigid; the target is free text and has to be
 * whatever is left over, never something the parser goes looking for.
 */

function AsiairNames() {}

AsiairNames.STAMP     = /^\d{8}-\d{6}$/;
AsiairNames.EXPOSURE  = /^\d+(\.\d+)?(s|ms)$/;
AsiairNames.BIN       = /^Bin\d+$/;
AsiairNames.GAIN      = /^gain\d+$/;
AsiairNames.ROTATION  = /^\d+deg$/;
AsiairNames.TEMP      = /^-?\d+(\.\d+)?C$/;
AsiairNames.SEQUENCE  = /^\d+$/;
AsiairNames.EXTENSION = /\.(fit|fits)$/i;

/*
 * The only frame types an ASIAIR writes. Checked rather than assumed,
 * because the leading token is otherwise whatever happens to precede the
 * first underscore -- and on a card copied through macOS that includes
 * AppleDouble sidecars. "._Light_IC 1396A_180.0s_..." splits to a type of
 * "." and a TARGET of "Light_IC 1396A", so every sidecar became a frame
 * filed under a phantom target. Seen for real: a 34-frame folder
 * enumerated as 68 files.
 */
AsiairNames.TYPES = [ "Light", "Flat", "Dark", "Bias" ];

/*
 * The tail after the timestamp: rotation, temperature, sequence.
 *
 * Split out because each of the three carries the same rule -- a SECOND
 * occurrence is ambiguous and the name is refused rather than guessed --
 * and three copies of that rule inside the main parser was most of what
 * made it hard to read.
 *
 * Returns null when the tail is ambiguous or incomplete. Every real
 * ASIAIR name carries a temperature and a sequence number.
 */
AsiairNames.tailFields = function( tail )
{
   var out = { rotation: null, temp: null, sequence: null };
   var kinds = [ [ "rotation", AsiairNames.ROTATION ],
                 [ "temp",     AsiairNames.TEMP ],
                 [ "sequence", AsiairNames.SEQUENCE ] ];

   for ( var k = 0; k < tail.length; ++k )
      for ( var j = 0; j < kinds.length; ++j )
         if ( kinds[j][1].test( tail[k] ) )
         {
            if ( out[kinds[j][0]] != null )
               return null;           // stated twice: which one is true?
            out[kinds[j][0]] = tail[k];
            break;
         }

   return ( out.temp == null || out.sequence == null ) ? null : out;
};

/* The index of the last token before `before` that matches, or -1. */
AsiairNames.lastMatch = function( tokens, re, before )
{
   for ( var i = before; i >= 0; --i )
      if ( re.test( tokens[i] ) )
         return i;
   return -1;
};

/* The index of the ONLY token matching, -1 for none, -2 for ambiguous. */
AsiairNames.onlyMatch = function( tokens, re )
{
   var at = -1;
   for ( var i = 0; i < tokens.length; ++i )
      if ( re.test( tokens[i] ) )
      {
         if ( at >= 0 )
            return -2;
         at = i;
      }
   return at;
};

AsiairNames.isKnownType = function( token )
{
   for ( var i = 0; i < AsiairNames.TYPES.length; ++i )
      if ( String( token ).toLowerCase() == AsiairNames.TYPES[i].toLowerCase() )
         return true;
   return false;
};

/*
 * Returns null for anything that is not an ASIAIR frame, and for anything
 * whose shape is ambiguous. Returning a half-filled record instead would
 * push the guess downstream into the night clustering, where it becomes a
 * frame silently filed under the wrong date.
 *
 * `binToken` is the RAW filename token ("Bin1"), deliberately NOT called
 * `binning`: the FITS keyword of that name holds "1". One name for two
 * different values is how a comparison quietly succeeds on frames that
 * share nothing.
 */
AsiairNames.parseName = function( filename )
{
   if ( !AsiairNames.EXTENSION.test( filename ) )
      return null;

   var t = String( filename ).replace( AsiairNames.EXTENSION, "" ).split( "_" );
   if ( !AsiairNames.isKnownType( t[0] ) )
      return null;

   // The stamp anchors everything. Two of them, or none, is not ours.
   var at = AsiairNames.onlyMatch( t, AsiairNames.STAMP );
   if ( at < 2 )
      return null;

   // gain sits immediately before the stamp, the filter before gain.
   if ( !AsiairNames.GAIN.test( t[at-1] ) )
      return null;

   // The bin token is the last before the filter; the exposure the last
   // before the bin -- taking the FIRST would steal "30s" out of a target
   // called "M42_30s".
   var binAt = AsiairNames.lastMatch( t, AsiairNames.BIN, at-3 );
   if ( binAt < 0 )
      return null;
   var expAt = AsiairNames.lastMatch( t, AsiairNames.EXPOSURE, binAt-1 );
   if ( expAt < 1 )
      return null;

   var tail = AsiairNames.tailFields( t.slice( at+1 ) );
   if ( tail == null )
      return null;

   return { type: t[0],
            target: t.slice( 1, expAt ).join( "_" ),
            exposure: t[expAt],
            binToken: t[binAt],
            camera: ( binAt+1 <= at-3 ) ? t.slice( binAt+1, at-2 ).join( "_" ) : null,
            filter: t[at-2],
            gain: t[at-1],
            stamp: t[at],
            rotation: tail.rotation,
            temp: tail.temp,
            sequence: tail.sequence };
};

/*
 * A comparison key in SECONDS, from the wall-clock components via
 * Date.UTC.
 *
 * Deliberately NOT `new Date( "..." )`: that resolves against the
 * importing machine's zone, so the same card would cluster differently on
 * a laptop in another timezone, and a DST transition would move a session
 * boundary. 00:30 to 05:00 on a US spring-forward date is 4.5 hours by
 * the clock and 3.5 elapsed. UTC arithmetic on the printed components is
 * monotone and has no discontinuities.
 *
 * Seconds rather than minutes because flooring loses the threshold: two
 * lights four hours and fifty-nine seconds apart would round to exactly
 * four hours and stay in one session, when the rule says over four hours
 * splits.
 */
AsiairNames.stampKey = function( stamp )
{
   if ( !AsiairNames.STAMP.test( String( stamp ) ) )
      return null;

   var y  = parseInt( stamp.substr( 0, 4 ), 10 );
   var mo = parseInt( stamp.substr( 4, 2 ), 10 );
   var d  = parseInt( stamp.substr( 6, 2 ), 10 );
   var h  = parseInt( stamp.substr( 9, 2 ), 10 );
   var mi = parseInt( stamp.substr( 11, 2 ), 10 );
   var se = parseInt( stamp.substr( 13, 2 ), 10 );

   if ( mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || se > 59 )
      return null;

   var ms = Date.UTC( y, mo-1, d, h, mi, se );

   /*
    * Date.UTC rolls 31 February over into March rather than refusing it.
    * A rolled date is not the date that was printed on the card, so it is
    * not a date this accepts: filing a frame under a day it was not shot
    * is worse than reporting the name as unreadable.
    */
   var back = new Date( ms );
   if ( back.getUTCFullYear() != y || back.getUTCMonth() != mo-1 || back.getUTCDate() != d )
      return null;

   return Math.floor( ms / 1000 );
};

AsiairNames.GAP_HOURS = 4;

/*
 * Cluster by gap: a new session wherever consecutive frames are more than
 * gapHours apart.
 *
 * Only LIGHTS are ever passed in. Clustering the flats alongside them is
 * what silently merges two nights -- a run of daytime flats bridges the
 * gap between a night ending at dawn and the next beginning at dusk, and
 * afterwards nothing can tell the two apart. Flats get their own batches
 * and are assigned to a session once the boundaries already exist.
 *
 * Frames with a null key -- an unreadable or impossible timestamp -- are
 * excluded rather than sorted to the front, where they would drag an
 * unrelated frame into the first session.
 */
AsiairNames.sessions = function( frames, gapHours )
{
   var gap = ( gapHours == null ? AsiairNames.GAP_HOURS : gapHours ) * 3600;

   var usable = [];
   for ( var i = 0; i < frames.length; ++i )
      if ( frames[i].key != null )
         usable.push( frames[i] );
   usable.sort( function( a, b ) { return a.key - b.key; } );

   var out = [];
   for ( var j = 0; j < usable.length; ++j )
   {
      var f = usable[j];
      var last = out.length ? out[out.length-1] : null;
      if ( last == null || f.key - last.last > gap )
         out.push( { first: f.key, last: f.key, frames: [ f ] } );
      else
      {
         last.last = f.key;
         last.frames.push( f );
      }
   }
   return out;
};

/*
 * A night is one target inside one session. The session supplies the
 * boundaries, the target supplies the split.
 *
 * Labelled with the date of its own first frame, which for a session
 * running past midnight is the evening date -- the one an observer would
 * call it by.
 *
 * `frames` is not decoration. Without it a selected night is only a
 * label, and every caller has to walk back through the session
 * re-filtering by target to learn which files it means: the same lookup
 * written twice, in two places, free to drift apart.
 */
/* The distinct filters in a set of frames, sorted, without duplicates. */
AsiairNames.filtersOf = function( frames )
{
   var seen = Object.create( null ), out = [];
   for ( var i = 0; i < frames.length; ++i )
      if ( frames[i].filter && !( frames[i].filter in seen ) )
      {
         seen[frames[i].filter] = true;
         out.push( frames[i].filter );
      }
   out.sort();
   return out;
};

/* Frames grouped by target, in the order the targets first appear. */
AsiairNames.byTarget = function( frames )
{
   var groups = Object.create( null ), order = [];
   for ( var i = 0; i < frames.length; ++i )
   {
      var t = frames[i].target;
      if ( !( t in groups ) ) { groups[t] = []; order.push( t ); }
      groups[t].push( frames[i] );
   }
   return { order: order, groups: groups };
};

AsiairNames.nights = function( sessions )
{
   var out = [];
   for ( var s = 0; s < sessions.length; ++s )
   {
      var by = AsiairNames.byTarget( sessions[s].frames );
      for ( var k = 0; k < by.order.length; ++k )
      {
         var g = by.groups[by.order[k]];
         out.push( { sessionIndex: s, target: by.order[k],
                     first: g[0].key, last: g[g.length-1].key,
                     count: g.length,
                     filters: AsiairNames.filtersOf( g ),
                     date: AsiairNames.dateOf( g[0].stamp ),
                     frames: g } );
      }
   }

   out.sort( function( a, b ) {
      return a.first - b.first ||
             ( a.target < b.target ? -1 : a.target > b.target ? 1 : 0 );
   } );
   return out;
};

AsiairNames.dateOf = function( stamp )
{
   return stamp.substr( 0, 4 ) + "-" + stamp.substr( 4, 2 ) + "-" + stamp.substr( 6, 2 );
};

/*
 * Flats are clustered among THEMSELVES, and only after the session
 * boundaries are already fixed.
 *
 * Clustering them together with the lights is what merges two nights: a
 * run of daytime flats at 10:00, 14:00 and 18:00 bridges the gap between
 * a night ending at 06:00 and the next beginning at 20:00, every
 * individual gap stays under the threshold, and the two nights become one
 * with their flats pooled.
 */
AsiairNames.flatBatches = function( flats, gapHours )
{
   var raw = AsiairNames.sessions( flats, gapHours );
   for ( var i = 0; i < raw.length; ++i )
      raw[i].mid = Math.floor( ( raw[i].first + raw[i].last ) / 2 );
   return raw;
};

/*
 * A batch goes WHOLE to the session nearest its midpoint.
 *
 * Assigning each flat on its own distance would bisect a batch lying
 * across the midpoint between two sessions, which is the one thing a
 * batch must never do: half a flat set calibrates nothing, and the
 * missing half is not reported anywhere.
 *
 * A batch sitting exactly on the midpoint is equidistant from both. The
 * tie goes to the EARLIER session -- `<` rather than `<=` below. Any rule
 * would do; having one written down is what stops the answer depending on
 * iteration order.
 */
AsiairNames.assignBatches = function( batches, sessions )
{
   var out = [];
   for ( var b = 0; b < batches.length; ++b )
   {
      var best = -1, bestDist = Infinity;
      for ( var s = 0; s < sessions.length; ++s )
      {
         var d = ( batches[b].mid < sessions[s].first )
               ? sessions[s].first - batches[b].mid
               : ( batches[b].mid > sessions[s].last
                 ? batches[b].mid - sessions[s].last : 0 );
         if ( d < bestDist ) { bestDist = d; best = s; }
      }
      out.push( best );
   }
   return out;
};

/*
 * Does one flat suit one light filter?
 *
 * Returns "exact", "weak", or null for no. Split out of matchFlats
 * because the decision for a single pair is the whole rule, and having it
 * inside two nested loops is what made that rule hard to see.
 *
 * Filter and binning must both be PRESENT and equal. Present matters as
 * much as equal: entryFor returns records with no camera and no rotation,
 * and an absent field on both sides compares undefined with undefined and
 * reads as agreement -- so a bare inequality reports an exact match
 * between frames that share nothing.
 *
 * Camera and rotation must agree WHERE BOTH SIDES STATE THEM. A rotation
 * change puts the dust somewhere else. Stated on one side only is weak:
 * offered and flagged, because refusing it loses good flats while
 * accepting it quietly hides a real mismatch.
 *
 * Gain is deliberately not compared. A flat is a ratio, and WBPP groups
 * flats by gain itself.
 */
AsiairNames.flatSuits = function( want, flat )
{
   if ( want.filter == null || flat.filter == null || flat.filter != want.filter )
      return null;
   if ( want.binning == null || flat.binning == null || flat.binning != want.binning )
      return null;

   var soft = false;
   var pairs = [ [ want.camera, flat.camera ], [ want.rotation, flat.rotation ] ];
   for ( var p = 0; p < pairs.length; ++p )
   {
      var a = pairs[p][0], b = pairs[p][1];
      if ( a != null && b != null )
      {
         if ( a != b )
            return null;
      }
      else if ( a != null || b != null )
         soft = true;
   }
   return soft ? "weak" : "exact";
};

AsiairNames.matchFlats = function( lightFilters, flatRecords )
{
   var out = [];
   for ( var i = 0; i < lightFilters.length; ++i )
   {
      var want = lightFilters[i];
      var hits = [], weak = false;

      for ( var j = 0; j < flatRecords.length; ++j )
      {
         var how = AsiairNames.flatSuits( want, flatRecords[j] );
         if ( how == null )
            continue;
         if ( how == "weak" )
            weak = true;
         hits.push( flatRecords[j] );
      }

      out.push( { filter: want.filter, flats: hits,
                  strength: hits.length == 0 ? "missing" : ( weak ? "weak" : "exact" ) } );
   }
   return out;
};

/*
 * Containment on path COMPONENTS.
 *
 * A character prefix test would call /Volumes/ASIAIR-backup a child of
 * /Volumes/ASIAIR and refuse a perfectly good destination -- which is as
 * wrong as accepting a bad one, just less dangerous.
 */
AsiairNames.isInside = function( path, root )
{
   function parts( p )
   {
      var out = String( p ).split( "/" );
      while ( out.length && out[out.length-1] == "" )
         out.pop();
      return out;
   }

   var a = parts( path ), b = parts( root );
   if ( a.length < b.length )
      return false;
   for ( var i = 0; i < b.length; ++i )
      if ( a[i] != b[i] )
         return false;
   return true;
};

/*
 * What the import will write, decided in one pass.
 *
 * Built ONCE at Run with the lights and the flats frozen together, so the
 * flat set cannot drift away from the light set while the copy proceeds.
 *
 * Flats follow the lights that SURVIVED review. A filter whose frames
 * were all rejected contributes nothing: calibration frames for data that
 * is not there are clutter in the destination and a puzzle in WBPP.
 */
AsiairNames.manifest = function( approvedLights, flatMatches, destination )
{
   function xisf( p )
   {
      var name = String( p ).split( "/" ).pop();
      return name.replace( AsiairNames.EXTENSION, ".xisf" );
   }

   var surviving = Object.create( null );
   var lights = [];
   for ( var i = 0; i < approvedLights.length; ++i )
   {
      surviving[approvedLights[i].filter] = true;
      lights.push( { src: approvedLights[i].path,
                     dst: destination + "/Light/" + xisf( approvedLights[i].path ) } );
   }

   var flats = [];
   for ( var m = 0; m < flatMatches.length; ++m )
   {
      if ( !( flatMatches[m].filter in surviving ) )
         continue;
      for ( var f = 0; f < flatMatches[m].flats.length; ++f )
      {
         var src = flatMatches[m].flats[f].path;
         flats.push( { src: src, dst: destination + "/Flat/" + xisf( src ) } );
      }
   }

   /*
    * Two sources, one destination -- Plan and Autorun holding the same
    * name, say. Overwriting cannot resolve this: whichever is written
    * second wins and a frame vanishes without a word. So it is refused
    * outright, whatever the overwrite setting says, and the whole
    * schedule is emptied rather than half-run.
    */
   var seen = Object.create( null ), collisions = [];
   var all = lights.concat( flats );
   for ( var k = 0; k < all.length; ++k )
   {
      /*
       * Keyed case-INSENSITIVELY. The destination is very often an APFS
       * or HFS+ volume that folds case, where a.xisf and A.xisf are one
       * file -- so comparing exactly would let the second silently
       * overwrite the first on the very filesystem this usually runs on.
       */
      var key = all[k].dst.toLowerCase();

      if ( key in seen )
      {
         /*
          * The SAME source scheduled twice is a duplicate, not a
          * collision: writing it twice produces the file it was going to
          * produce anyway. Only two DIFFERENT sources landing on one name
          * lose data, and only those are refused. Treating a duplicate as
          * a collision would empty the whole schedule over a flat that
          * two filters legitimately share.
          */
         if ( seen[key] != all[k].src )
            collisions.push( { dst: all[k].dst, a: seen[key], b: all[k].src } );
      }
      else
         seen[key] = all[k].src;
   }

   if ( collisions.length )
      return { lights: [], flats: [], collisions: collisions };

   // Duplicates removed, so a shared flat is written once.
   function dedupe( list )
   {
      var out = [], had = Object.create( null );
      for ( var i = 0; i < list.length; ++i )
      {
         var k2 = list[i].dst.toLowerCase();
         if ( k2 in had ) continue;
         had[k2] = true;
         out.push( list[i] );
      }
      return out;
   }

   return { lights: dedupe( lights ), flats: dedupe( flats ), collisions: [] };
};
