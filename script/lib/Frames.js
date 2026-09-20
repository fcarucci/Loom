/*
 * Frame Selector: the decisions.
 *
 * Everything here is pure arithmetic over measurements, with no PixInsight
 * call and no file access, because this is the code that decides which of
 * someone's subframes get deleted. It is tested under node, exhaustively,
 * including every degenerate case the review found -- an eccentricity of
 * zero, a MAD of zero, a channel of four frames -- none of which can be
 * reproduced on demand with real data.
 *
 * The PixInsight half lives in script/FrameSelector.js.
 */

var Frames = {};

/*
 * Bumped when the meaning of a stored measurement changes. A cached number
 * from an older definition compared against a fresh one is exactly the
 * quiet wrongness this tool exists to report.
 */
Frames.MEASURE_VERSION = "v1";

/*
 * SubframeSelector's measurement row, by position.
 *
 * Read from WBPP's own analyzer, which carries the comment "fixed indexes
 * that need to be aligned with the process implementation"
 * (BPP-SubframeAnalyzer.js:481), and cross-checked against a live
 * measurement on 1.9.5 build 1702.
 *
 * PSF SNR is 28. An earlier draft said 8; column 8 reads 0 on every frame
 * measured here, so the score's dominant term would have been a constant
 * zero -- a failure with no symptom.
 */
Frames.COL = { path: 3, fwhm: 5, eccentricity: 6, noise: 12, stars: 14,
               psfSNR: 28 };

Frames.metricsFromRow = function( row )
{
   return { path:         row[Frames.COL.path],
            fwhm:         row[Frames.COL.fwhm],
            eccentricity: row[Frames.COL.eccentricity],
            noise:        row[Frames.COL.noise],
            stars:        row[Frames.COL.stars],
            psfSNR:       row[Frames.COL.psfSNR] };
};

/*
 * Ranges a metric can occupy on a real subframe, used to check that a
 * column still MEANS what it is read as. Deliberately non-overlapping
 * between metrics, so a reordered table puts a value outside its range.
 *
 * The spec asked for four MUTUALLY DISJOINT ranges, so that any swap of two
 * columns moves a value outside the range for its slot. Measured against
 * real frames on 1.9.5 build 1702, that is not achievable, and the reason
 * is worth writing down rather than fudging.
 *
 *                 eccentricity   FWHM    PSF SNR    stars
 *   single sub          0.547    3.518      8.815     7507
 *   drizzled master     0.511    6.563  35526.663    40551
 *
 * PSF SNR is not "far above any star count" as the spec assumed -- on a
 * single subframe it is 8.8 against 7507 stars, three orders of magnitude
 * BELOW. For stars and psfSNR to be disjoint across both, psfSNR's ceiling
 * would have to be at least 35527 while its floor stayed under 7507, and
 * stars' floor would have to sit above that ceiling while containing 7507.
 * There is no such pair of ranges.
 *
 * So the ranges below are honest bounds that contain real values, and the
 * swap detection that the spec actually needs is done by
 * Frames.meaningProblems, which uses a discriminator that DOES hold: a star
 * count is an integer and a PSF SNR is not.
 */
Frames.PLAUSIBLE = {
   fwhm:         { lo: 1,    hi: 60 },         // pixels
   eccentricity: { lo: 0.01, hi: 0.95 },       // a fraction, never 0 or 1
   psfSNR:       { lo: 1,    hi: 1e9 },        // ~9 on a sub, ~35000 on a master
   stars:        { lo: 200,  hi: 500000 }      // counts
};

/*
 * Ascending by measured value on real frames. The spec had stars below
 * psfSNR; both frames measured here put psfSNR below stars.
 */
Frames.METRIC_RANGE_ORDER = [ "eccentricity", "fwhm", "psfSNR", "stars" ];

Frames.metricInRange = function( name, value )
{
   var r = Frames.PLAUSIBLE[name];
   return ( r != null ) && isFinite( value ) && value >= r.lo && value <= r.hi;
};

