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
Frames.MEASURE_VERSION = "v2";   // v2: adds background and SNR estimate

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
 *
 * 9 and 10 are WBPP's iSNREstimate and iMedian (BPP-SubframeAnalyzer.js
 * 487-488), confirmed on a live light frame; see verified-parameters.md.
 * Both are optional here: shown or advisory, never deciding a deletion.
 */
Frames.COL = { path: 3, fwhm: 5, eccentricity: 6, snr: 9, median: 10,
               noise: 12, stars: 14, psfSNR: 28 };

Frames.metricsFromRow = function( row )
{
   return { path:         row[Frames.COL.path],
            fwhm:         row[Frames.COL.fwhm],
            eccentricity: row[Frames.COL.eccentricity],
            noise:        row[Frames.COL.noise],
            stars:        row[Frames.COL.stars],
            psfSNR:       row[Frames.COL.psfSNR],
            background:   row[Frames.COL.median],
            snrWeight:    row[Frames.COL.snr] };
};

/*
 * Figures that are SHOWN (SNR) or feed an advisory flag (background) but
 * decide nothing. A problem with one of them blanks that figure; it never
 * abandons a channel the way meaningProblems does.
 */
Frames.OPTIONAL = [ "background", "snrWeight" ];

Frames.optionalOk = function( v )
{
   return typeof v == "number" && isFinite( v ) && v >= 0;
};

/* Schema check, first row only: which optional columns are not usable. */
Frames.optionalProblems = function( m )
{
   var bad = [];
   for ( var i = 0; i < Frames.OPTIONAL.length; ++i )
      if ( !Frames.optionalOk( m[Frames.OPTIONAL[i]] ) )
         bad.push( Frames.OPTIONAL[i] );
   return bad;
};

/*
 * Two scopes. `disabled` names fields the schema check failed on: null for
 * every row. Otherwise one bad reading is null for that row alone.
 */
Frames.sanitizeOptional = function( m, disabled )
{
   for ( var i = 0; i < Frames.OPTIONAL.length; ++i )
   {
      var f = Frames.OPTIONAL[i];
      if ( ( disabled && disabled[f] ) || !Frames.optionalOk( m[f] ) )
         m[f] = null;
   }
   return m;
};

/*
 * Stored WITHOUT the path. The key is the digest, so identical bytes sitting
 * somewhere else must not come back naming the first file.
 */
Frames.STORED_KEYS = [ "fwhm", "eccentricity", "noise", "stars", "psfSNR",
                       "background", "snrWeight" ];

Frames.storedMetrics = function( m )
{
   var out = {};
   for ( var i = 0; i < Frames.STORED_KEYS.length; ++i )
   {
      var k = Frames.STORED_KEYS[i];
      out[k] = ( m[k] === undefined ) ? null : m[k];
   }
   return out;
};

/*
 * A cached entry is usable when it has every key. A null optional is a
 * cached "unavailable", not a miss -- otherwise a frame whose background
 * cannot be read would be re-measured on every scan, forever.
 */
