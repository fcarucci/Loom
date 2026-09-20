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
/*
 * `gating` names the metrics allowed to reject. A metric left out is
 * measured, scored and plotted as before, but its gate is inactive -- so
 * it cannot condemn a frame, and the plot draws no band for it. Omitting
 * the argument gates on everything, which is what the metric-level tests
 * below assume.
 */
Frames.relativeGates = function( frames, k, gating )
{
   var valid = Frames.validOnly( frames );
   if ( valid.length < Frames.MIN_FRAMES )
      return {};

   var gates = {};
   for ( var m = 0; m < Frames.METRICS.length; ++m )
   {
      var name = Frames.METRICS[m];
      if ( gating != null && !gating[name] )
      {
         gates[name] = { active: false, limit: null,
                         median: Frames.median( Frames.columnOf( valid, name ) ),
                         sigma: null, reason: "not used as a gate" };
         continue;
      }
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
 * Which metrics may REJECT a frame, as opposed to merely rank it.
 *
 * PSF SNR is off, and that is the whole point of the distinction.
 * Integration weights each frame by its signal -- WBPP's default is PSF
 * Signal Weight -- so a faint frame already contributes in proportion to
 * what it is worth. With that weighting the stack's SNR goes as the root
 * of the sum of the frames' squared SNRs, which every frame with signal
 * increases: deleting a faint one throws away signal that was already
 * being discounted correctly.
 *
 * FWHM and eccentricity are on because no weight repairs them. The
 * stacked PSF is a weighted blend of the frames' own, and weight follows
 * signal, not sharpness -- so a soft frame with good SNR earns a HIGH
 * weight and blurs the result. That is what rejection is for.
 *
 * Star count is on as the evidence of transparency that FWHM does not
 * carry: cloud takes stars out of a frame without widening the ones that
 * remain, and it brings gradients that do not average away.
 *
 * SNR stays available, because a frame far below the rest usually means
 * something was wrong rather than merely dim.
 */
Frames.DEFAULT_GATING = { psfSNR: false, fwhm: true, eccentricity: true,
                          stars: true };

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
            /*
             * Per channel, not per folder. A night's L and its Ha are not
             * the same population -- one can be tight and the other ragged
             * -- and one preset over both either spares the bad channel or
             * cuts into the good one.
             */
            preset: Frames.DEFAULT_PRESET,
            k: Frames.PRESETS[Frames.DEFAULT_PRESET],
            kEdited: false,
            weights: { psfSNR: Frames.DEFAULT_WEIGHTS.psfSNR,
                       fwhm: Frames.DEFAULT_WEIGHTS.fwhm,
                       eccentricity: Frames.DEFAULT_WEIGHTS.eccentricity,
                       stars: Frames.DEFAULT_WEIGHTS.stars },
            limits: {},              // metric -> { lo?, hi? }
            /*
             * Copied, never shared: a preset that handed out the same
             * object gave every channel one set of knobs, and changing a
             * channel changed them all. That bug has been here once.
             */
            gating: { psfSNR: Frames.DEFAULT_GATING.psfSNR,
                      fwhm: Frames.DEFAULT_GATING.fwhm,
                      eccentricity: Frames.DEFAULT_GATING.eccentricity,
                      stars: Frames.DEFAULT_GATING.stars },
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
   if ( settings.gating != null )
      out.gating = Frames.copyOf( settings.gating );
   out.preset = preset;
   if ( !settings.kEdited && Frames.PRESETS[preset] != null )
      out.k = Frames.PRESETS[preset];
   return out;
};

Frames.METRIC_LABEL = { psfSNR: "PSF SNR", fwhm: "FWHM",
                        eccentricity: "eccentricity", stars: "stars" };

/* The same metrics as column headings, where width is scarce. */
Frames.METRIC_HEADING = { psfSNR: "PSF SNR", fwhm: "FWHM",
                          eccentricity: "ecc", stars: "stars" };

/*
 * The review's columns, derived from METRICS rather than written out
 * beside them. The verdict names the metrics that condemned a frame and
 * the table colours those cells, so the two orders have to agree; deriving
 * both from one list is what stops them drifting apart.
 */
/*
 * No verdict column. It repeated what the row already says -- a red cross
 * by the name, the offending measurement in red -- at the cost of the
 * width the names needed. The wording behind it, which is worth having
 * when a frame is borderline, is the row's tooltip instead.
 */
Frames.FRAME_COLUMNS = [ "Frame" ]
   .concat( Frames.METRICS.map( function( m ) { return Frames.METRIC_HEADING[m]; } ) )
   .concat( [ "score" ] );

/*
 * The range a frame's measurement may sit in and still be kept.
 *
 * Shown as a band behind the plot, so "why is that one out" is answered
 * by looking rather than by reading the verdict column. A null end is
 * unbounded: a relative gate only ever cuts from one side, because only
 * one direction of a metric is worse.
 */
Frames.acceptedBand = function( metric, gates, settings )
{
   var lo = null, hi = null;

   if ( settings.mode != Frames.MODE.ABSOLUTE )
   {
      var g = gates ? gates[metric] : null;
      if ( g != null && g.active )
      {
         if ( Frames.WORSE_WHEN[metric] == "higher" )
            hi = g.limit;
         else
            lo = g.limit;
      }
   }
   if ( settings.mode != Frames.MODE.RELATIVE )
   {
      var lim = settings.limits ? settings.limits[metric] : null;
      if ( lim != null )
      {
         // The tighter of the two wins: in BOTH mode a frame has to pass
         // the gate AND the limit, so the band is their intersection.
         if ( lim.lo != null )
            lo = ( lo == null ) ? lim.lo : Math.max( lo, lim.lo );
         if ( lim.hi != null )
            hi = ( hi == null ) ? lim.hi : Math.min( hi, lim.hi );
      }
   }
   return { lo: lo, hi: hi };
};

/*
 * The point nearest a click, by horizontal position alone.
 *
 * Only x matters: the points are one per frame along the axis, so the
 * nearest column IS the frame meant -- and requiring the click to land
 * near the marker vertically would make a frame at the top of the plot
 * harder to pick than one in the middle.
 *
 * Returns -1 when there is nothing to pick or the click is outside.
 */
Frames.pointAt = function( x, left, plotWidth, count )
{
   if ( count <= 0 || plotWidth <= 0 )
      return -1;
   /*
    * Rejected BEFORE rounding, not after. Rounding pulls an outside click
    * back into range -- a click in the left margin, where the axis
    * numbers are, rounded to 0 and selected the first frame.
    */
   if ( x < left || x > left + plotWidth )
      return -1;
   if ( count == 1 )
      return 0;
   var t = ( x - left ) / plotWidth;
   return Math.max( 0, Math.min( count - 1, Math.round( t * ( count - 1 ) ) ) );
};

/*
 * Which of a set of labels can be drawn without landing on each other.
 *
 * The plot labels the axis at top and bottom and the band's own limits,
 * and a limit near either extreme puts two numbers in the same few
 * pixels -- unreadable, and worse than showing one. Entries are given in
 * order of importance and the first to claim a position keeps it: a
 * threshold outranks the extreme it sits near, because the extreme is
 * only the data's edge while the threshold is the decision.
 */
Frames.spacedLabels = function( entries, gap )
{
   var out = [], used = [];
   var g = ( gap == null ) ? 12 : gap;
   for ( var i = 0; i < entries.length; ++i )
   {
      var y = entries[i].y, clear = true;
      for ( var u = 0; u < used.length; ++u )
         if ( Math.abs( used[u] - y ) < g )
         {
            clear = false;
            break;
         }
      if ( clear )
      {
         used.push( y );
         out.push( entries[i] );
      }
   }
   return out;
};

/*
 * Vertical extent of the plot: every point visible, the band's edges
 * visible, and a margin so nothing is drawn on the frame itself. A band
 * edge far outside the data is deliberately included -- a gate nothing
 * comes close to is worth seeing as exactly that.
 */
Frames.plotBounds = function( values, band )
{
   var lo = null, hi = null;
   function take( v )
   {
      if ( v == null || !isFinite( v ) )
         return;
      lo = ( lo == null ) ? v : Math.min( lo, v );
      hi = ( hi == null ) ? v : Math.max( hi, v );
   }
   for ( var i = 0; i < values.length; ++i )
      take( values[i] );
   if ( band != null )
   {
      take( band.lo );
      take( band.hi );
    }
   if ( lo == null )
      return { lo: 0, hi: 1 };
   if ( hi == lo )
   {
      // A channel whose frames all measure the same is not an error; it
      // still has to occupy a height rather than collapse to a line.
      var d = ( hi == 0 ) ? 0.5 : Math.abs( hi ) * 0.05;
      return { lo: lo - d, hi: hi + d };
   }
   var pad = ( hi - lo ) * 0.08;
   return { lo: lo - pad, hi: hi + pad };
};

/* Column showing `metric`, or null when it has none. */
Frames.metricColumn = function( metric )
{
   var i = Frames.METRICS.indexOf( metric );
   return ( i < 0 ) ? null : i + 1;
};

/* The trailing column, by the same rule. */
Frames.SCORE_COLUMN = Frames.METRICS.length + 1;

/* Rejections from this channel's own spread: only an ACTIVE gate can reject. */
/*
 * A failure names the metric it came from, not only its sentence.
 *
 * The review colours the offending measurement, and deciding which column
 * that is by reading the sentence back would tie the display to the exact
 * wording of a message. The metric travels with the text instead.
 */
Frames.relativeFailures = function( metrics, gates )
{
   var out = [];
   for ( var m = 0; m < Frames.METRICS.length; ++m )
   {
      var name = Frames.METRICS[m], g = gates[name];
      if ( g == null || !g.active )
         continue;
      var v = metrics[name];
      var bad = ( Frames.WORSE_WHEN[name] == "higher" ) ? v > g.limit : v < g.limit;
      if ( bad )
         out.push( { metric: name,
                     text: Frames.METRIC_LABEL[name] + " " + Frames.round( v ) +
                           ", median " + Frames.round( g.median ) +
                           ", limit " + Frames.round( g.limit ) } );
   }
   return out;
};

Frames.relativeReasons = function( metrics, gates )
{
   return Frames.relativeFailures( metrics, gates ).map(
      function( f ) { return f.text; } );
};

/* Rejections from a hard limit: only a CONFIGURED limit can reject. */
Frames.absoluteFailures = function( metrics, limits )
{
   var out = [];
   for ( var a = 0; a < Frames.METRICS.length; ++a )
   {
      var an = Frames.METRICS[a], lim = limits[an];
      if ( lim == null )
         continue;
      if ( lim.hi != null && metrics[an] > lim.hi )
         out.push( { metric: an,
                     text: Frames.METRIC_LABEL[an] + " " + Frames.round( metrics[an] ) +
                           " above the limit of " + Frames.round( lim.hi ) } );
      if ( lim.lo != null && metrics[an] < lim.lo )
         out.push( { metric: an,
                     text: Frames.METRIC_LABEL[an] + " " + Frames.round( metrics[an] ) +
                           " below the limit of " + Frames.round( lim.lo ) } );
   }
   return out;
};

Frames.absoluteReasons = function( metrics, limits )
{
   return Frames.absoluteFailures( metrics, limits ).map(
      function( f ) { return f.text; } );
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

   var failures = [];
   if ( settings.mode != Frames.MODE.ABSOLUTE )
      failures = failures.concat( Frames.relativeFailures( metrics, gates ) );
   if ( settings.mode != Frames.MODE.RELATIVE )
      failures = failures.concat( Frames.absoluteFailures( metrics, settings.limits ) );

   /*
    * `failing` is deduplicated: a metric can fail the relative gate and the
    * absolute limit at once in BOTH mode, and the review would otherwise be
    * told to colour the same column twice.
    */
   var reasons = [], failing = [], seen = Object.create( null );
   for ( var i = 0; i < failures.length; ++i )
   {
      reasons.push( failures[i].text );
      if ( !seen[failures[i].metric] )
      {
         seen[failures[i].metric] = true;
         failing.push( failures[i].metric );
      }
   }

   return { state: reasons.length ? Frames.STATE.REJECTED : Frames.STATE.APPROVED,
            reasons: reasons, failing: failing };
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
 * they are reported, and the figures are to be read knowing it. They are
 * not withheld: a tool that measures frames and then refuses to act on
 * its own measurements is an obstacle, not a safeguard.
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
    * A uniformly unknown calibration state is NOT reported.
    *
    * It was, on the argument that absence of evidence is not uniformity.
    * In practice a frame is "calibrated" only if its name ends _c or it
    * carries a readable HISTORY keyword, so an ordinary folder has every
    * frame unknown and every channel was flagged -- a warning that fires
    * on everything says nothing about anything.
    *
    * And the argument was weak: frames equally unknown are still being
    * compared like with like. A genuine mixture of raw and calibrated is
    * caught by the distinct() test above, which is evidence rather than
    * the lack of it.
    */

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

/*
 * The part of each name that actually differs.
 *
 * Subframes of one channel share everything but a timestamp and a
 * sequence number: target, exposure, binning, camera, filter, gain. Shown
 * whole in a column too narrow for them, the shared head is all that
 * fits and every row reads "Light_...a.xisf" -- identical, and useless
 * for telling one frame from another.
 *
 * Dropping the common prefix leaves exactly what identifies each frame.
 * The prefix is cut at the last separator inside it, so a name never
 * starts mid-token: with 0009 and 0010 present the raw common prefix ends
 * "..._00", and cutting there would show "09" and "10".
 *
 * Returns the full names unchanged when there is nothing to gain -- one
 * frame, or no shared head.
 */
Frames.SEPARATORS = "_-.";

Frames.shortNames = function( paths )
{
   var names = [];
   for ( var i = 0; i < paths.length; ++i )
      names.push( Frames.outputName( paths[i] ) );
   if ( names.length < 2 )
      return names;

   var prefix = names[0];
   for ( var n = 1; n < names.length && prefix.length > 0; ++n )
   {
      var j = 0, other = names[n];
      while ( j < prefix.length && j < other.length && prefix.charAt( j ) == other.charAt( j ) )
         ++j;
      prefix = prefix.substring( 0, j );
   }
   // Back up to a separator so the remainder starts at a whole token.
   var cut = -1;
   for ( var k = 0; k < prefix.length; ++k )
      if ( Frames.SEPARATORS.indexOf( prefix.charAt( k ) ) >= 0 )
         cut = k;
   if ( cut < 0 )
      return names;

   var out = [];
   for ( var m = 0; m < names.length; ++m )
   {
      var short_ = names[m].substring( cut + 1 );
      // Never hand back nothing: a name identical to the prefix keeps its
      // whole self rather than becoming an empty cell.
      out.push( short_.length ? short_ : names[m] );
   }
   return out;
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
/*
 * Is the chosen destination one of the folders the frames came from?
 *
 * The same test outputMapping calls `aliased`, exposed so the review can
 * say what will happen BEFORE Apply is pressed rather than refusing
 * afterwards. Choosing the input folder is not a mistake -- it is how you
 * ask for the frames to be culled where they are -- but it means
 * something entirely different from copying, and the dialog has to say so.
 */
/* Extension every output carries, whatever the input was. */
Frames.XISF = ".xisf";

/*
 * Which of these frames are not already XISF.
 *
 * Converting in place is a no-op for a folder of XISF, and saying so
 * costs nothing while running SubframeSelector over them to discover it
 * costs a pass over every file. A frame already in the target format is
 * not work to be done.
 */
Frames.needingXisf = function( paths )
{
   var out = [];
   for ( var i = 0; i < paths.length; ++i )
   {
      var e = File.extractExtension( paths[i] );
      if ( String( e ).toLowerCase() != Frames.XISF )
         out.push( paths[i] );
   }
   return out;
};

/* The frames grouped by the folder they live in. */
Frames.byDirectory = function( paths )
{
   var dirs = Object.create( null ), order = [];
   for ( var i = 0; i < paths.length; ++i )
   {
      var d = File.extractDirectory( paths[i] );
      if ( dirs[d] == null )
      {
         dirs[d] = [];
         order.push( d );
      }
      dirs[d].push( paths[i] );
   }
   return { dirs: dirs, order: order };
};

Frames.destinationIsSource = function( paths, destination )
{
   if ( destination == null )
      return false;
   for ( var i = 0; i < paths.length; ++i )
      if ( File.extractDirectory( paths[i] ) == destination )
         return true;
   return false;
};

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