/*
 * Does this row still MEAN what it is read as?
 *
 * Pinning the indices cannot answer that -- Frames.COL.psfSNR === 28 passes
 * unchanged after the process reorders its table -- and disjoint ranges
 * cannot either, for the reason set out above. What discriminates is the
 * shape of each figure:
 *
 *   - eccentricity is a fraction strictly inside (0, 1); every other metric
 *     measured here is above 1
 *   - a star COUNT is a non-negative integer; PSF SNR never is. This is the
 *     swap the spec most needs caught, and it is caught exactly
 *   - the path is a non-empty string, so a numeric column in slot 3 fails
 *
 * Honest limit: on a single subframe, FWHM 3.5 and PSF SNR 8.8 are both
 * plausible small non-integers, so a swap of THOSE two is not detectable
 * from one frame's values alone. That gap is stated here rather than hidden
 * behind ranges that look disjoint and are not.
 */
Frames.meaningProblems = function( metrics )
{
   var bad = [];
   if ( typeof metrics.path != "string" || metrics.path === "" )
      bad.push( "path is not a non-empty string" );
   if ( !( metrics.eccentricity > 0 && metrics.eccentricity < 1 ) )
      bad.push( "eccentricity is not a fraction in (0,1)" );
   if ( !Frames.metricInRange( "fwhm", metrics.fwhm ) )
      bad.push( "FWHM outside a plausible range" );
   if ( !( isFinite( metrics.stars ) && metrics.stars >= 0 &&
           Math.floor( metrics.stars ) === metrics.stars ) )
      bad.push( "star count is not a whole number" );
   if ( !isFinite( metrics.psfSNR ) ||
        Math.floor( metrics.psfSNR ) === metrics.psfSNR )
      bad.push( "PSF SNR is a whole number, so it is probably the star count" );
   return bad;
};

/*
 * The four figures the selector judges on, in the order the score weights
 * them. Named once so a metric cannot be added to the score and forgotten
 * in the gates.
 */
Frames.METRICS = [ "psfSNR", "fwhm", "eccentricity", "stars" ];

Frames.STATE = { APPROVED: "approved",
                 REJECTED: "rejected",
                 UNMEASURABLE: "unmeasurable" };

/*
 * Valid means finite AND positive, not merely present.
 *
 * Zero is excluded because every one of these appears in a denominator: an
 * eccentricity of 0 makes the score infinite, and a star count of 0 makes
 * the median ratio infinite. A zero weight does not rescue it either --
 * JavaScript evaluates 0 * Infinity as NaN.
 */
Frames.metricValid = function( value )
{
   return typeof value == "number" && isFinite( value ) && value > 0;
};

/*
 * A frame is measurable only if EVERY metric is. A frame missing one is not
 * scored and not auto-rejected: comparisons against NaN are all false, so an
 * invalid frame would otherwise sail through every gate and be kept.
 */
Frames.frameValid = function( metrics )
{
   if ( metrics == null )
      return false;
   for ( var i = 0; i < Frames.METRICS.length; ++i )
      if ( !Frames.metricValid( metrics[Frames.METRICS[i]] ) )
         return false;
   return true;
};

/*
 * Below this many VALID measurements the relative clip does not run. A
 * median exists at three frames; a dispersion worth deleting files over does
 * not. At ten frames the simulated loss is already about double its
 * asymptotic value, which is the honest reason this is not lower.
 */
Frames.MIN_FRAMES = 10;

/*
 * A spread below this fraction of the median is not a spread, it is the
 * measurement's own repeatability. Clipping on it rejects a perfect night:
 * FWHM [4, 4, 4, 4, 4.000001] has a MAD of zero and every k rejects the
 * last frame.
 */
Frames.SIGMA_FLOOR_FRACTION = 0.01;

/* Which direction is bad, per metric. */
Frames.WORSE_WHEN = { psfSNR: "lower", fwhm: "higher",
                      eccentricity: "higher", stars: "lower" };

Frames.median = function( values )
{
   if ( values == null || values.length == 0 )
      return NaN;
   var v = values.slice().sort( function( a, b ) { return a - b; } );
   var n = v.length, h = n >> 1;
   return ( n % 2 ) ? v[h] : ( v[h-1] + v[h] )/2;
};

/*
 * MAD, normalised to a Gaussian sigma. MAD rather than the standard
 * deviation because a handful of disasters widens a deviation-based gate and
 * defeats the thing meant to catch them.
 */