Frames.cacheEntryUsable = function( e )
{
   if ( e == null || typeof e != "object" )
      return false;
   for ( var i = 0; i < Frames.STORED_KEYS.length; ++i )
      if ( !Object.prototype.hasOwnProperty.call( e, Frames.STORED_KEYS[i] ) )
         return false;
   return true;
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
 * A clip on median and MAD is scale-invariant, so it drops roughly the same
 * FRACTION however good the night was. That is right for "drop this night's
 * worst" and wrong for "drop frames that are bad in absolute terms", and the
 * two cannot be combined on one metric -- a frame the relative gate rejects
 * cannot be rescued by also passing a ceiling.
 *
 * So the choice is made PER METRIC: a limit typed for a metric replaces its
 * relative gate, and a metric with no typed limit keeps it. Typing a limit
 * on every metric is how an entire good night is kept.
 */

Frames.defaultSettings = function()
{
   return { /*
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
                        eccentricity: "eccentricity", stars: "stars",
                        snrWeight: "SNR estimate" };

/* The same metrics as column headings, where width is scarce. */
Frames.METRIC_HEADING = { psfSNR: "PSF SNR", fwhm: "FWHM",
                          eccentricity: "ecc", stars: "stars", snrWeight: "SNR" };

/*
 * What the table and the plot show, in column order. Wider than METRICS:
 * SNR is shown and plotted but never gated, weighted or scored, so it must
 * not be in the list that drives those.
 */
Frames.DISPLAY_METRICS = [ "psfSNR", "snrWeight", "fwhm", "eccentricity", "stars" ];

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
   .concat( Frames.DISPLAY_METRICS.map( function( m ) { return Frames.METRIC_HEADING[m]; } ) )
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
   var open = { lo: null, hi: null };
   if ( settings.gating != null && settings.gating[metric] === false )
      return open;                         // unticked: nothing cuts
   var lim = settings.limits ? settings.limits[metric] : null;
   // A typed limit REPLACES the relative gate for its metric.
   return ( lim != null ) ? Frames.narrowBand( open, lim )
                          : Frames.relativeBand( metric, gates );
};

/* The side an active relative gate cuts from; the other end is open. */
Frames.relativeBand = function( metric, gates )
{
   var g = gates ? gates[metric] : null;
   if ( g == null || !g.active )
      return { lo: null, hi: null };
   return ( Frames.WORSE_WHEN[metric] == "higher" ) ? { lo: null, hi: g.limit }
                                                    : { lo: g.limit, hi: null };
};

/* A band narrowed by a limit's ends; the tighter end wins on each side. */
Frames.narrowBand = function( band, lim )
{
   if ( lim == null )
      return band;
   var lo = band.lo, hi = band.hi;
   if ( lim.lo != null )
      lo = ( lo == null ) ? lim.lo : Math.max( lo, lim.lo );
   if ( lim.hi != null )
      hi = ( hi == null ) ? lim.hi : Math.min( hi, lim.hi );
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
   var i = Frames.DISPLAY_METRICS.indexOf( metric );
   return ( i < 0 ) ? null : i + 1;
};

/* The trailing column, by the same rule. */
Frames.SCORE_COLUMN = Frames.DISPLAY_METRICS.length + 1;

/* A measurement as the table shows it: "-" when there is none. */
Frames.displayValue = function( metric, value )
{
   if ( value == null || !isFinite( value ) )
      return "-";
   return ( metric == "stars" ) ? String( Math.round( value ) ) : Frames.round( value );
};

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

/*
 * Rejections from a hard limit: only a CONFIGURED limit on a CHECKED
 * criterion can reject. An omitted gating map means every criterion is on,
 * which is what the metric-level tests assume.
 */
Frames.absoluteFailures = function( metrics, limits, gating )
{
   var out = [];
   for ( var a = 0; a < Frames.METRICS.length; ++a )
   {
      var an = Frames.METRICS[a], lim = limits[an];
      if ( lim == null || ( gating != null && !gating[an] ) )
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

Frames.absoluteReasons = function( metrics, limits, gating )
{
   return Frames.absoluteFailures( metrics, limits, gating ).map(
      function( f ) { return f.text; } );
};

/*
 * The gates that still apply: a metric with a typed limit is judged on that
 * limit alone, so its relative gate is set aside.
 */
Frames.autoGates = function( gates, limits )
{
   var out = {}, keys = Object.keys( gates || {} );
   for ( var i = 0; i < keys.length; ++i )
   {
      var g = gates[keys[i]];
      out[keys[i]] = ( limits && limits[keys[i]] != null )
         ? { active: false, limit: null, median: g.median, sigma: g.sigma,
             reason: "replaced by a typed limit" }
         : g;
   }
   return out;
};

/*
 * The verdict, per metric, so nothing is left to infer: a TICKED metric
 * with a typed limit is rejected iff it fails that limit; a ticked metric
 * without one is rejected iff its ACTIVE relative gate fails; an unticked
 * metric rejects nothing (its gate is inactive, its limit skipped).
 */
Frames.verdict = function( metrics, gates, settings )
{
   if ( !Frames.frameValid( metrics ) )
      return { state: Frames.STATE.UNMEASURABLE,
               reasons: [ "a metric is missing or not positive" ] };

   var failures = Frames.relativeFailures( metrics, Frames.autoGates( gates, settings.limits ) )
      .concat( Frames.absoluteFailures( metrics, settings.limits || {}, settings.gating ) );

   /*
    * `failing` is deduplicated: a limit with both ends can fail on either,
    * and the review would otherwise be told to colour one column twice.
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

/* ---- the criteria panel's arithmetic -------------------------------- */

Frames.OPERATOR = { fwhm: "<=", eccentricity: "<=", stars: ">=", psfSNR: ">=" };
Frames.LIMIT_DECIMALS = { fwhm: 2, eccentricity: 3, stars: 0, psfSNR: 2 };
/* Left to right in the panel, as SubframeStudio lays them out. */
Frames.CRITERIA_ORDER = [ "fwhm", "eccentricity", "stars", "psfSNR" ];

/*
 * A typed threshold: a positive finite number, null for blank (no
 * threshold), undefined for anything else -- which the dialog answers by
 * putting the stored value back. A comma is not accepted as a decimal
 * point: guessing wrong would move a limit by a factor of a thousand.
 */
Frames.parseLimit = function( text )
{
   var t = String( text == null ? "" : text ).trim();
   if ( t === "" )
      return null;
   if ( !/^[0-9]*\.?[0-9]+(e[+-]?[0-9]+)?$/i.test( t ) )
      return undefined;
   var v = parseFloat( t );
   return ( isFinite( v ) && v > 0 ) ? v : undefined;
};

Frames.formatLimit = function( metric, value )
{
   if ( value == null || !isFinite( value ) )
      return "";
   var d = Frames.LIMIT_DECIMALS[metric];
   return value.toFixed( d == null ? 2 : d );
};

/* The stored worse-side bound, or null. */
Frames.limitValue = function( settings, metric )
{
   var lim = settings.limits ? settings.limits[metric] : null;
   if ( lim == null )
      return null;
   var v = ( Frames.WORSE_WHEN[metric] == "higher" ) ? lim.hi : lim.lo;
   return ( v == null ) ? null : v;
};

/*
 * Settings with one metric's limit typed, or cleared back to automatic
 * with null. The whole entry is replaced, so no bound on the other side
 * can survive. Returns a copy.
 */
Frames.withLimit = function( settings, metric, value )
{
   var out = Frames.copyOf( settings );
   out.limits = Frames.copyOf( settings.limits );
   if ( value == null )
      delete out.limits[metric];
   else
      out.limits[metric] = ( Frames.WORSE_WHEN[metric] == "higher" ) ? { hi: value }
                                                                    : { lo: value };
   return out;
};

/*
 * What a criterion's box shows: the typed limit, or -- automatic -- the
 * limit k times the spread gives, which the box shows greyed. `value` is
 * null when there is neither: no typed limit and no usable gate.
 */
Frames.displayLimit = function( metric, gates, settings )
{
   var typed = Frames.limitValue( settings, metric );
   if ( typed != null )
      return { value: typed, auto: false };
   var g = gates ? gates[metric] : null;
   return { value: ( g != null && g.active ) ? g.limit : null, auto: true };
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

/*
 * Whether Run leaves this frame out of its result -- the one rule behind
 * the filmstrip's cross, the preview's red tag and the keep counter.
 *
 * Mirrors the two things Run does. Culling in place deletes an enabled
 * channel's rejected frames (committableRows + buildManifest). Copying out
 * copies only an enabled channel's frames that are not rejected
 * (approvedPaths + commitCopy), so a switched-off channel is left out
 * entirely. finalState already applies overrides: a condemned frame is
 * left out even when it could not be measured.
 */
Frames.leftOut = function( row, channelEnabled, copying )
{
   var rejected = Frames.finalState( row.state, row.override ) == Frames.STATE.REJECTED;
   return copying ? ( !channelEnabled || rejected ) : ( channelEnabled && rejected );
};

Frames.keepCount = function( rows, channelEnabled, copying )
{
   var keep = 0;
   for ( var i = 0; i < rows.length; ++i )
      if ( !Frames.leftOut( rows[i], channelEnabled, copying ) )
         ++keep;
   return { keep: keep, total: rows.length };
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
   var names = paths.map( function( p ) { return Frames.outputName( p ); } );
   if ( names.length < 2 )
      return names;
   // Back up to a separator so the remainder starts at a whole token.
   var cut = Frames.lastSeparatorIn( Frames.commonPrefix( names ) );
   if ( cut < 0 )
      return names;
   return names.map( function( name )
   {
      // Never hand back nothing: a name identical to the prefix keeps its
      // whole self rather than becoming an empty cell.
      var short_ = name.substring( cut + 1 );
      return short_.length ? short_ : name;
   } );
};

/* The longest start every name shares. */
Frames.commonPrefix = function( names )
{
   var prefix = names[0];
   for ( var n = 1; n < names.length && prefix.length > 0; ++n )
   {
      var j = 0, other = names[n];
      while ( j < prefix.length && j < other.length && prefix.charAt( j ) == other.charAt( j ) )
         ++j;
      prefix = prefix.substring( 0, j );
   }
   return prefix;
};

/* Index of the last name separator in a string, or -1. */
Frames.lastSeparatorIn = function( text )
{
   var cut = -1;
   for ( var k = 0; k < text.length; ++k )
      if ( Frames.SEPARATORS.indexOf( text.charAt( k ) ) >= 0 )
         cut = k;
   return cut;
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

/*
 * Why the output folder must not be emptied, or null.
 *
 * Copying out empties the folder first -- recursively, without asking -- so
 * a folder that IS, or CONTAINS, the folder of any frame being copied would
 * take the originals with it. The filesystem root and the home folder are
 * refused outright: a destination left pointing there is a mistake, never
 * an output folder.
 */
Frames.emptyRefusal = function( sourcePaths, destination, home )
{
   if ( destination == null || String( destination ).trim() === "" )
      return "no output folder chosen";
   var dest = Frames.withoutTrailingSlash( destination );
   if ( dest === "" )
      return "refusing to empty the filesystem root";
   if ( home != null && dest === Frames.withoutTrailingSlash( home ) )
      return "refusing to empty the home folder";
   for ( var i = 0; i < sourcePaths.length; ++i )
   {
      var dir = Frames.withoutTrailingSlash( File.extractDirectory( sourcePaths[i] ) );
      if ( dir === dest || dir.indexOf( dest + "/" ) === 0 )
         return "the output folder contains the frames being copied (" + dir + ")";
   }
   return null;
};

Frames.withoutTrailingSlash = function( path )
{
   return String( path ).replace( /\/+$/, "" );
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

/* ---- a channel: its cohort, its review, its verdicts ---------------- */

/*
 * A channel holds its COHORT -- the entries and the measurements taken at
 * scan time -- separately from the rows, which are the review. Medians and
 * MADs are computed from the cohort every time, never from whatever
 * survived a partial run, because recomputing on survivors is iterative
 * clipping.
 */
Frames.newChannel = function( key, entries, metrics, problems )
{
   var rows = [];
   for ( var i = 0; i < entries.length; ++i )
   {
      var e = entries[i];
      rows.push( { path: e.path, channel: key,
                   metrics: metrics[e.path] || null,
                   digest: e.identity ? e.identity.digest : null,
                   size: e.identity ? e.identity.size : 0,
                   mtime: e.identity ? e.identity.mtime : 0,
                   state: Frames.STATE.UNMEASURABLE, reasons: [],
                   override: null, score: null } );
   }
   var ch = { key: key, entries: entries, metrics: metrics,
              problems: problems || [],
              settings: Frames.defaultSettings(), rows: rows };
   var list = [];
   for ( var f = 0; f < rows.length; ++f )
      list.push( rows[f].metrics );
   /*
    * Computed once, from the measurements: knobs do not move a flag.
    * Frames that are not comparable are not flagged at all -- a raw
    * background compared across unlike frames means nothing.
    */
   ch.flags = Frames.anomalyFlags( list, ch.problems.length == 0 &&
                                         Frames.autoRejectAllowed( key ) );
   return ch;
};

/*
 * Recompute every verdict in a channel from its cohort.
 *
 * Overrides are left standing: changing k or a weight must not discard the
 * one judgement in the dialog that was made by looking at the frame.
 */
Frames.recompute = function( ch )
{
   var cohort = [];
   for ( var i = 0; i < ch.rows.length; ++i )
      if ( ch.rows[i].metrics != null )
         cohort.push( ch.rows[i].metrics );

   var gates = Frames.relativeGates( cohort, ch.settings.k, ch.settings.gating );
   /*
    * Kept on the channel: the plot draws the band these define, and
    * recomputing them there would be a second place for k to be read.
    */
   ch.gates = gates;
   var meds = Frames.medians( cohort );
   /*
    * A disabled channel is untouched, and a group with no
    * readable FILTER is never auto-rejected -- there is no evidence its
    * frames belong together, so "this night's worst" means nothing over
    * them. Both still get scores, so the table is readable.
    */
   var mayReject = ch.settings.enabled && Frames.autoRejectAllowed( ch.key );

   for ( var r = 0; r < ch.rows.length; ++r )
      Frames.judgeRow( ch.rows[r], gates, meds, ch.settings, mayReject );
   return ch;
};

/* One row's score and verdict, against the channel's gates and settings. */
Frames.judgeRow = function( row, gates, meds, settings, mayReject )
{
   if ( row.metrics == null )
   {
      row.score = null;
      row.state = Frames.STATE.UNMEASURABLE;
      row.reasons = [ "no measurement" ];
      row.failing = [];
      return;
   }
   row.score = Frames.score( row.metrics, meds, settings.weights );
   var v = Frames.verdict( row.metrics, gates, settings );
   var suppressed = !mayReject && v.state == Frames.STATE.REJECTED;
   row.state   = suppressed ? Frames.STATE.APPROVED : v.state;
   row.reasons = suppressed ? [] : v.reasons;
   /*
    * Cleared with the reasons when a rejection is suppressed: the channel
    * is not being acted on, so there is nothing to mark.
    */
   row.failing = suppressed ? [] : v.failing;
};

/* ---- anomaly flags ----------------------------------------------------
 *
 * Advisory, always on, and never a verdict: a flag does not reject, does
 * not change a state, does not reach the manifest. The tags name symptoms
 * -- "looks like" -- not diagnoses, and they are relative to the channel,
 * the way SubframeStudio's are.
 */
Frames.FLAG_K = 3;
Frames.FLAG_MIN_FRAMES = 5;
Frames.DROPPED_FRACTION = 0.1;
/*
 * CLOUD from background: this far above the channel's median, as a
 * fraction. Relative, not sigma: SubframeSelector's median moves by about
 * one 16-bit step between frames on a steady night (measured on four
 * channels, 2026-09-23), far under any spread floor, so a sigma rule never
 * ran -- and without the floor it would tag noise. A real cloud lifts the
 * background by tens of percent.
 */
Frames.CLOUD_BACKGROUND_RISE = 0.05;
Frames.FLAG_ORDER = [ "focus", "tracking", "cloud", "dropped" ];
Frames.FLAG_TAG = { focus: "FOCUS", tracking: "TRACKING", cloud: "CLOUD", dropped: "DROPPED" };
Frames.FLAG_BADGE = { focus: "F", tracking: "T", cloud: "C", dropped: "D" };
/* Summary order, as SubframeStudio words it. */
Frames.FLAG_SUMMARY_ORDER = [ "cloud", "focus", "tracking", "dropped" ];

/*
 * One metric's baseline over the channel: every row with a usable value --
 * positive, or non-negative for a star COUNT, where 0 is a real reading.
 * Null below FLAG_MIN_FRAMES values. `spread` says whether a sigma rule may
 * use it, with the same 1% floor the rejection gates use.
 */
Frames.flagBaseline = function( list, metric, allowZero )
{
   var v = [];
   for ( var i = 0; i < list.length; ++i )
   {
      var x = list[i] ? list[i][metric] : null;
      if ( typeof x == "number" && isFinite( x ) && ( allowZero ? x >= 0 : x > 0 ) )
         v.push( x );
   }
   if ( v.length < Frames.FLAG_MIN_FRAMES )
      return null;
   var med = Frames.median( v ), sd = Frames.sigma( v );
   return { median: med, sigma: sd,
            spread: sd > 0 && sd >= Frames.SIGMA_FLOOR_FRACTION*Math.abs( med ) };
};

Frames.isNumber = function( v ) { return typeof v == "number" && isFinite( v ); };

/* Above the channel by more than FLAG_K sigma, on a baseline with spread. */
Frames.flagHigh = function( base, v )
{
   return base != null && base.spread && Frames.isNumber( v ) &&
          v > base.median + Frames.FLAG_K*base.sigma;
};

/* One frame's flags against the channel's baselines, in FLAG_ORDER. */
Frames.frameFlags = function( m, b )
{
   var st = b.stars;
   var dropped = st != null && Frames.isNumber( m.stars ) &&
                 m.stars < Frames.DROPPED_FRACTION*st.median;
   // A dropped frame is not ALSO cloud for the same low star count.
   var fewStars = !dropped && st != null && st.spread && Frames.isNumber( m.stars ) &&
                  m.stars < st.median - Frames.FLAG_K*st.sigma;
   var bright = b.background != null && Frames.isNumber( m.background ) &&
                m.background > b.background.median*( 1 + Frames.CLOUD_BACKGROUND_RISE );
   var f = { focus: Frames.flagHigh( b.fwhm, m.fwhm ),
             tracking: Frames.flagHigh( b.eccentricity, m.eccentricity ),
             cloud: bright || fewStars,
             dropped: dropped };
   return Frames.FLAG_ORDER.filter( function( k ) { return f[k]; } );
};

Frames.anomalyFlags = function( list, comparable )
{
   var out = [];
   for ( var n = 0; n < list.length; ++n )
      out.push( [] );
   if ( !comparable )
      return out;
   var b = { fwhm: Frames.flagBaseline( list, "fwhm", false ),
             eccentricity: Frames.flagBaseline( list, "eccentricity", false ),
             background: Frames.flagBaseline( list, "background", false ),
             stars: Frames.flagBaseline( list, "stars", true ) };
   for ( var i = 0; i < list.length; ++i )
      if ( list[i] != null )
         out[i] = Frames.frameFlags( list[i], b );
   return out;
};

/* "N flagged: a cloud, b focus", N counting frames. "" when none. */
Frames.flagSummary = function( flagsList )
{
   var frames = 0, per = {};
   for ( var i = 0; i < flagsList.length; ++i )
   {
      if ( flagsList[i].length )
         ++frames;
      for ( var j = 0; j < flagsList[i].length; ++j )
         per[flagsList[i][j]] = ( per[flagsList[i][j]] || 0 ) + 1;
   }
   if ( frames == 0 )
      return "";
   var parts = [];
   for ( var k = 0; k < Frames.FLAG_SUMMARY_ORDER.length; ++k )
   {
      var key = Frames.FLAG_SUMMARY_ORDER[k];
      if ( per[key] )
         parts.push( per[key] + " " + key );
   }
   return frames + " flagged: " + parts.join( ", " );
};

/* A row's tooltip: the path, the verdict and why, then what it looks like. */
Frames.rowTooltip = function( row, flags )
{
   var mark = ( row.override == Frames.OVERRIDE.RESCUED ) ? " (rescued)"
            : ( row.override == Frames.OVERRIDE.CONDEMNED ) ? " (condemned)" : "";
   var fl = flags || [];
   return row.path + "\n" + Frames.finalState( row.state, row.override ) + mark +
          ( row.reasons.length ? ": " + row.reasons.join( "; " ) : "" ) +
          ( fl.length ? "\nlooks like: " + fl.map( function( f )
               { return Frames.FLAG_TAG[f].toLowerCase(); } ).join( ", " ) : "" );
};

/* The preview's tags, top to bottom: the red one, then each flag alone. */
Frames.frameTags = function( row, flags, channelEnabled, copying )
{
   var tags = [];
   if ( Frames.leftOut( row, channelEnabled, copying ) )
      tags.push( { text: ( Frames.finalState( row.state, row.override ) == Frames.STATE.REJECTED )
                         ? "REJECTED" : "CHANNEL OFF", kind: "reject" } );
   for ( var i = 0; i < ( flags || [] ).length; ++i )
      tags.push( { text: Frames.FLAG_TAG[flags[i]], kind: "flag" } );
   return tags;
};

/* ---- the filmstrip's arithmetic ------------------------------------- */

/*
 * The first tile the strip shows. When the selection is already on screen
 * in the current view (`current`), the view does not move: clicking a tile
 * must leave the clicked frame under the mouse, and re-centring on every
 * click slid a different frame under it. Otherwise -- a selection made in
 * the table or the plot, off screen -- it is centred, clamped to the ends.
 */
Frames.stripFirst = function( count, selected, visible, current )
{
   if ( count <= visible )
      return 0;
   var last = count - visible;
   var s = ( selected < 0 ) ? 0 : selected;
   if ( current != null && current >= 0 && current <= last &&
        s >= current && s < current + visible )
      return current;
   return Math.max( 0, Math.min( s - Math.floor( visible/2 ), last ) );
};

/*
 * The frame a click at x lands on, or -1. Only a drawn tile counts: the
 * space after the last whole tile, and anything past the channel's end,
 * picks nothing -- a click there used to select a frame nobody could see.
 */
Frames.tileAt = function( x, tileW, visible, first, count )
{
   if ( x < 0 )
      return -1;
   var slot = Math.floor( x/tileW );
   if ( slot >= visible )
      return -1;
   var i = first + slot;
   return ( i < count ) ? i : -1;
};

/*
 * The order thumbnails are loaded in: the visible tiles first, nearest the
 * selection first, then the rest of the channel outward from it. Ties go
 * to the lower index. Every index appears exactly once.
 */
Frames.thumbnailOrder = function( count, selected, first, visible )
{
   var s = ( selected < 0 ) ? first : selected;
   function byDistance( a, b )
   {
      var d = Math.abs( a - s ) - Math.abs( b - s );
      return d != 0 ? d : a - b;
   }
   var inView = [], rest = [];
   for ( var i = 0; i < count; ++i )
      ( i >= first && i < first + visible ? inView : rest ).push( i );
   return inView.sort( byDistance ).concat( rest.sort( byDistance ) );
};