Frames.sigma = function( values )
{
   var m = Frames.median( values );
   if ( !isFinite( m ) )
      return NaN;
   var d = [];
   for ( var i = 0; i < values.length; ++i )
      d.push( Math.abs( values[i] - m ) );
   return 1.4826 * Frames.median( d );
};

/*
 * One metric's rejection bound. Returns the bound AND whether it is usable,
 * because a gate that cannot reject must say so rather than quietly passing
 * everything.
 */
Frames.gate = function( values, metric, k )
{
   var med = Frames.median( values ), sd = Frames.sigma( values );
   var g = { active: false, limit: null, median: med, sigma: sd, reason: null };
   if ( !isFinite( med ) || !isFinite( sd ) )
      { g.reason = "no usable measurements"; return g; }
   if ( sd <= 0 || sd < Frames.SIGMA_FLOOR_FRACTION*Math.abs( med ) )
      { g.reason = "no usable spread"; return g; }

   g.limit = ( Frames.WORSE_WHEN[metric] == "higher" ) ? med + k*sd : med - k*sd;
   if ( Frames.WORSE_WHEN[metric] == "lower" && g.limit <= 0 )
      { g.reason = "lower bound is not positive"; g.limit = null; return g; }
   g.active = true;
   return g;
};

/*
 * The frames a statistic may be computed over. An unmeasurable frame is not
 * merely skipped in the display; it must not reach a median or a MAD, or the
 * gate every other frame is judged against is built partly from NaN.
 */
Frames.validOnly = function( frames )
{
   var valid = [];
   for ( var i = 0; i < frames.length; ++i )
      if ( Frames.frameValid( frames[i] ) )
         valid.push( frames[i] );
   return valid;
};

Frames.columnOf = function( frames, metric )
{
   var values = [];
   for ( var i = 0; i < frames.length; ++i )
      values.push( frames[i][metric] );
   return values;
};

/*
 * Every gate for a channel, or none at all below the minimum count. The
 * caller cannot accidentally apply a gate built from four frames.
 */
Frames.relativeGates = function( frames, k )
{
   var valid = Frames.validOnly( frames );
   if ( valid.length < Frames.MIN_FRAMES )
      return {};

   var gates = {};
   for ( var m = 0; m < Frames.METRICS.length; ++m )
   {
      var name = Frames.METRICS[m];
      gates[name] = Frames.gate( Frames.columnOf( valid, name ), name, k );
   }
   return gates;
};

/*
 * SNR dominant, which is the conventional weighting. The weights are a
 * ranking preference and not an allocation of "quality" between independent
 * things: SNR, star count and FWHM all move together through detection and
 * fitting.
 */
Frames.DEFAULT_WEIGHTS = { psfSNR: 0.5, fwhm: 0.3, eccentricity: 0.1,
                           stars: 0.1 };

/*
 * Two stops either side of typical. Beyond that the frame's ranking is not
 * in question, and without a clamp a near-zero eccentricity contributes 40
 * against its nominal weight of 0.1.
 */
Frames.CLAMP = { lo: 0.25, hi: 4 };

Frames.medians = function( frames )
{
   var valid = Frames.validOnly( frames ), out = {};
   for ( var m = 0; m < Frames.METRICS.length; ++m )
   {
      var name = Frames.METRICS[m];
      out[name] = Frames.median( Frames.columnOf( valid, name ) );
   }
   return out;
};

/*
 * Higher is better in every term: FWHM and eccentricity are inverted, so a
 * sharper or rounder frame raises the score.
 *
 * Returns null rather than a number for an unmeasurable frame. A score of 0
 * would sort it below everything and invite someone to delete it.
 */
Frames.score = function( metrics, medians, weights )
{
   if ( !Frames.frameValid( metrics ) )
      return null;
   var w = weights || Frames.DEFAULT_WEIGHTS, total = 0;
   for ( var m = 0; m < Frames.METRICS.length; ++m )
   {
      var name = Frames.METRICS[m], med = medians[name];
      if ( !Frames.metricValid( med ) )
         return null;
      var ratio = ( Frames.WORSE_WHEN[name] == "higher" )
                  ? med/metrics[name] : metrics[name]/med;
      ratio = Math.max( Frames.CLAMP.lo, Math.min( Frames.CLAMP.hi, ratio ) );
      total += ( w[name] || 0 ) * ratio;
   }
   return total;
};

/*
 * k, the one number that decides how much is dropped. The names say which
 * end of the range they sit at; "Strict" and "Stricter" read as the same
 * thing and leave the loose end unnamed.
 */
Frames.PRESETS = { lenient: 3.0, balanced: 2.5, strict: 2.0 };
Frames.DEFAULT_PRESET = "balanced";

/*
 * A clip on median and MAD is scale-invariant, so Relative drops roughly the
 * same FRACTION however good the night was. That is right for "drop this
 * night's worst" and wrong for "drop frames that are bad in absolute terms",
 * and no amount of extra criteria reconciles them -- a frame the relative
 * gate rejects cannot be rescued by also passing a ceiling. Hence modes.
 */
Frames.MODE = { RELATIVE: "relative", ABSOLUTE: "absolute", BOTH: "both" };

Frames.defaultSettings = function()
{
   return { mode: Frames.MODE.RELATIVE,
            k: Frames.PRESETS[Frames.DEFAULT_PRESET],
            kEdited: false,
            weights: { psfSNR: Frames.DEFAULT_WEIGHTS.psfSNR,
                       fwhm: Frames.DEFAULT_WEIGHTS.fwhm,
                       eccentricity: Frames.DEFAULT_WEIGHTS.eccentricity,
                       stars: Frames.DEFAULT_WEIGHTS.stars },
            limits: {},              // metric -> { lo?, hi? }
            enabled: true };
};

Frames.copyOf = function( o )
{
   var out = {}, keys = Object.keys( o || {} );
   for ( var i = 0; i < keys.length; ++i )
      out[keys[i]] = o[keys[i]];
   return out;
};

/*
 * A preset sets k for every channel that has not had k edited by hand.
 * Editing a weight does not pin k -- only editing k does.
 *
 * weights and limits are copied rather than shared. The caller replaces a
 * channel's settings with the object returned here, so under a shallow copy
 * the two would go on pointing at one weights object: a weight edited after
 * a preset change would reach back into whatever still held the original,
 * altering a score with nothing in the dialog to account for it.
 */
Frames.applyPreset = function( settings, preset )
{
   var out = Frames.copyOf( settings );
   if ( settings.weights != null )
      out.weights = Frames.copyOf( settings.weights );
   if ( settings.limits != null )
   {
      out.limits = Frames.copyOf( settings.limits );
      var keys = Object.keys( out.limits );
      for ( var i = 0; i < keys.length; ++i )
         out.limits[keys[i]] = Frames.copyOf( out.limits[keys[i]] );
   }
   if ( !settings.kEdited && Frames.PRESETS[preset] != null )
      out.k = Frames.PRESETS[preset];
   return out;
};

Frames.METRIC_LABEL = { psfSNR: "PSF SNR", fwhm: "FWHM",
                        eccentricity: "eccentricity", stars: "stars" };

/* Rejections from this channel's own spread: only an ACTIVE gate can reject. */
Frames.relativeReasons = function( metrics, gates )
{
   var reasons = [];
   for ( var m = 0; m < Frames.METRICS.length; ++m )
   {
      var name = Frames.METRICS[m], g = gates[name];
      if ( g == null || !g.active )
         continue;
      var v = metrics[name];
      var bad = ( Frames.WORSE_WHEN[name] == "higher" ) ? v > g.limit : v < g.limit;
      if ( bad )
         reasons.push( Frames.METRIC_LABEL[name] + " " + Frames.round( v ) +
                       ", median " + Frames.round( g.median ) +
                       ", limit " + Frames.round( g.limit ) );
   }
   return reasons;
};

/* Rejections from a hard limit: only a CONFIGURED limit can reject. */
Frames.absoluteReasons = function( metrics, limits )
{
   var reasons = [];
   for ( var a = 0; a < Frames.METRICS.length; ++a )
   {
      var an = Frames.METRICS[a], lim = limits[an];
      if ( lim == null )
         continue;
      if ( lim.hi != null && metrics[an] > lim.hi )
         reasons.push( Frames.METRIC_LABEL[an] + " " + Frames.round( metrics[an] ) +
                       " above the limit of " + Frames.round( lim.hi ) );
      if ( lim.lo != null && metrics[an] < lim.lo )
         reasons.push( Frames.METRIC_LABEL[an] + " " + Frames.round( metrics[an] ) +
                       " below the limit of " + Frames.round( lim.lo ) );
   }
   return reasons;
};

/*
 * The verdict, with the rejection predicate stated per mode so nothing is
 * left to infer:
 *
 *   Relative  rejected iff an ACTIVE relative gate fails; hard limits are
 *             ignored entirely, even if values remain from another mode
 *   Absolute  rejected iff a CONFIGURED hard limit fails
 *   Both      rejected iff either fails
 */
Frames.verdict = function( metrics, gates, settings )
{
   if ( !Frames.frameValid( metrics ) )
      return { state: Frames.STATE.UNMEASURABLE,
               reasons: [ "a metric is missing or not positive" ] };

   var reasons = [];
   if ( settings.mode != Frames.MODE.ABSOLUTE )
      reasons = reasons.concat( Frames.relativeReasons( metrics, gates ) );
   if ( settings.mode != Frames.MODE.RELATIVE )
      reasons = reasons.concat( Frames.absoluteReasons( metrics, settings.limits ) );

   return { state: reasons.length ? Frames.STATE.REJECTED : Frames.STATE.APPROVED,
            reasons: reasons };
};

/* Three significant figures, so a reason reads as a sentence. */
Frames.round = function( v )
{
   if ( !isFinite( v ) )
      return String( v );
   var a = Math.abs( v );
   if ( a >= 100 ) return String( Math.round( v ) );
   if ( a >= 1 )   return v.toFixed( 2 );
   return v.toPrecision( 3 );
};

Frames.NO_FILTER = "(no filter)";

/*
 * Grouping is by the RAW FILTER string.
 *
 * Not Util.channelFromFilter: that maps to Loom's seven canonical channels
 * and returns null for anything else, so it would merge two distinct
 * filters into one group and silently drop unfamiliar ones. Here a group is
 * a filter, whatever it is called.
 *
 * Splitting further -- by exposure, by night -- is a judgement about
 * someone's data. This does not do it; it reports instead.
 */
Frames.groupByFilter = function( entries )
{
   /*
    * Prototype-less: a filter named "constructor" or "toString" reads as an
    * inherited member of a bare object, so the group array was never created
    * and push() was called on Object.prototype's function.
    */
   var out = Object.create( null );
   for ( var i = 0; i < entries.length; ++i )
   {
      var f = entries[i].filter;
      var key = ( f == null || String( f ).trim() == "" ) ? Frames.NO_FILTER
                                                          : String( f ).trim();
      if ( out[key] == null )
         out[key] = [];
      out[key].push( entries[i] );
   }
   return out;
};

/*
 * Frames with no readable FILTER are grouped so they can be SEEN, never so
 * they can be clipped: there is no evidence they belong together, so the
 * channel statistics that justify a deletion do not apply to them.
 */
Frames.autoRejectAllowed = function( filterKey )
{
   return filterKey != Frames.NO_FILTER;
};

/*
 * A group of one filter is not automatically comparable. A shorter exposure
 * legitimately loses on SNR and star count; a different binning makes pixel
 * FWHM incomparable; an uncalibrated frame among calibrated ones differs in
 * every metric. Any of those makes the channel's statistics meaningless, so
 * they are reported and Apply is blocked for that channel.
 */
Frames.comparability = function( group )
{
   var problems = [];
   function distinct( field )
   {
      // prototype-less for the same reason groupByFilter is: an inherited
      // key reads as already-seen, and the value is then never counted.
      var seen = Object.create( null ), n = 0;
      for ( var i = 0; i < group.length; ++i )
      {
         var v = String( group[i][field] );
         if ( !seen[v] ) { seen[v] = true; ++n; }
      }
      return n;
   }
   if ( group == null || group.length == 0 )
      return { uniform: true, problems: [] };

   if ( distinct( "exposure" ) > 1 )
      problems.push( "mixed exposure times" );
   if ( distinct( "binning" ) > 1 || distinct( "binningY" ) > 1 )
      problems.push( "mixed binning" );
   if ( distinct( "imageType" ) > 1 )
      problems.push( "mixed image types" );
   if ( distinct( "width" ) > 1 || distinct( "height" ) > 1 )
      problems.push( "mixed image geometry" );
   if ( distinct( "calibrated" ) > 1 )
      problems.push( "mixed calibration state" );
   /*
    * An ENTIRELY unknown calibration state is not uniformity, it is an
    * absence of evidence. A group of raw and calibrated frames that all
    * report "unknown" would otherwise pass the guard and be clipped.
    */
   if ( group[0].calibrated == "unknown" && distinct( "calibrated" ) == 1 )
      problems.push( "calibration state unknown for every frame" );

   return { uniform: problems.length == 0, problems: problems };
};

/*
 * A hand decision, which OUTRANKS the formula: somebody looked at the frame
 * and the formula did not. Changing k or a weight recomputes every verdict
 * and leaves overrides standing.
 *
 * Session only. Persisting them would put a second source of truth on disk
 * to silently contradict the formula next time.
 */
Frames.OVERRIDE = { RESCUED: "rescued", CONDEMNED: "condemned" };

Frames.finalState = function( verdictState, override )
{
   /*
    * An unmeasurable frame may be condemned by hand but never approved:
    * there is no measurement behind an approval, and "approved" would put it
    * in the kept set on the strength of nothing.
    */
   if ( verdictState == Frames.STATE.UNMEASURABLE )
      return ( override == Frames.OVERRIDE.CONDEMNED ) ? Frames.STATE.REJECTED
                                                        : Frames.STATE.UNMEASURABLE;
   if ( override == Frames.OVERRIDE.RESCUED )
      return Frames.STATE.APPROVED;
   if ( override == Frames.OVERRIDE.CONDEMNED )
      return Frames.STATE.REJECTED;
   return verdictState;
};

Frames.counts = function( rows )
{
   var c = { total: rows.length, kept: 0, rejected: 0, unmeasurable: 0,
             rescued: 0, condemned: 0 };
   for ( var i = 0; i < rows.length; ++i )
   {
      var r = rows[i];
      var final = Frames.finalState( r.state, r.override );
      if ( final == Frames.STATE.APPROVED )          ++c.kept;
      else if ( final == Frames.STATE.REJECTED )     ++c.rejected;
      else                                           ++c.unmeasurable;
      if ( r.override == Frames.OVERRIDE.RESCUED )   ++c.rescued;
      if ( r.override == Frames.OVERRIDE.CONDEMNED ) ++c.condemned;
   }
   return c;
};

/* Hand edits are named, so a summary never hides one. */
Frames.summaryLine = function( channel, c )
{
   var extra = [];
   if ( c.rescued )      extra.push( "+" + c.rescued + " rescued" );
   if ( c.condemned )    extra.push( "-" + c.condemned + " condemned" );
   if ( c.unmeasurable ) extra.push( c.unmeasurable + " unmeasurable" );
   return channel + ": " + c.kept + " kept of " + c.total +
          ( extra.length ? " (" + extra.join( ", " ) + ")" : "" );
};

/*
 * The execution manifest: a copy of the review taken when Apply is
 * confirmed, immutable thereafter.
 *
 * Three layers keep this honest. The COHORT is the frames and their
 * measurements, fixed at scan time -- channel medians always come from it,
 * never from the survivors of a partial run. The REVIEW is verdicts, knobs
 * and overrides, freely editable and worth nothing until committed. The
 * MANIFEST is what executes.
 *
 * Without that separation, retrying after a partial run recomputes the
 * statistics over the survivors, which is iterative clipping: the MAD
 * tightens with every deletion and the next pass condemns a frame nobody
 * looked at.
 *
 * The lifecycle is written as a table because the prose version of this
 * ("decisions are frozen") did not say what happens when someone edits a
 * knob after a partial run, and that is exactly the case that would
 * reintroduce iterative clipping.
 */
Frames.PHASE = { REVIEW: "review", EXECUTING: "executing",
                 STOPPED: "stopped", DONE: "done" };

Frames.canEdit = function( phase ) { return phase == Frames.PHASE.REVIEW; };

Frames.nextPhase = function( phase, event )
{
   var T = {};
   T[Frames.PHASE.REVIEW]    = { commit: Frames.PHASE.EXECUTING };
   T[Frames.PHASE.EXECUTING] = { stop: Frames.PHASE.STOPPED,
                                 finish: Frames.PHASE.DONE };
   T[Frames.PHASE.STOPPED]   = { resume: Frames.PHASE.EXECUTING,
                                 abandon: Frames.PHASE.REVIEW };
   T[Frames.PHASE.DONE]      = { review: Frames.PHASE.REVIEW };
   var row = T[phase] || {};
   return ( row[event] != null ) ? row[event] : null;
};

Frames.buildManifest = function( rows )
{
   var entries = [];
   for ( var i = 0; i < rows.length; ++i )
   {
      var r = rows[i];
      /*
       * The FINAL state, so a rescued frame is not deleted and a condemned
       * one is. Reading r.state here instead would silently ignore every
       * hand decision.
       */
      if ( Frames.finalState( r.state, r.override ) != Frames.STATE.REJECTED )
         continue;
      /*
       * A SNAPSHOT, deep-copied. Sharing the review's reasons array would
       * let a later edit change what the "frozen" manifest says, and the
       * audit record must survive the review being edited.
       *
       * The automatic verdict and the override are both recorded: if a
       * rescued frame later turns out to have been the bad one, the log
       * says who chose it.
       */
      entries.push( { path: r.path, channel: r.channel || "",
                      digest: r.digest, size: r.size, mtime: r.mtime,
                      autoVerdict: r.state,
                      override: r.override || null,
                      reasons: ( r.reasons || [] ).slice(),
                      outcome: null, detail: "" } );
   }
   return { entries: entries, created: Date.now() };
};

Frames.manifestPending = function( manifest )
{
   var out = [];
   for ( var i = 0; i < manifest.entries.length; ++i )
      if ( manifest.entries[i].outcome == null )
         out.push( manifest.entries[i] );
   return out;
};

Frames.recordOutcome = function( manifest, path, outcome, detail )
{
   for ( var i = 0; i < manifest.entries.length; ++i )
      if ( manifest.entries[i].path == path )
      {
         manifest.entries[i].outcome = outcome;
         manifest.entries[i].detail = detail || "";
         return;
      }
};

/*
 * Identity is the digest. Path, size and modification time are all
 * preservable by a replacement, so none of them authorises a deletion.
 *
 * This does NOT close the window between the check and the unlink -- nothing
 * in PJSR locks a file. It narrows it to microseconds; the audit log is what
 * survives if something slips through.
 */
Frames.identityMatches = function( entry, current )
{
   return current != null && entry != null &&
          current.digest === entry.digest &&
          current.size === entry.size;
};

Frames.outputName = function( path )
{
   var i = Math.max( path.lastIndexOf( "/" ), path.lastIndexOf( "\\" ) );
   return ( i < 0 ) ? path : path.substring( i + 1 );
};

/*
 * The destination mapping is computed BEFORE anything is written.
 *
 * Export success has to be well defined even though no original is deleted,
 * because otherwise "it worked" is a guess. Two sources sharing an output
 * name abort the channel rather than silently overwriting one with the
 * other, and a destination inside a source directory is refused outright:
 * it makes "the output" and "the original" the same file.
 *
 * The output EXTENSION is part of the key, not decoration. Converting on
 * output manufactures collisions that the source names do not show -- a.fit
 * and a.xisf are distinct until both become a.xisf -- and that is precisely
 * the pair a mapping built from source names alone would miss.
 *
 * This lives in Frames rather than FrameSelector because it is pure and the
 * collision rule is what most needs testing; node never loads FrameSelector.
 */
Frames.outputMapping = function( approved, destination, extension )
{
   var mapping = {}, taken = {}, collisions = [], aliased = false;
   for ( var i = 0; i < approved.length; ++i )
   {
      var src = approved[i];
      if ( File.extractDirectory( src ) == destination )
         aliased = true;
      var name = extension ? File.extractName( src ) + extension
                           : Frames.outputName( src );
      if ( taken[name] )
         collisions.push( name );
      taken[name] = true;
      mapping[src] = destination + "/" + name;
   }
   return { mapping: mapping, collisions: collisions, aliased: aliased };
};
