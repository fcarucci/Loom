#engine v8

#feature-id    Loom Fly-Through : Loom > Loom Fly-Through
#feature-info  Turns a finished astrophoto into a push-in video: the photo's \
               own stars move at their real Gaia distances.

#include <pjsr/UndoFlag.jsh>
#include <pjsr/DataType.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/ColorSpace.jsh>
#include <pjsr/SampleType.jsh>
#include <pjsr/ImageOp.jsh>

#ifndef LOOM_LIBS_INCLUDED
#include "lib/Util.js"
#include "lib/Cache.js"
#include "lib/Psb.js"
#include "lib/Steps.js"
#include "lib/Pipeline.js"
#include "lib/Frames.js"
#include "lib/Fly.js"
#include "lib/Sky.js"
#include "lib/Render.js"
#endif

function FlyThrough() {}

/*
 * A preset folder ready for a new clip: created, and emptied of OUR frame
 * files only -- a stale frame from a longer earlier render would otherwise
 * be picked up by ffmpeg's frame pattern. With keepFrames (a render being
 * resumed) only frames caught half-written go.
 */
FlyThrough.prepareFolder = function( folder, keepFrames )
{
   if ( !File.directoryExists( folder ) )
      File.createDirectory( folder, true );
   Steps.directoryEntries( folder ).filter( function( name )
   {
      return FlyThrough.isPartialFrame( name ) || ( !keepFrames && Fly.isFrameFile( name ) );
   } ).forEach( function( name )
   {
      try { File.remove( folder + "/" + name ); }
      catch ( e ) { Util.warn( "fly", "could not remove " + folder + "/" + name + ": " + e ); }
   } );
};

/*
 * The final render: for each preset, every frame as a 16-bit TIFF in
 * <dir>/<preset>/, then (unless cancelled) the video when opts.video and
 * an ffmpeg path are given; otherwise the exact command is returned.
 * `progress` = { onFrame( written, total, preset ), isCancelled(),
 * cancelAfter (the suite's hook) }. Cancel finishes the current frame and
 * keeps what was written.
 */
FlyThrough.renderFinal = function( scene, presets, opts, dir, progress )
{
   progress = progress || {};
   var specs = presets.map( function( id ) { return Fly.presetSpec( id, opts.orientation, opts.loop ); } ), res = { written: 0, reused: 0, cancelled: false, videos: [], commands: [], failed: [] };

   var total = specs.reduce( function( a, p ) { return a + Fly.frameCount( opts.duration, opts.fps, p.pingPong ); }, 0 );
   function cancelled()
   {
      return ( progress.cancelAfter != null && res.written >= progress.cancelAfter ) ||
             ( progress.isCancelled && progress.isCancelled() );
   }
   for ( var k = 0; k < specs.length && !res.cancelled; ++k )
   {
      var job = FlyThrough.presetJob( scene, specs[k], opts ), folder = dir + "/" + job.p.id;
      if ( cancelled() ) { res.cancelled = true; break; }       // before touching this preset's folder
      // frames made from the same options are kept: only the encode changed (Fly.frameSignature)
      var signature = Fly.frameSignature( opts, job.p, opts.sceneKey || "" );
      if ( FlyThrough.framesReady( folder, signature, job.n ) ) res.reused += job.n;
      else
      {
         // a render stopped part way with the same options is resumed: its frames are kept
         var resume = FlyThrough.recordMatches( folder, signature );
         FlyThrough.prepareFolder( folder, resume );
         // the record goes first, so the frames written from here on can be resumed too
         File.writeTextFile( folder + "/" + FlyThrough.FRAMES_RECORD, signature );
         FlyThrough.renderFrames( job, folder, res, progress, total, cancelled, resume );
      }
      if ( !res.cancelled )
         // the video beside its frames' folder, named after the object, preset and encoding (Fly.videoName)
         FlyThrough.finishPreset( folder, dir + "/" + Fly.videoName( opts.objectName, job.p.id, job.po.transfer ), job.po, res, progress, job.n );
   }
   return res;
};

FlyThrough.PREVIEW_EVERY = 250;          // ms between the rendered frames shown in the preview
FlyThrough.FRAMES_RECORD = "frames.json";   // what a preset folder's frames were made from (Fly.frameSignature)

FlyThrough.PARTIAL_SUFFIX = ".part";   // a frame being written; renamed when complete

FlyThrough.isPartialFrame = function( name )
{
   return name.length > FlyThrough.PARTIAL_SUFFIX.length && name.slice( -FlyThrough.PARTIAL_SUFFIX.length ) == FlyThrough.PARTIAL_SUFFIX &&
          Fly.isFrameFile( name.slice( 0, -FlyThrough.PARTIAL_SUFFIX.length ) );
};

/* Were a preset folder's frames made with `signature` (Fly.frameSignature)? */
FlyThrough.recordMatches = function( folder, signature )
{
   var record = folder + "/" + FlyThrough.FRAMES_RECORD;
   try { return File.exists( record ) && File.readTextFile( record ) == signature; }
   catch ( e ) { return false; }
};

/* Are a preset folder's frames the ones `signature` makes, all n of them? */
FlyThrough.framesReady = function( folder, signature, n )
{
   if ( !FlyThrough.recordMatches( folder, signature ) ) return false;
   for ( var i = 0; i < n; ++i ) if ( !File.exists( Fly.framePath( folder, i ) ) ) return false;
   return true;
};

/*
 * The scene a preset's frames are drawn from: turned for it (Render.sceneFor)
 * and, at High and Medium star quality, shrunk toward the preset's size
 * (Fly.qualityScale, Render.scaledScene) -- decided by the preset, so a
 * draft of it draws from the same scene. Highest: the full-size scene.
 */
FlyThrough.qualityScene = function( scene, opts, spec )
{
   var ps = Render.sceneFor( scene, spec.w, spec.h ), crop = Fly.presetCrop( ps.w, ps.h, ps.tp.x, ps.tp.y, spec.w, spec.h );
   var d = Fly.qualityScale( opts.starQuality, crop.w, spec.w );
   return d < 0.999 ? Render.scaledScene( ps, d ) : ps;
};

/* One preset's render: its frames, scene (a vertical image turned, never cut to a band), crop and options. */
FlyThrough.presetJob = function( scene, p, opts )
{
   var n = Fly.frameCount( opts.duration, opts.fps, p.pingPong ), ps = FlyThrough.qualityScene( scene, opts, p );
   var F = p.crossfade ? Fly.crossfadeFrames( opts.duration, opts.fps ) : 0;
   var po = Object.assign( {}, FlyThrough.presetOptions( opts, p.preset || p.id ), { frameDt: Fly.frameStep( n, F, p.pingPong ), loops: !!( p.pingPong || p.crossfade ),
               logo: opts.logoImage ? Render.logoLayer( opts.logoImage, p.w, p.h, opts.logoPlace, scene.nc, opts.logoOpacity ) : null } );
   return { p: p, n: n, F: F, ps: ps, po: po, crop: Fly.presetCrop( ps.w, ps.h, ps.tp.x, ps.tp.y, p.w, p.h ) };
};

/*
 * A preset's frames into `folder`, counting into res and stopping when
 * cancelled(). Resuming, the frames already there are kept (res.reused).
 * Each frame is written under a .part name and renamed when complete, so
 * one caught half-written by a crash is never taken for a finished one.
 * progress.onFrame( done, total, preset, kept ): kept of the done were there before.
 */
FlyThrough.renderFrames = function( job, folder, res, progress, total, cancelled, resume )
{
   var p = job.p, icc = job.po.transfer == "sdr" ? Render.srgbIcc() : null, still = {};
   try { FlyThrough.renderFrameRange( job, folder, res, progress, total, cancelled, resume, icc, still ); }
   finally { if ( still.still ) still.still.free(); }
};

FlyThrough.renderFrameRange = function( job, folder, res, progress, total, cancelled, resume, icc, still )
{
   var p = job.p;
   for ( var i = 0; i < job.n; ++i )
   {
      var path = Fly.framePath( folder, i );
      if ( resume && File.exists( path ) ) { ++res.reused; continue; }
      if ( cancelled() ) { res.cancelled = true; return; }
      var img = p.crossfade ? FlyThrough.loopImage( job.ps, i, job.n, job.F, job.po, p.w, p.h, job.crop, still )
                            : Render.frame( job.ps, Fly.timeAt( i, job.n, p.pingPong ), job.po, p.w, p.h, job.crop );
      try
      {
         Render.writeTiff( img, path + FlyThrough.PARTIAL_SUFFIX, icc );
         if ( progress.onImage ) progress.onImage( img, p.id, job.po.transfer );   // the frame just finished, for the preview
      }
      finally { img.free(); }
      File.move( path + FlyThrough.PARTIAL_SUFFIX, path );
      ++res.written;
      if ( progress.onFrame ) progress.onFrame( res.written + res.reused, total, p.id, res.reused );
      CoreApplication.processEvents();
   }
};

/* A logo layer (Render.logoLayer, premultiplied) painted over an 8-bit bitmap: logo + pixel x (1 - alpha). */
FlyThrough.paintLayer = function( bmp, L )
{
   for ( var v = 0; v < L.h; ++v )
      for ( var u = 0; u < L.w; ++u )
      {
         var j = v*L.w + u, a = L.a[j], x = L.x + u, y = L.y + v;
         if ( a <= 0 || x < 0 || y < 0 || x >= bmp.width || y >= bmp.height ) continue;
         var c = bmp.pixel( x, y ), out = 0xff000000;
         for ( var k = 0; k < 3; ++k )
         {
            var sh = 16 - 8*k, was = ( ( c >> sh ) & 0xff )/255, val = L.p[Math.min( k, L.p.length - 1 )][j] + was*( 1 - a );
            out |= Math.max( 0, Math.min( 255, Math.round( 255*val ) ) ) << sh;
         }
         bmp.setPixel( x, y, out >>> 0 );
      }
};

/* The logo image for options o (logoPath, logoPlace), read without opening a window; null when off. */
FlyThrough.readLogo = function( o )
{
   if ( !o.logoPath || o.logoPlace == "off" ) return null;
   if ( !File.exists( o.logoPath ) ) throw new Error( "The logo file is not there: " + o.logoPath );
   var f = new FileFormatInstance( new FileFormat( File.extractExtension( o.logoPath ), true, false ) );
   var d = f.open( o.logoPath, "verbosity 0" );
   if ( !d || !d.length ) throw new Error( "PixInsight could not read the logo " + o.logoPath );
   try
   {
      var img = new Image( 1, 1, 1 );
      if ( !f.readImage( img ) ) { img.free(); throw new Error( "PixInsight could not read the logo " + o.logoPath ); }
      return img;
   }
   finally { f.close(); }
};

/*
 * One preset's options: the output transform from the image's colour to
 * the video's -- Rec.709 for SDR, or BT.2020 with the preset's transfer
 * (opts.hdrTransfer[id], else Fly.HDR_DEFAULT_TRANSFER) for HDR -- fresh
 * per preset, so a PQ preset measures its own MaxCLL/MaxFALL.
 */
FlyThrough.presetOptions = function( opts, id )
{
   if ( opts.output )
      return opts;                              // the suite supplies its own
   var hdr = ( opts.dynamic == "hdr" );
   var transfer = hdr ? ( ( opts.hdrTransfer && opts.hdrTransfer[id] ) || Fly.HDR_DEFAULT_TRANSFER[id] || "hlg" ) : "sdr";
   return Object.assign( {}, opts, { transfer: transfer,
      output: Fly.outputTransform( opts.colour || Fly.SRGB_COLOUR, transfer, { peak: opts.peak || Fly.HDR_PEAK_DEFAULT } ) } );
};

/* The video for one finished preset, or the command that would make it. */
FlyThrough.finishPreset = function( folder, outBase, opts, res, progress, frames )
{
   var args = FlyThrough.encodeArgs( folder, outBase, opts, frames );
   if ( !opts.video || !opts.ffmpeg )
   {
      res.commands.push( Fly.commandLine( opts.ffmpeg || "ffmpeg", args, Util.PLATFORM ) );
      return;
   }
   var video = args[args.length - 1];
   FlyThrough.removeQuietly( video );                            // no stale video that looks current
   if ( Render.encode( opts.ffmpeg, args, progress.isCancelled, FlyThrough.encodeProgress( progress, frames ) ) )
      res.videos.push( video );
   else if ( progress.isCancelled && progress.isCancelled() )
   {
      res.cancelled = true;
      FlyThrough.removeQuietly( video );                         // a partial file is not a video
   }
   else
      res.failed.push( { video: video, output: Render.lastEncodeOutput, command: Fly.commandLine( opts.ffmpeg, args, Util.PLATFORM ) } );
};

/* The ffmpeg arguments for a preset's frames: HDR tags and metadata when it is HDR, its music when there is some. */
FlyThrough.encodeArgs = function( folder, outBase, opts, frames )
{
   var hdr = ( opts.transfer == "pq" || opts.transfer == "hlg" ) ?
             { transfer: opts.transfer, peak: opts.peak || Fly.HDR_PEAK_DEFAULT,
               maxCll: opts.output.stats.maxCll, maxFall: opts.output.stats.maxFall } : null;
   var audio = ( opts.music && opts.music.path ) ? { path: opts.music.path, fade: opts.music.fade, duration: frames/opts.fps, loop: !!opts.loops } : null;
   return Fly.ffmpegArgs( folder, opts.fps, outBase, opts.format || ( hdr ? "hevc" : "h264" ), opts.quality || "high", hdr, audio );
};

/* ffmpeg counts its frames on stderr ("frame=  37"): that is the encode's progress. Null without a listener. */
FlyThrough.encodeProgress = function( progress, frames )
{
   if ( !progress.onEncode ) return null;
   return function( chunk )
   {
      var k = Fly.ffmpegFrameProgress( chunk );
      if ( k != null ) progress.onEncode( Math.min( k, frames ), frames );
   };
};

FlyThrough.removeQuietly = function( path )
{
   try { if ( File.exists( path ) ) File.remove( path ); } catch ( e ) {}
};

/*
 * Phase one, fast: where the image points, what it shows, how far away.
 * choices = { hints: {ra, dec, focal, pixel} for an unsolved image,
 * target (an NGC/IC entry, overriding the automatic pick), type ("nebula" |
 * "galaxy", overriding), distance (pc, typed) }. D is null when a nebula's
 * distance is still needed.
 */
FlyThrough.identify = function( window, choices, progress )
{
   choices = choices || {};
   var stage = ( progress && progress.stage ) ? progress.stage : function() {};
   if ( Sky.projector( window ) == null )
   {
      if ( !choices.hints )
         return { needsSolve: true };
      stage( "Plate-solving: finding where in the sky the image points", 0, 0 );
      var solvedPixel = Sky.solveWithHints( window, choices.hints, stage );
   }
   stage( "Finding what the image shows (NGC/IC catalogue)", 1, 4 );
   var proj = Sky.projector( window ), field = Sky.field( window );
   var ngc = Sky.readNgcIc(), pick = ngc ? Fly.pickTarget( ngc, field.centre, field.radiusDeg ) : { best: null, runnerUp: null };
   var target = choices.target || pick.best;
   var type = choices.type || Fly.targetType( target );
   // the camera flies into the middle of the picture, the photographer's
   // composition; the target sets the distance, not the direction (IC 1396's
   // catalogued centre, off the middle of a frame, sent the flight sideways)
   var id = { proj: proj, field: field, pick: pick, target: target, type: type, D: null, distanceSource: null, cluster: null,
              aim: field.centre,
              sources: ( stage( "Looking up the stars and their distances in Gaia", 2, 4 ), Sky.requireSources( field.centre, field.radiusDeg ) ),
              solvedPixel: ( typeof solvedPixel == "number" ) ? solvedPixel : null };
   if ( type == "galaxy" )
   {
      id.D = Infinity;
      id.distanceSource = "galaxy";
   }
   else if ( choices.distance > 0 )
   {
      id.D = choices.distance;
      id.distanceSource = "typed";
   }
   else if ( target )
   {
      stage( "Finding the nebula's distance from its star cluster", 3, 4 );
      FlyThrough.clusterDistance( id, target );
   }
   return id;
};

/* The nebula's distance from its ionising cluster when one is found, else from the bright star that lights it. */
FlyThrough.clusterDistance = function( id, target )
{
   var r = ( target.diameter > 0 ? target.diameter/120 : id.field.radiusDeg );
   try
   {
      var c = Fly.findCluster( Sky.clusterSources( target, r ), target, Fly.clusterRadius( r ) );
      if ( c != null )
      {
         id.cluster = c;
         id.D = c.distance;
         id.distanceSource = "cluster";
      }
   }
   catch ( e ) { Util.warn( "fly", "cluster search failed: " + e ); }
   if ( id.D != null ) return;
   // no ionising cluster (a reflection nebula): the bright star that lights it
   var star = Fly.illuminatorDistance( id.sources, target );
   if ( !star && id.sources.origin != "online" )
   {
      // the local catalogue may lack it (HD 200775, the Iris's, is not in DR3/SP): Gaia DR3 online
      star = Fly.illuminatorDistance( Sky.queryOnline( target, Fly.illuminatorRadius( target ), Fly.ILLUMINATOR_MAX_G ), target );
      if ( star ) star.online = true;
   }
   if ( star )
   {
      id.illuminator = star;
      id.D = star.distance;
      id.distanceSource = "star";
   }
};

/*
 * Phase two, slow: the star split, the placed stars and their sprites,
 * and the scene. Returns { scene, counts, windows } -- the caller closes
 * `windows` (the starless and stars layers).
 */
FlyThrough.build = function( window, id, choices, progress )
{
   var stage = ( progress && progress.stage ) ? progress.stage : function() {};
   var stretched = Sky.stretchIfLinear( window );
   stage( "Removing the stars with " + choices.tool + " (the longest step)", 0, 0 );
   var split = Sky.splitStars( window, choices.tool );
   try
   {
      var img = window.mainView.image, W = img.width, H = img.height;
      var placed = Fly.placeStars( id.sources, id.proj, W, H );
      var neighbours = [];
      id.sources.forEach( function( s )
      {
         var q = id.proj( s.ra, s.dec );
         if ( q && q.x >= 0 && q.y >= 0 && q.x < W && q.y < H ) neighbours.push( { source: s, x: q.x, y: q.y } );
      } );
      var sp = Sky.sprites( split.stars.mainView.image, placed, choices.fwhm, neighbours, stage );
      stage( "Putting the scene together", 0, 0 );
      var scene = Render.scene( { starless: split.starless.mainView.image, stars: split.stars.mainView.image,
                                  sprites: sp.sprites, residual: sp.residual, project: id.proj,
                                  target: id.aim || id.target || id.field.centre, D: id.D,
                                  catalogue: neighbours.map( function( n ) { return { x: n.x, y: n.y, G: n.source.G, ra: n.source.ra, dec: n.source.dec }; } ) } );
      var col = Sky.colourOf( choices.colourFrom || window );   // the original's profile, not the working copy's
      // the scene holds its own copies of the pixels: the split windows (up
      // to ~1.4 GB for a 60 MP RGB image) are not needed any more
      split.starless.forceClose();
      split.stars.forceClose();
      return { scene: scene, windows: [], colour: col.colour, colourNote: col.note + ( stretched ? "; it looked linear, so it was stretched" : "" ), stretched: stretched,
               counts: { detected: sp.detected, placed: sp.sprites.length, blended: sp.blended, backdrop: neighbours.length - sp.sprites.length } };
   }
   catch ( e )
   {
      split.starless.forceClose();
      split.stars.forceClose();
      throw e;
   }
};

/* ---------------------------------------------------------------------------
 * Draft preview.
 * ------------------------------------------------------------------------ */

/*
 * Frame i of an n-frame crossfade loop fading over F frames (Fly.loopFrame):
 * one render, or two mixed. The pre-roll it dissolves into is frame 0 when
 * the camera rests before the start (any easing but linear): with a `cache`
 * ({}) that still is rendered once and kept in cache.still (the caller frees it).
 */
FlyThrough.loopImage = function( scene, i, n, F, opts, w, h, crop, cache )
{
   var f = Fly.loopFrame( i, n, F ), img = Render.frame( scene, f.a, opts, w, h, crop );
   if ( f.alpha >= 1 ) return img;
   var still = !!cache && f.b <= 0 && opts.easing != "linear";
   var b = still ? ( cache.still || ( cache.still = Render.frame( scene, 0, opts, w, h, crop ) ) ) : Render.frame( scene, f.b, opts, w, h, crop );
   try { return Render.blend( img, b, f.alpha ); }
   finally { if ( !still ) b.free(); }
};

/*
 * The draft: Fly.DRAFT_LONG px on the long side, under 400 MB for any duration
 * (Fly.draftPlan lowers the frame rate if needed), SDR. A ping-pong preset
 * renders only its forward half; the player plays it back and forth.
 */
FlyThrough.renderDraft = function( scene, opts, spec, progress )
{
   progress = progress || {};
   var plan = Fly.draftPlan( opts.duration, opts.fps, Fly.DRAFT_LONG, spec.w/spec.h );
   scene = FlyThrough.qualityScene( scene, opts, spec );
   var crop = Fly.presetCrop( scene.w, scene.h, scene.tp.x, scene.tp.y, spec.w, spec.h );
   var o = Object.assign( {}, opts, { kernel: "bilinear", output: Fly.outputTransform( opts.colour || Fly.SRGB_COLOUR, "sdr" ) } );
   var bitmaps = [], still = {};   // a crossfade's pre-roll still, rendered once (FlyThrough.loopImage)
   var F = spec.crossfade ? Fly.crossfadeFrames( opts.duration, plan.fps ) : 0;
   o.frameDt = Fly.frameStep( plan.frames, F, false );
   o.logo = opts.logoImage ? Render.logoLayer( opts.logoImage, plan.width, plan.height, opts.logoPlace, scene.nc, opts.logoOpacity ) : null;
   try
   {
      for ( var i = 0; i < plan.frames; ++i )
      {
         if ( progress.isCancelled && progress.isCancelled() )
            break;
         var img = spec.crossfade ? FlyThrough.loopImage( scene, i, plan.frames, F, o, plan.width, plan.height, crop, still )
                                  : Render.frame( scene, plan.frames > 1 ? i/( plan.frames - 1 ) : 0, o, plan.width, plan.height, crop );
         try { bitmaps.push( img.render() ); }
         finally { img.free(); }
         if ( progress.onFrame ) progress.onFrame( i + 1, plan.frames );
         CoreApplication.processEvents();
      }
   }
   finally { if ( still.still ) still.still.free(); }   // a crossfade's still, even when a frame fails
   return { bitmaps: bitmaps, fps: plan.fps, pingPong: spec.pingPong };
};

/*
 * Plays a list of bitmaps. A single-shot Timer re-armed per frame; the
 * frame shown is the one due by the wall clock, so late ticks drop frames
 * instead of slowing the clip. Stops itself when not visible.
 */
FlyThrough.Player = class extends Control
{
   constructor( parent )
   {
      super( parent );
      var self = this;
      this.frames = [];
      this.fps = 30;
      this.pingPong = false;
      this.current = 0;
      this.startedAt = 0;
      this.playing = false;
      this.onFrame = null;
      this.setScaledMinSize( 480, 270 );
      this.timer = new Timer;
      this.timer.singleShot = true;
      this.timer.onTimeout = function() { self.tick(); };
      this.onPaint = function() { self.paintFrame(); };
   }

   setFrames( bitmaps, fps, pingPong )
   {
      this.pause();
      this.badge = "";                       // a label belongs to the frames it was set with
      this.frames = bitmaps || [];
      this.fps = fps || 30;
      this.pingPong = !!pingPong;
      this.current = 0;
      this.update();
   }

   frameAt( now )
   {
      var n = this.frames.length;
      if ( n == 0 ) return 0;
      var i = Render.dueFrame( this.startedAt, now, this.fps, Fly.sequenceLength( n, this.pingPong ) );
      return Fly.sequenceFrame( i, n, this.pingPong );
   }

   play()
   {
      if ( this.frames.length == 0 || this.timer == null ) return;
      this.startedAt = Date.now() - this.current*1000/this.fps;
      this.playing = true;
      this.arm();
   }

   pause()
   {
      this.playing = false;
      if ( this.timer ) this.timer.stop();
   }

   seek( i )
   {
      this.current = Math.max( 0, Math.min( this.frames.length - 1, i ) );
      this.update();
   }

   arm()
   {
      this.timer.interval = 1/Math.max( 1, this.fps );   // seconds
      this.timer.start();
   }

   tick()
   {
      if ( !this.playing || this.timer == null ) return;
      if ( !this.visible ) { this.pause(); return; }
      this.current = this.frameAt( Date.now() );
      if ( this.onFrame ) this.onFrame( this.current );
      this.update();
      this.arm();
   }

   /* A label in the preview's bottom-right corner ("" for none), e.g. "HDR Preview". */
   setBadge( text )
   {
      this.badge = text || "";
      this.update();
   }

   paintFrame()
   {
      var g = new Graphics( this );
      try
      {
         g.fillRect( new Rect( 0, 0, this.width, this.height ), new Brush( 0xff000000 ) );
         var b = this.frames[this.current];
         if ( b )
         {
            var k = Math.min( this.width/b.width, this.height/b.height ), w = b.width*k, h = b.height*k;
            var x = ( this.width - w )/2, y = ( this.height - h )/2;
            g.drawScaledBitmap( new Rect( x, y, x + w, y + h ), b );
            if ( this.badge ) this.paintBadge( g, x + w, y + h );
         }
      }
      finally { g.end(); }
   }

   /* The badge: white on a dark rounded box, inset from the frame's bottom-right corner (right, bottom). */
   paintBadge( g, right, bottom )
   {
      var f = this.font, pad = 6, tw = f.width( this.badge ), th = f.height, m = 10;
      var r = new Rect( right - m - tw - 2*pad, bottom - m - th - pad, right - m, bottom - m );
      g.antialiasing = true;
      g.pen = new Pen( 0x60ffffff );
      g.brush = new Brush( 0xb0000000 );
      g.drawRoundedRect( r, 6, 6 );
      g.pen = new Pen( 0xffffffff );
      g.drawText( r.x0 + pad, r.y0 + pad/2 + f.ascent, this.badge );
   }

   release()
   {
      try
      {
         this.pause();
         if ( this.timer ) { this.timer.onTimeout = null; this.timer = null; }
         this.onPaint = null;
         this.onFrame = null;
         this.frames = [];
      }
      catch ( e ) { /* releasing must never be the thing that fails */ }
   }
};

/*
 * A progress bar: filled to the fraction done, with the stage, count,
 * percentage and time left written across it (Fly.progressText). A stage
 * with no count (a star tool that reports nothing) shows a pulse instead.
 * set() repaints and pumps events, so it moves while work runs; the one
 * thing it cannot do is move during a single PixInsight process, which
 * holds the thread until it returns.
 */
FlyThrough.ProgressBar = class extends Control
{
   constructor( parent )
   {
      super( parent );
      var self = this;
      this.fraction = null;
      this.text = "";
      this.setScaledMinHeight( 22 );
      this.setScaledMinWidth( 420 );
      this.onPaint = function()
      {
         var g = new Graphics( self );
         try { self.paintOn( g, self.width, self.height ); }
         finally { g.end(); }
      };
   }

   set( fraction, text )
   {
      this.fraction = ( fraction == null ) ? null : Math.max( 0, Math.min( 1, fraction ) );
      this.text = text || "";
      this.update();
      processEvents();
   }

   paintOn( g, w, h )
   {
      var P = FlyThrough.ProgressBar;
      g.fillRect( new Rect( 0, 0, w, h ), new Brush( P.TRACK ) );
      if ( this.fraction != null )
         g.fillRect( new Rect( 0, 0, Math.round( w*this.fraction ), h ), new Brush( P.FILL ) );
      else if ( this.text )
      {
         var bw = Math.round( w/5 ), x = Math.round( ( ( Date.now()/1500 ) % 1 )*( w - bw ) );
         g.fillRect( new Rect( x, 0, x + bw, h ), new Brush( P.PULSE ) );
      }
      g.pen = new Pen( P.TEXT );
      g.drawTextRect( new Rect( 6, 0, w - 6, h ), this.text, TextAlign_Center | TextAlign_VertCenter );
   }

   release()
   {
      this.onPaint = null;
   }
};
FlyThrough.ProgressBar.TRACK = 0xff2b2b2b;
FlyThrough.ProgressBar.FILL  = 0xff3a7bd5;
FlyThrough.ProgressBar.PULSE = 0xff4f6f9a;
FlyThrough.ProgressBar.TEXT  = 0xffffffff;

/* ---------------------------------------------------------------------------
 * The dialog.
 * ------------------------------------------------------------------------ */

FlyThrough.PRESET_LABELS = { social_vertical: "Social 1080×1920", social_square: "Social 1080×1080",
                             youtube_4k: "YouTube 3840×2160", youtube_1080: "YouTube 1920×1080",
                             exhibition: "Exhibition 3840×2160 (loop)" };
FlyThrough.PRESET_ORDER = [ "social_vertical", "social_square", "youtube_4k", "youtube_1080", "exhibition" ];
FlyThrough.FPS = [ 24, 25, 30, 60 ];

/* Render speed on this scene, ms per output megapixel, from one 1920x1080 frame. */
FlyThrough.measureSpeed = function( scene, opts )
{
   // from the scene the chosen Star quality draws from (High and Medium: shrunk to the video's size)
   scene = FlyThrough.qualityScene( scene, opts, { id: "speed", w: 1920, h: 1080 } );
   var crop = Fly.presetCrop( scene.w, scene.h, scene.tp.x, scene.tp.y, 1920, 1080 );
   var t0 = Date.now();
   Render.frame( scene, 0.5, Object.assign( {}, opts, { travel: opts.travel || 1,
                 output: Fly.outputTransform( opts.colour || Fly.SRGB_COLOUR, "sdr" ) } ), 1920, 1080, crop ).free();
   return ( Date.now() - t0 )/( 1920*1080/1e6 );
};

/* Estimated milliseconds for a final render (HDR measured ~1.3x SDR). */
FlyThrough.estimateMs = function( msPerMp, presets, duration, fps, hdr, orientation, loop )
{
   return presets.reduce( function( a, id )
   {
      var p = Fly.presetSpec( id, orientation, loop );
      return a + Fly.frameCount( duration, fps, p.pingPong )*p.w*p.h/1e6*msPerMp;
   }, 0 )*( hdr ? 1.3 : 1 );
};

/* The found target, as the dialog shows it. */
FlyThrough.describeTarget = function( id )
{
   var t = id.target;
   if ( !t )
      return "No NGC/IC object in the field: type the distance.";
   return "<b>" + t.id + "</b>" + ( t.name ? " (" + t.name + ")" : "" ) +
          ( id.pick.runnerUp ? " &nbsp; runner-up: " + id.pick.runnerUp.id : "" );
};

/* Where the distance came from. */
FlyThrough.describeDistance = function( id )
{
   switch ( id.distanceSource )
   {
   case "cluster": return "from its cluster: " + id.cluster.members + " stars, " +
                          Math.round( id.cluster.lo ) + "–" + Math.round( id.cluster.hi ) + " pc";
   case "star":    return "from its brightest star (G " + id.illuminator.G.toFixed( 1 ) + "), which lights it" + ( id.illuminator.online ? " (Gaia DR3 online)" : "" );
   case "galaxy":  return "galaxy: fixed backdrop";
   case "typed":   return "typed";
   }
   return "<b>needed</b>";
};

FlyThrough.STILL_LONG = 960;   // px: the preview's still of a chosen image, on its long side

/* A small still of the image for the preview (rendered at 1:n, so a big image is never drawn whole). */
FlyThrough.still = function( window )
{
   var img = window.mainView.image, n = Math.max( 1, Math.ceil( Math.max( img.width, img.height )/FlyThrough.STILL_LONG ) );
   return img.render( n > 1 ? -n : 1 );
};

FlyThrough.LOOPS = [ [ "none", "None" ], [ "pingpong", "Back and forth" ], [ "crossfade", "Crossfade" ] ];   // the Loop choices
FlyThrough.STAR_QUALITIES = [ "highest", "high", "medium" ];   // the Star quality combo's items, in order
FlyThrough.STAR_QUALITY_DEFAULT = "high";                 // the dialog's default (code without a level gets Highest)
FlyThrough.OPTIONS_SETTING = "Loom/flyOptions";   // the dialog's options, JSON, restored next time
FlyThrough.OBJECTS_SETTING = "Loom/flyObjects";   // each image's solve hints, JSON by file (or name, unsaved)
FlyThrough.LOGO_SETTING = "Loom/flyLogo";         // the logo file last chosen
FlyThrough.LOGO_PLACE_SETTING = "Loom/flyLogoPlace";
/* ---------------------------------------------------------------------------
 * The per-image cache, in the system's temp folder: an image's solved
 * working copy, its star scene per star tool, and its drafts, so choosing
 * it again skips the resample, solve, star removal and deblending.
 * ------------------------------------------------------------------------ */

FlyThrough.CACHE_KEEP = 3;   // images kept; the least recently used go first

FlyThrough.cacheRoot = function()
{
   return File.systemTempDirectory + "/LoomFlyThrough";
};

/* An image's cache folder under root (Fly.imageCacheKey: its file or view name, its size, the Loom version), made if missing. */
FlyThrough.cacheDir = function( root, window )
{
   var path = window.filePath, bytes = 0, mtime = 0, img = window.mainView.image;
   try { if ( path && File.exists( path ) ) { var fi = new FileInfo( path ); bytes = fi.size; mtime = fi.lastModified.getTime(); } } catch ( e ) {}
   var dir = root + "/" + Fly.imageCacheKey( path, bytes, mtime, img.width, img.height, img.numberOfChannels, Util.LOOM_VERSION, window.mainView.id );
   if ( !File.directoryExists( dir ) ) File.createDirectory( dir, true );
   return dir;
};

/* The solved working copy, as XISF (which keeps the astrometric solution), its scale and whether it had a solution. */
FlyThrough.saveWork = function( dir, work )
{
   work.window.saveAs( dir + "/work.xisf", false, false, false, false );
   var keywords = work.window.keywords.map( function( k ) { return [ k.name, k.value, k.comment ]; } );
   File.writeTextFile( dir + "/work.json", JSON.stringify( { scale: work.scale, solved: work.window.hasAstrometricSolution, keywords: keywords } ) );
};

FlyThrough.loadWork = function( dir )
{
   try
   {
      if ( !File.exists( dir + "/work.xisf" ) || !File.exists( dir + "/work.json" ) ) return null;
      var w = ImageWindow.open( dir + "/work.xisf" );
      if ( !w.length ) return null;
      var meta = JSON.parse( File.readTextFile( dir + "/work.json" ) );
      // a copy saved with WCS keywords only is reopened with a solution PixInsight builds from them,
      // which reads them flipped against Loom (measured): it comes back as it was saved, keywords only
      if ( !meta.solved && w[0].hasAstrometricSolution )
      {
         w[0].clearAstrometricSolution();            // which takes the WCS keywords with it: they are put back
         w[0].keywords = ( meta.keywords || [] ).map( function( k ) { return new FITSKeyword( k[0], k[1], k[2] ); } );
      }
      return { window: w[0], scale: meta.scale };
   }
   catch ( e ) { return null; }
};

FlyThrough.starsName = function( tool ) { return "stars-" + Fly.hashKey( tool ); };

/* The star scene built with `tool` (Render.packScene), its counts and notes. */
FlyThrough.saveBuilt = function( dir, tool, built )
{
   var packed = Render.packScene( built.scene ), base = dir + "/" + FlyThrough.starsName( tool );
   Sky.writeArrays( base + ".bin", packed.arrays );
   File.writeTextFile( base + ".json", JSON.stringify( { tool: tool, scene: packed.meta, lengths: packed.arrays.map( function( a ) { return a.length; } ),
                                                         counts: built.counts, colourNote: built.colourNote, stretched: built.stretched } ) );
};

/* The star scene built with `tool`, its projection from the solved working copy and its colour from the image; null when none. */
FlyThrough.loadBuilt = function( dir, tool, workWindow, colourFrom )
{
   var base = dir + "/" + FlyThrough.starsName( tool );
   try
   {
      if ( !File.exists( base + ".json" ) || !File.exists( base + ".bin" ) ) return null;
      var meta = JSON.parse( File.readTextFile( base + ".json" ) ), project = Sky.projector( workWindow );
      if ( meta.tool != tool || !project ) return null;          // stars it cannot place are no use
      var scene = Render.unpackScene( meta.scene, Sky.readArrays( base + ".bin", meta.lengths ), project );
      return { scene: scene, windows: [], colour: Sky.colourOf( colourFrom ).colour, colourNote: meta.colourNote, stretched: meta.stretched, counts: meta.counts };
   }
   catch ( e ) { return null; }
};

/* A draft, for the options signature `sig` (Fly.frameSignature), as PNG frames. */
FlyThrough.saveDraft = function( dir, sig, d )
{
   var folder = dir + "/draft-" + Fly.hashKey( sig );
   if ( !File.directoryExists( folder ) ) File.createDirectory( folder, true );
   d.bitmaps.forEach( function( b, i ) { b.save( folder + "/" + i + ".png" ); } );
   File.writeTextFile( folder + "/draft.json", JSON.stringify( { sig: sig, fps: d.fps, pingPong: d.pingPong, n: d.bitmaps.length } ) );
};

FlyThrough.loadDraft = function( dir, sig )
{
   var folder = dir + "/draft-" + Fly.hashKey( sig );
   try
   {
      if ( !File.exists( folder + "/draft.json" ) ) return null;
      var meta = JSON.parse( File.readTextFile( folder + "/draft.json" ) );
      if ( meta.sig != sig ) return null;
      var bitmaps = [];
      for ( var i = 0; i < meta.n; ++i ) bitmaps.push( new Bitmap( folder + "/" + i + ".png" ) );
      return { bitmaps: bitmaps, fps: meta.fps, pingPong: meta.pingPong };
   }
   catch ( e ) { return null; }
};

/* Marks an image's cache folder as used (at time t, now by default). */
FlyThrough.touch = function( dir, t )
{
   try { File.writeTextFile( dir + "/used", String( t != null ? t : Date.now() ) ); } catch ( e ) {}
};

/* Keeps the `keep` most recently used images' folders under root and deletes the rest (Fly.cacheToPrune). */
FlyThrough.prune = function( root, keep )
{
   if ( !File.directoryExists( root ) ) return;
   var entries = [], find = new FileFind;
   if ( find.begin( root + "/*" ) )
      do
      {
         if ( !find.isDirectory || find.name == "." || find.name == ".." ) continue;
         var used = 0;
         try { used = parseFloat( File.readTextFile( root + "/" + find.name + "/used" ) ) || 0; } catch ( e ) {}
         entries.push( { name: find.name, used: used } );
      }
      while ( find.next() );
   Fly.cacheToPrune( entries, keep ).forEach( function( name ) { FlyThrough.removeTree( root + "/" + name ); } );
};

/* A folder and everything in it. */
FlyThrough.removeTree = function( dir )
{
   var find = new FileFind, subs = [], files = [];
   if ( find.begin( dir + "/*" ) )
      do
      {
         if ( find.name == "." || find.name == ".." ) continue;
         ( find.isDirectory ? subs : files ).push( dir + "/" + find.name );
      }
      while ( find.next() );
   files.forEach( function( f ) { try { File.remove( f ); } catch ( e ) {} } );
   subs.forEach( FlyThrough.removeTree );
   try { File.removeDirectory( dir ); } catch ( e ) {}
};

/* A cancelled job ends by throwing this (FlyThrough.cancel), which the dialog shows as "Cancelled", not as an error. */
FlyThrough.cancel = function() { var e = new Error( "Cancelled." ); e.loomCancel = true; return e; };
FlyThrough.isCancel = function( e ) { return !!( e && e.loomCancel ); };

FlyThrough.TOOL_SETTING = "Loom/flyStarTool";   // the star removal tool last chosen
FlyThrough.FOCAL_SETTING = "Loom/flyFocal";   // the last focal length used (mm)
FlyThrough.PIXEL_SETTING = "Loom/flyPixel";   // the last pixel size used (um)

FlyThrough.Dialog = class extends Dialog
{
   constructor( window )
   {
   super();
   try
   {
      this.imageWindow = ( window && !window.isNull ) ? window : null;
      this.id = null;
      this.built = null;
      this.builtTool = null;
      this.busy = false;
      this.cancelRequested = false;
      this.drafting = false;          // the job running is a draft
      this.redraftPending = false;    // a change made during a draft left it stale (redraft)
      this.analysisDepth = 0;         // > 0 while the image is analysed and its stars extracted (analysing)
      this.extracting = null;         // the star tool whose extraction is running
      this.toolSwitch = null;         // the tool chosen while it ran: its result is discarded, and extracted again with this one
      this.ffmpeg = Render.findFfmpeg();
      this.encoders = this.ffmpeg ? Render.ffmpegEncoders( this.ffmpeg ) : "";
      this.windowTitle = "Loom Fly-Through";
      this.buildInput();
      this.buildFound();
      this.buildMotion();
      this.buildOutput();
      this.buildVideo();
      this.buildPreview();
      this.buildButtons();
      this.layOut();
      this.refreshHdr();
      this.refreshFormats();
      this.restoreOptions();
      this.setImage( this.imageWindow );
      // an image active when the dialog opens is got ready as soon as the dialog is on screen
      var self = this;
      this.onShow = function() { if ( self.imageWindow && !self.shownOnce ) { self.shownOnce = true; self.chooseImage( self.imageWindow ); } };
   }
   catch ( e )
   {
      try { this.release(); } catch ( e2 ) {}
      throw e;
   }
   }

   label( text )
   {
      var l = new Label( this );
      l.text = text;
      l.textAlignment = TextAlign_Right | TextAlign_VertCenter;
      l.setScaledMinWidth( 90 );
      return l;
   }

   row( items )
   {
      var s = new HorizontalSizer;
      s.spacing = 6;
      items.forEach( function( c ) { if ( c === "stretch" ) s.addStretch(); else s.add( c ); } );
      return s;
   }

   edit( text, width )
   {
      var e = new Edit( this );
      e.text = text;
      if ( width ) e.setScaledFixedWidth( width );
      return e;
   }

   group( title, rows )
   {
      var g = new GroupBox( this );
      g.title = title;
      g.sizer = new VerticalSizer;
      g.sizer.margin = 6;
      g.sizer.spacing = 4;
      rows.forEach( function( r ) { g.sizer.add( r ); } );
      return g;
   }

   /* The image -- one already open, or a file -- and the solve hints an unsolved image needs. */
   buildInput()
   {
      var self = this;
      this.imageLabel = new Label( this );
      this.imageList = new ViewList( this );
      this.imageList.getMainViews();
      this.imageList.setScaledMinWidth( 220 );
      this.imageList.toolTip = "<p>The finished image to fly into: any image open in PixInsight.</p>";
      this.imageList.onViewSelected = function( view )
      {
         var same = self.imageWindow && !view.isNull && view.id == self.imageWindow.mainView.id;
         if ( !same ) self.guarded( function() { self.chooseImage( view.isNull ? null : view.window ); } );
      };
      this.openButton = new PushButton( this );
      this.openButton.text = "Open\u2026";
      this.openButton.toolTip = "<p>Open an image file (XISF, FITS or TIFF).</p>";
      this.openButton.onClick = function() { self.guarded( function() { self.openImage(); } ); };
      this.imageBox = this.group( "Image", [ this.row( [ this.imageList, this.openButton, "stretch" ] ), this.imageLabel ] );
      this.objectEdit = this.edit( "", 180 );
      this.objectEdit.toolTip = "<p>What the image shows: an NGC/IC id (IC 1396), a Messier number (M31) or a name (Elephant's Trunk). Typos are forgiven.</p>";
      this.objectMatch = new Label( this );
      this.objectMatch.text = "";
      this.objectEdit.onEditCompleted = function() { self.lookUpObject(); self.hintsEdited(); };
      this.raEdit = this.edit( "", 90 );
      this.decEdit = this.edit( "", 90 );
      this.focalEdit = this.edit( "", 70 );
      this.pixelEdit = this.edit( "", 60 );
      [ this.raEdit, this.decEdit, this.focalEdit, this.pixelEdit ].forEach( function( e ) { e.onEditCompleted = function() { self.hintsEdited(); }; } );
      this.hints = this.group( "Solve (the image has no astrometric solution)", [
         this.row( [ this.label( "Object:" ), this.objectEdit, this.objectMatch, "stretch" ] ),
         this.row( [ this.label( "RA (°):" ), this.raEdit, this.label( "Dec (°):" ), this.decEdit, "stretch" ] ),
         this.row( [ this.label( "Focal (mm):" ), this.focalEdit, this.label( "Pixel (µm):" ), this.pixelEdit, "stretch" ] ) ] );
   }

   /* Opens an image file and makes it the one to fly into. */
   openImage()
   {
      var d = new OpenFileDialog;
      d.caption = "Loom Fly-Through: choose a finished image";
      d.filters = [ [ "Images", "*.xisf *.fit *.fits *.fts *.tif *.tiff" ] ];
      if ( !d.execute() )
         return;
      var opened = ImageWindow.open( d.fileName );
      if ( opened.length == 0 )
         throw new Error( "PixInsight could not open " + d.fileName + "." );
      opened[0].show();
      this.imageList.getMainViews();
      this.imageList.currentView = opened[0].mainView;
      this.chooseImage( opened[0] );
   }

   /* An image the user chose: set it, and get it ready by itself once the dialog is idle. */
   chooseImage( window )
   {
      this.setImage( window );
      if ( this.imageWindow ) this.scheduleAuto();
   }

   /* Gets the image ready (autoPrepare) once the dialog is idle. */
   scheduleAuto()
   {
      var self = this;
      this.autoPending = true;
      if ( !this.autoTimer )
      {
         this.autoTimer = new Timer;
         this.autoTimer.interval = 0.1;
         this.autoTimer.periodic = false;
         this.autoTimer.onTimeout = function()
         {
            if ( !self.autoPending || self.busy ) return;
            self.autoPending = false;
            self.guarded( function() { self.run( function( progress ) { return self.autoPrepare( progress ); } ); } );
         };
      }
      this.autoTimer.start();
   }

   /*
    * Analyses the image, extracts its stars and plays a draft, so the next
    * step is Render. A nebula whose distance is not known stops after the
    * analysis and asks for it. Returns the status line.
    */
   autoPrepare( progress )
   {
      this.requireTool();
      if ( this.hintsMissing() )
         return "No astrometric solution and nothing in the header to solve from: fill in Object (or RA/Dec), focal length and pixel size, and it starts by itself.";
      var self = this, o = null;
      // the dialog stays usable meanwhile (analysing): what is changed is read when it ends
      var note = this.analysing( function()
      {
         self.analyse();
         var galaxy = ( self.typeCombo.currentItem == 1 ), D = self.number( self.distanceEdit );
         if ( !galaxy && !( D > 0 ) )
            return "Analysed. Type the nebula's distance in parsecs, then Draft or Render.";
         o = self.prepare();
         return null;
      } );
      this.redraftPending = false;          // the options were read after every change made so far
      if ( note ) return note;
      // the first draft, again while a change made during it left it stale (as draft() does)
      this.drafting = true;
      try
      {
         for ( ;; )
         {
            this.makeDraft( o, progress || this.progressFor() );
            if ( !this.redraftPending ) break;
            this.redraftPending = false;
            this.cancelRequested = false;
            o = this.prepare();
         }
      }
      finally { this.drafting = false; }
      return "Ready: the draft is playing; press Render for the video.";
   }

   /*
    * The image to fly into (null for none yet). Another image starts over:
    * its working copy, analysis, stars and draft all belong to the old one.
    */
   setImage( window )
   {
      if ( this.busy ) throw new Error( "Wait for the current job to finish (or cancel it) before choosing another image." );
      this.releaseImageWork();
      this.imageWindow = ( window && !window.isNull ) ? window : null;
      if ( this.player ) this.player.setFrames( this.imageWindow ? [ this.stillWithLogo( this.imageWindow ) ] : [], 30, false );
      this.fillFromImage();
   }

   /* What belonged to the previous image: its working copy, analysis, stars, draft and touched fields. */
   releaseImageWork()
   {
      this.releaseBuilt();
      if ( this.work ) { try { this.work.window.forceClose(); } catch ( e ) {} this.work = null; }
      this.id = null;
      this.builtTool = null;
      this.typeTouched = this.distanceTouched = this.travelTouched = false;
      this.hasDraft = false;
      this.cacheDirPath = null;
      this.workCached = false;
      if ( this.starsLabel ) this.starsLabel.text = "";
   }

   /* The fields that come from the image: its name, focal length and pixel size, folder, and the solve hints it needs. */
   fillFromImage()
   {
      var has = ( this.imageWindow != null ), path = has ? this.imageWindow.filePath : "";
      if ( has && this.imageList.currentView.isNull ) this.imageList.currentView = this.imageWindow.mainView;
      this.imageLabel.text = has ? "Image: " + this.imageWindow.mainView.id : "Choose an image that is open, or Open\u2026 a file.";
      // the header's focal length and pixel size, else the ones last used (a rig rarely changes)
      this.focalEdit.text = has ? String( Sky.keywordNumber( this.imageWindow, "FOCALLEN" ) || Settings.read( FlyThrough.FOCAL_SETTING, DataType_String ) || "" ) : "";
      this.pixelEdit.text = has ? String( Sky.keywordNumber( this.imageWindow, "XPIXSZ" ) || Settings.read( FlyThrough.PIXEL_SETTING, DataType_String ) || "" ) : "";
      if ( path ) this.folderEdit.text = File.extractDrive( path ) + File.extractDirectory( path );
      this.needsHints = has && ( Sky.projector( this.imageWindow ) == null );
      // an image's hints start empty -- never the last image's -- then come from its memory or its name
      this.objectEdit.text = this.raEdit.text = this.decEdit.text = this.objectMatch.text = "";
      if ( this.needsHints ) this.recallObject();
      if ( this.needsHints && !this.objectEdit.text.trim() ) this.objectFromName();
      this.hints.visible = this.needsHints;
      if ( this.targetLabel ) this.targetLabel.text = "";
      [ "draftButton", "renderButton" ].forEach( function( k ) { if ( this[k] ) this[k].enabled = has; }, this );
   }

   /* An unsolved image without a centre, focal length or pixel size to solve from. */
   hintsMissing()
   {
      if ( !this.needsHints ) return false;
      var ra = Fly.parseAngle( this.raEdit.text, true ), dec = Fly.parseAngle( this.decEdit.text, false );
      return !( ra != null && dec != null && this.number( this.focalEdit ) > 0 && this.number( this.pixelEdit ) > 0 );
   }

   /* A solve hint was edited: focal length and pixel size are remembered, and complete hints start the analysis. */
   hintsEdited()
   {
      this.rememberObject();
      if ( this.number( this.focalEdit ) > 0 ) Settings.write( FlyThrough.FOCAL_SETTING, DataType_String, this.focalEdit.text.trim() );
      if ( this.number( this.pixelEdit ) > 0 ) Settings.write( FlyThrough.PIXEL_SETTING, DataType_String, this.pixelEdit.text.trim() );
      if ( this.imageWindow && this.id == null && !this.busy && !this.hintsMissing() ) this.scheduleAuto();
   }

   /* The Object box: an id, a Messier number or a name, typos forgiven (Fly.findObject); the match fills RA and Dec. */
   lookUpObject()
   {
      if ( this.ngcIc === undefined ) this.ngcIc = Sky.readNgcIc();
      var found = Fly.findObject( this.objectEdit.text, this.ngcIc || [] );
      if ( !found.length )
      {
         this.objectMatch.text = this.objectEdit.text.trim() ? "not found" : "";
         return;
      }
      var e = found[0];
      this.raEdit.text = e.ra.toFixed( 4 );
      this.decEdit.text = e.dec.toFixed( 4 );
      this.objectMatch.text = e.id + ( e.name ? " \u00b7 " + e.name : "" );
      this.objectMatch.toolTip = found.length > 1 ? "<p>Also close: " + found.slice( 1, 4 ).map( function( f ) { return f.id + ( f.name ? " (" + f.name + ")" : "" ); } ).join( ", " ) + "</p>" : "";
   }

   /* What was found: target, type, distance, counts. */
   buildFound()
   {
      var self = this;
      this.targetLabel = new Label( this );
      this.targetLabel.text = "";
      this.targetLabel.useRichText = true;
      this.typeCombo = new ComboBox( this );
      this.typeCombo.addItem( "Nebula" );
      this.typeCombo.addItem( "Galaxy (fixed)" );
      this.typeTouched = false;
      this.typeCombo.onItemSelected = function() { self.typeTouched = true; };
      this.distanceEdit = this.edit( "", 80 );
      this.distanceTouched = false;
      this.distanceEdit.onEditCompleted = function() { self.distanceTouched = true; };
      this.distanceEdit.toolTip = "<p>Distance to the nebula in parsecs. Filled from its cluster when one is found.</p>";
      this.distanceNote = new Label( this );
      this.distanceNote.useRichText = true;
      this.toolCombo = new ComboBox( this );
      this.tools = Steps.availableStarTools();
      this.tools.forEach( function( t ) { self.toolCombo.addItem( t ); } );
      var lastTool = this.tools.indexOf( Settings.read( FlyThrough.TOOL_SETTING, DataType_String ) || "" );
      if ( lastTool >= 0 ) this.toolCombo.currentItem = lastTool;
      this.toolCombo.onItemSelected = function( i ) { if ( self.tools[i] ) Settings.write( FlyThrough.TOOL_SETTING, DataType_String, self.tools[i] ); self.toolChanged(); };
      this.starsLabel = new Label( this );
      this.starsLabel.text = "";
      this.starsLabel.toolTip = "<p>Detected: stars found in the image. Moving: those with a Gaia distance near enough to move. " +
         "In the background: catalogued stars too far to move, which stay with the nebula or galaxy.</p>";
      this.found = this.group( "Target", [
         this.row( [ this.targetLabel, "stretch" ] ),
         this.row( [ this.starsLabel, "stretch" ] ),
         this.row( [ this.label( "Type:" ), this.typeCombo, this.label( "Distance (pc):" ), this.distanceEdit, this.distanceNote, "stretch" ] ),
         this.row( [ this.label( "Star tool:" ), this.toolCombo, "stretch" ] ) ] );
   }

   buildMotion()
   {
      var self = this;
      this.travelEdit = this.edit( "", 70 );
      this.travelTouched = false;
      this.travelEdit.onEditCompleted = function() { self.travelTouched = ( self.travelEdit.text.trim() != "" ); };
      this.travelNote = new Label( this );
      this.travelNote.text = "";
      this.travelEdit.toolTip = "<p>How far the camera moves towards the target, in parsecs (at most 0.9 of a nebula's distance).</p>";
      this.easingCombo = new ComboBox( this );
      this.easingCombo.addItem( "Smooth" );
      this.easingCombo.addItem( "Linear" );
      this.growthEdit = this.edit( String( Fly.GROWTH_DEFAULT ), 50 );
      this.nebulaSpin = new SpinBox( this );
      this.nebulaSpin.minValue = 0; this.nebulaSpin.maxValue = 100;
      this.nebulaSpin.value = Math.round( 100*Fly.BACKDROP_MOTION_DEFAULT );
      this.nebulaSpin.toolTip = "<p>How much the nebula grows as the camera moves in, as a share of " +
         "the growth its real distance would give. 100% is physical and usually far too much to watch; " +
         "the stars always keep their real motion.</p>";
      this.brightCheck = new CheckBox( this );
      this.brightCheck.text = "Brighten approaching stars";
      this.brightCheck.checked = true;
      this.twinkleSpin = new SpinBox( this );
      this.twinkleSpin.minValue = 0; this.twinkleSpin.maxValue = 10;
      this.twinkleSpin.value = Math.round( 100*Fly.TWINKLE_DEFAULT );
      this.twinkleSpin.toolTip = "<p>A slow wobble in each star's brightness, in percent. Not physical -- in space stars do not twinkle -- " +
         "but a touch of life up close. 0 is physical.</p>";
      this.saturationSlider = new Slider( this );
      this.saturationSlider.setRange( 0, 200 );
      this.saturationSlider.value = 100;
      this.saturationSlider.setScaledMinWidth( 160 );
      this.saturationSlider.toolTip = "<p>The stars' colour saturation: 100% as in the image, less toward white, more for richer colour. Their brightness is kept.</p>";
      this.saturationLabel = new Label( this );
      this.saturationLabel.text = "100%";
      this.saturationSlider.onValueUpdated = function( v ) { self.saturationLabel.text = v + "%"; };
      this.bloomSpin = new SpinBox( this );
      this.bloomSpin.minValue = 0; this.bloomSpin.maxValue = 200; this.bloomSpin.value = 100;
      this.bloomSpin.toolTip = "<p>A near star's light past white glows instead of clipping flat: a round core, a soft halo and a faint glare, " +
         "whitening as a camera's sensor does. 0 turns it off.</p>";
      this.blurCheck = new CheckBox( this );
      this.blurCheck.text = "Motion blur";
      this.blurCheck.checked = true;
      this.blurCheck.toolTip = "<p>Stars streak slightly along their path, as a camera with a 180\u00b0 shutter records them.</p>";
      this.starQualityCombo = new ComboBox( this );
      [ "Highest", "High", "Medium" ].forEach( function( t ) { self.starQualityCombo.addItem( t ); } );
      this.starQualityCombo.currentItem = FlyThrough.STAR_QUALITIES.indexOf( FlyThrough.STAR_QUALITY_DEFAULT );
      this.starQualityCombo.toolTip = "<p><b>Highest</b>: every star drawn in full from the 4K working image (for 1080p, computed at 4K and downsampled). " +
                                      "<b>High</b>: drawn from the image shrunk to the video's size, glows sampled only as finely as they need: " +
                                      "the same look, about a third faster. " +
                                      "<b>Medium</b>: the stars drawn at half the video's resolution and scaled up onto the full-size nebula: " +
                                      "softer stars, about three times faster.</p>";
      this.starQualityCombo.onItemSelected = function() { self.redraft(); };
      this.motion = this.group( "Motion", [
         this.row( [ this.label( "Travel (pc):" ), this.travelEdit, this.travelNote, this.label( "Easing:" ), this.easingCombo, "stretch" ] ),
         this.row( [ this.label( "Growth:" ), this.growthEdit, this.label( "Nebula motion (%):" ), this.nebulaSpin, this.brightCheck, "stretch" ] ),
         this.row( [ this.label( "Twinkle (%):" ), this.twinkleSpin, this.label( "Bloom (%):" ), this.bloomSpin, this.blurCheck, "stretch" ] ),
         this.row( [ this.label( "Star colour:" ), this.saturationSlider, this.saturationLabel, "stretch" ] ) ] );
   }

   buildOutput()
   {
      var self = this;
      this.durationSpin = new SpinBox( this );
      this.durationSpin.minValue = 2; this.durationSpin.maxValue = 600; this.durationSpin.value = 20;
      this.fpsCombo = new ComboBox( this );
      FlyThrough.FPS.forEach( function( f ) { self.fpsCombo.addItem( String( f ) ); } );
      this.fpsCombo.currentItem = FlyThrough.FPS.indexOf( 30 );
      this.durationSpin.onValueUpdated = function() { self.refreshEstimate(); };
      this.fpsCombo.onItemSelected = function() { self.refreshEstimate(); };
      this.presetChecks = {};
      this.transferCombos = {};
      this.orientationCombo = new ComboBox( this );
      this.orientationCombo.addItem( "Horizontal" );
      this.orientationCombo.addItem( "Vertical" );
      this.orientationCombo.toolTip = "<p>Vertical turns the YouTube and Exhibition presets portrait (1080\u00d71920, 2160\u00d73840); the social ones keep their shape.</p>";
      this.orientationCombo.onItemSelected = function() { self.refreshPresetLabels(); self.refreshFormats(); self.refreshEstimate(); self.redraft(); };
      this.loopCombo = new ComboBox( this );
      FlyThrough.LOOPS.forEach( function( l ) { self.loopCombo.addItem( l[1] ); } );
      this.loopCombo.toolTip = "<p>Make the video loop, for any preset: fly back out to the start, or crossfade from the end into the start over " +
         Fly.CROSSFADE_SECONDS + " s (a quarter of a short clip) so it only ever moves forward. Music loops with it. " +
         "The Exhibition preset always loops (back and forth when None).</p>";
      this.loopCombo.onItemSelected = function() { self.refreshEstimate(); };
      var rows = [ this.row( [ this.label( "Duration (s):" ), this.durationSpin, this.label( "fps:" ), this.fpsCombo,
                               this.label( "Orientation:" ), this.orientationCombo, "stretch" ] ),
                   this.row( [ this.label( "Loop:" ), this.loopCombo, this.label( "Star quality:" ), this.starQualityCombo, "stretch" ] ) ];   // how the frames are drawn: with the other output choices
      var cells = [];
      FlyThrough.PRESET_ORDER.forEach( function( id )
      {
         var c = new CheckBox( self );
         c.text = FlyThrough.PRESET_LABELS[id];
         c.checked = ( id == "youtube_1080" );
         c.onCheck = function() { self.refreshFormats(); self.refreshEstimate(); };
         var t = new ComboBox( self );
         t.addItem( "HLG" );
         t.addItem( "PQ (HDR10)" );
         t.currentItem = ( Fly.HDR_DEFAULT_TRANSFER[id] == "pq" ) ? 1 : 0;
         t.toolTip = "<p>HDR transfer for this preset: HLG looks right on SDR screens too; PQ is for a display you control.</p>";
         self.presetChecks[id] = c;
         self.transferCombos[id] = t;
         cells.push( self.row( [ c, t ] ) );
      } );
      for ( var k = 0; k < cells.length; k += 2 )           // two presets a row: the dialog has to fit a laptop screen
         rows.push( this.row( [ this.label( "" ), cells[k] ].concat( cells[k + 1] ? [ cells[k + 1] ] : [] ).concat( [ "stretch" ] ) ) );
      this.dynamicCombo = new ComboBox( this );
      this.dynamicCombo.addItem( "SDR" );
      this.dynamicCombo.addItem( "HDR" );
      this.dynamicCombo.onItemSelected = function() { self.refreshHdr(); self.refreshFormats(); self.refreshEstimate(); };
      this.peakSpin = new SpinBox( this );
      this.peakSpin.minValue = 400; this.peakSpin.maxValue = 4000; this.peakSpin.stepSize = 100;
      this.peakSpin.value = Fly.HDR_PEAK_DEFAULT;
      this.peakSpin.toolTip = "<p>Peak brightness for PQ, in nits. HLG is relative to a 1000-nit display.</p>";
      this.dynamicCombo.toolTip = "<p>SDR, or HDR (HLG or PQ per preset). The draft preview is SDR; judge HDR in the video.</p>";
      this.starHdrCheck = new CheckBox( this );
      this.starHdrCheck.text = "Stars into HDR headroom";
      this.starHdrCheck.checked = true;
      this.starHdrCheck.toolTip = "<p>In HDR, the bright stars reach past SDR white towards the peak, each by its Gaia magnitude " +
         "(the brightest to the peak, 5 magnitudes fainter not at all); the nebula or galaxy keeps its tone.</p>";
      rows.push( this.row( [ this.label( "Dynamic range:" ), this.dynamicCombo, this.label( "Peak (nits):" ), this.peakSpin, this.starHdrCheck, "stretch" ] ) );
      this.logoEdit = this.edit( Settings.read( FlyThrough.LOGO_SETTING, DataType_String ) || "", 0 );
      this.logoEdit.toolTip = "<p>A logo drawn on every frame (PNG transparency is kept), sized and spaced for each preset.</p>";
      this.logoButton = new PushButton( this );
      this.logoButton.text = "Logo\u2026";
      this.logoButton.onClick = function()
      {
         var d = new OpenFileDialog;
         d.caption = "Loom Fly-Through: Logo";
         d.filters = [ [ "Images", "*.png *.tif *.tiff *.xisf *.jpg *.jpeg" ] ];
         if ( d.execute() ) self.safely( function() { self.useLogo( d.fileName ); } );   // mid-job too: redraft() keeps it for when the job ends
      };
      this.logoPlaceCombo = new ComboBox( this );
      [ "Off", "Center", "Top left", "Top middle", "Top right", "Bottom left", "Bottom middle", "Bottom right" ].forEach( function( t ) { self.logoPlaceCombo.addItem( t ); } );
      this.logoPlaceCombo.currentItem = Math.max( 0, Fly.LOGO_PLACES.indexOf( Settings.read( FlyThrough.LOGO_PLACE_SETTING, DataType_String ) || "" ) + 1 );
      this.logoPlaceCombo.onItemSelected = function( i ) { Settings.write( FlyThrough.LOGO_PLACE_SETTING, DataType_String, i > 0 ? Fly.LOGO_PLACES[i - 1] : "off" ); self.logoChanged(); };
      this.logoOpacity = new Slider( this );
      this.logoOpacity.setRange( 0, 100 );
      this.logoOpacity.value = 100;
      this.logoOpacity.setScaledMinWidth( 100 );
      this.logoOpacity.toolTip = "<p>The logo's opacity; its own transparency is kept.</p>";
      this.logoOpacityLabel = new Label( this );
      this.logoOpacityLabel.text = "100%";
      this.logoOpacity.onValueUpdated = function( v ) { self.logoOpacityLabel.text = v + "%"; self.logoChanged(); };
      rows.push( this.row( [ this.label( "Logo:" ), this.logoEdit, this.logoButton, this.logoPlaceCombo ] ) );
      this.logoDelaySpin = new SpinBox( this );
      this.logoDelaySpin.minValue = 0; this.logoDelaySpin.maxValue = 600; this.logoDelaySpin.value = 0;
      this.logoDelaySpin.toolTip = "<p>Fade the logo in after this many seconds (over one second). 0 shows it from the start.</p>";
      rows.push( this.row( [ this.label( "Opacity:" ), this.logoOpacity, this.logoOpacityLabel, this.label( "Fade in after (s):" ), this.logoDelaySpin, "stretch" ] ) );
      this.musicEdit = this.edit( "", 0 );
      this.musicEdit.toolTip = "<p>Music under the video: looped if it is shorter, cut to the video's length.</p>";
      this.musicButton = new PushButton( this );
      this.musicButton.text = "Music\u2026";
      this.musicButton.onClick = function() { self.browseFile( self.musicEdit, "Music", [ [ "Audio", "*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus" ] ], null ); };
      this.fadeCheck = new CheckBox( this );
      this.fadeCheck.text = "Fade in/out";
      this.fadeCheck.checked = true;
      this.fadeCheck.toolTip = "<p>Fade the music in and out over " + Fly.AUDIO_FADE + " s (a quarter of a short clip).</p>";
      rows.push( this.row( [ this.label( "Music:" ), this.musicEdit, this.musicButton, this.fadeCheck ] ) );
      this.folderEdit = this.edit( Settings.read( "Loom/flyFolder", DataType_String ) || "", 0 );
      this.folderButton = new PushButton( this );
      this.folderButton.text = "Folder…";
      this.folderButton.onClick = function()
      {
         var d = new GetDirectoryDialog;
         d.caption = "Output folder";
         if ( d.execute() ) self.folderEdit.text = d.directory;
      };
      rows.push( this.row( [ this.label( "Output:" ), this.folderEdit, this.folderButton ] ) );
      this.output = this.group( "Output", rows );
   }

   /* The preset names with the frame they will render at, in the chosen orientation. */
   refreshPresetLabels()
   {
      var turn = this.orientationCombo.currentItem == 1 ? "vertical" : "horizontal", self = this;
      FlyThrough.PRESET_ORDER.forEach( function( id )
      {
         var p = Fly.presetSpec( id, turn );
         self.presetChecks[id].text = FlyThrough.PRESET_LABELS[id].replace( /\d+×\d+/, p.w + "×" + p.h );
      } );
   }

   refreshHdr()
   {
      var hdr = ( this.dynamicCombo.currentItem == 1 ), self = this;
      this.peakSpin.enabled = hdr;
      if ( this.starHdrCheck ) this.starHdrCheck.enabled = hdr;
      FlyThrough.PRESET_ORDER.forEach( function( id ) { self.transferCombos[id].visible = hdr; } );
   }

   buildVideo()
   {
      var self = this;
      this.ffmpegLabel = new Label( this );
      this.ffmpegLabel.text = this.ffmpeg ? ( "ffmpeg: " + this.ffmpeg ) : Fly.ffmpegInstallHint( Util.PLATFORM );
      this.ffmpegButton = new PushButton( this );
      this.ffmpegButton.text = "Browse…";
      this.ffmpegButton.onClick = function() { self.browseFfmpeg(); };
      this.videoCheck = new CheckBox( this );
      this.videoCheck.text = "Create video";
      this.videoCheck.checked = ( this.ffmpeg != null );
      this.videoCheck.enabled = ( this.ffmpeg != null );
      this.formatCombo = new ComboBox( this );
      this.qualityCombo = new ComboBox( this );
      this.qualityCombo.addItem( "High" );
      this.qualityCombo.addItem( "Standard" );
      this.video = this.group( "Video", [ this.row( [ this.ffmpegLabel, "stretch", this.ffmpegButton ] ),
         this.row( [ this.videoCheck, this.label( "Format:" ), this.formatCombo, this.label( "Quality:" ), this.qualityCombo, "stretch" ] ) ] );
   }

   browseFfmpeg()
   {
      var d = new OpenFileDialog;
      d.caption = "ffmpeg";
      d.filters = [ [ "ffmpeg", Util.isWindows() ? "ffmpeg.exe" : "ffmpeg" ] ];
      if ( !d.execute() || !Render.isWorkingFfmpeg( d.fileName ) )
         return;
      this.ffmpeg = d.fileName;
      Settings.write( Render.FFMPEG_SETTING, DataType_String, this.ffmpeg );
      this.encoders = Render.ffmpegEncoders( this.ffmpeg );
      this.ffmpegLabel.text = "ffmpeg: " + this.ffmpeg;
      this.videoCheck.enabled = true;
      this.videoCheck.checked = true;
      this.refreshFormats();
   }

   /* Only the formats this ffmpeg can encode, and valid for SDR or HDR. */
   refreshFormats()
   {
      var hdr = ( this.dynamicCombo.currentItem == 1 ), self = this;
      var formats = this.ffmpeg ? Fly.availableFormats( this.encoders, hdr ) : [];
      var turn = this.orientationCombo && this.orientationCombo.currentItem == 1 ? "vertical" : "horizontal";
      var widest = Math.max.apply( null, [ 0 ].concat( this.checkedPresets().map( function( id ) { var p = Fly.presetSpec( id, turn ); return Math.max( p.w, p.h ); } ) ) );
      var def = Fly.defaultFormat( formats, widest );
      this.formatIds = formats.map( function( f ) { return f.id; } );
      this.formatCombo.clear();
      formats.forEach( function( f ) { self.formatCombo.addItem( f.label ); } );
      if ( def != null ) this.formatCombo.currentItem = this.formatIds.indexOf( def );
   }

   checkedPresets()
   {
      var self = this;
      return FlyThrough.PRESET_ORDER.filter( function( id ) { return self.presetChecks[id] && self.presetChecks[id].checked; } );
   }

   buildPreview()
   {
      var self = this;
      this.player = new FlyThrough.Player( this );
      this.playButton = new PushButton( this );
      this.playButton.text = "Play";
      this.playButton.onClick = function()
      {
         if ( self.player.playing ) { self.player.pause(); self.playButton.text = "Play"; }
         else if ( !self.hasDraft ) { if ( self.imageWindow ) self.guarded( function() { self.draft(); } ); }   // nothing to play yet: make it
         else { self.player.play(); self.playButton.text = "Pause"; }
      };
      this.scrubber = new Slider( this );
      this.scrubber.minValue = 0;
      this.scrubber.maxValue = 0;
      this.scrubber.onValueUpdated = function( v ) { if ( !self.player.playing ) self.player.seek( v ); };
      this.player.onFrame = function( i ) { self.scrubber.value = i; };
      this.preview = this.group( "Draft preview", [ this.player, this.row( [ this.playButton, this.scrubber ] ) ] );
   }

   buildButtons()
   {
      var self = this;
      this.status = new Label( this );
      this.status.text = "";
      this.estimateLabel = new Label( this );
      this.estimateLabel.text = "";
      this.draftButton = new PushButton( this );
      this.draftButton.text = "Draft";
      this.draftButton.onClick = function() { self.guarded( function() { self.draft(); } ); };
      this.renderButton = new PushButton( this );
      this.renderButton.text = "Render";
      this.renderButton.onClick = function()
      {
         if ( self.busy ) { self.requestCancel(); return; }
         self.guarded( function() { self.renderAll(); } );
      };
      this.closeButton = new PushButton( this );
      this.closeButton.text = "Close";
      this.closeButton.onClick = function() { if ( !self.busy && self.closing() ) self.cancel(); };
      this.bar = new FlyThrough.ProgressBar( this );
      this.buttons = new VerticalSizer;
      this.buttons.spacing = 6;
      this.buttons.add( this.row( [ this.estimateLabel, "stretch", this.draftButton, this.renderButton, this.closeButton ] ) );
      // the progress sits under the image, where it is always on screen
      this.progressBox = new VerticalSizer;
      this.progressBox.spacing = 4;
      this.progressBox.add( this.bar );
      this.progressBox.add( this.status );
      // the window's own close (title bar, Escape) goes the way the Close button does
      this.onClose = function() { return self.closing(); };
   }

   /*
    * What closing does, from the Close button or the window's own close:
    * save the options and, mid-job, cancel the job rather than leave it
    * running unseen. Returns true, always: PJSR keeps the window open when
    * onClose returns anything else, undefined included (a Windows tester's
    * title-bar "x" did nothing), and a failed save must not keep it open.
    */
   closing()
   {
      if ( this.busy ) this.requestCancel();
      try { this.saveOptions(); }
      catch ( e ) { Util.warn( "fly", "the options were not saved: " + ( e.message || e ) ); }
      return true;
   }

   layOut()
   {
      var left = new VerticalSizer;
      left.spacing = 6;
      [ this.imageBox, this.progressBox, this.hints, this.found, this.motion, this.output, this.video ].forEach( function( c ) { left.add( c ); } );
      var top = new HorizontalSizer;
      top.spacing = 8;
      top.add( left );
      top.add( this.preview, 100 );
      this.sizer = new VerticalSizer;
      this.sizer.margin = 8;
      this.sizer.spacing = 8;
      this.sizer.add( top, 100 );
      this.sizer.add( this.buttons );
      this.adjustToContents();
   }

   /* Errors become a message, not a script failure; nothing starts while a job runs. */
   guarded( fn )
   {
      if ( this.busy ) return;
      this.safely( fn );
   }

   /* Errors become a message, not a script failure; a cancelled job just says so. */
   safely( fn )
   {
      try { fn(); }
      catch ( e )
      {
         if ( FlyThrough.isCancel( e ) ) { this.cancelled(); return; }
         this.status.text = "";
         ( new MessageBox( String( e.message || e ), "Loom Fly-Through", StdIcon_Error, StdButton_Ok ) ).execute();
      }
   }

   /* The job was cancelled: said on the bar and the status line. */
   cancelled()
   {
      this.bar.set( 1, "Cancelled" );
      this.status.text = "Cancelled.";
   }

   /*
    * Cancel (the Render button mid-job, or closing): the job stops at its
    * next step -- a running star removal once it returns -- and no redraft
    * follows. Nothing asks PixInsight to abort: in its window that pops up
    * "Do you want to abort the current process?", and it does not stop a
    * star tool early anyway (measured: StarXTerminator ran to its end).
    */
   requestCancel()
   {
      this.cancelRequested = true;
      this.redraftPending = false;
   }

   /* Stops at this point when the job was cancelled. */
   stopIfCancelled()
   {
      if ( this.cancelRequested ) throw FlyThrough.cancel();
   }

   /*
    * The star tool was changed. During an extraction the stars are
    * extracted again with the new tool as soon as the running one returns
    * (ensureBuilt; its stars are cached for their tool); otherwise the new
    * tool is used by the next Draft or Render.
    */
   toolChanged()
   {
      if ( !this.extracting ) return;
      this.toolSwitch = this.tools[this.toolCombo.currentItem];
      this.bar.set( null, this.switchingText() );
   }

   switchingText()
   {
      return "Switching to " + this.toolSwitch + " after the current star removal stops\u2026";
   }

   /*
    * Runs fn -- the image's analysis and star extraction -- with the dialog
    * usable: every option stays editable (a change is read when the analysis
    * ends, or redrafts), while what depends on the analysis is greyed out
    * (Draft, Play, the star counts, the image choice) and Render is Cancel.
    * Nested calls run inside the outer one. Controls come back however it
    * ends; a cancel ends it here.
    */
   analysing( fn )
   {
      if ( this.analysisDepth > 0 ) return fn();
      var wasBusy = this.busy;
      if ( !wasBusy ) this.cancelRequested = false;
      this.busy = true;
      this.analysisDepth = 1;
      this.refreshJobControls();
      try
      {
         var r = fn();
         this.stopIfCancelled();
         return r;
      }
      finally
      {
         this.analysisDepth = 0;
         this.busy = wasBusy;
         this.refreshJobControls();
      }
   }

   /* What a running job leaves usable (see analysing and run). */
   refreshJobControls()
   {
      var inAnalysis = this.analysisDepth > 0, has = ( this.imageWindow != null );
      this.draftButton.enabled = has && !this.busy;
      this.renderButton.text = this.busy ? "Cancel" : "Render";
      this.renderButton.enabled = has || this.busy;
      this.playButton.enabled = !inAnalysis;
      this.starsLabel.enabled = !inAnalysis;
      this.imageList.enabled = this.openButton.enabled = !inAnalysis;   // another image cannot be taken until this one's analysis ends
      // the bar's pulse keeps moving while a PixInsight process runs (it lets timers through)
      if ( inAnalysis && !this.pulseTimer )
      {
         var self = this;
         this.pulseTimer = new Timer;
         this.pulseTimer.interval = 0.25;
         this.pulseTimer.periodic = true;
         this.pulseTimer.onTimeout = function() { if ( self.bar ) self.bar.update(); };
         this.pulseTimer.start();
      }
      else if ( !inAnalysis && this.pulseTimer ) this.stopPulse();
   }

   stopPulse()
   {
      if ( !this.pulseTimer ) return;
      this.pulseTimer.stop();
      this.pulseTimer.onTimeout = null;
      this.pulseTimer = null;
   }

   number( edit ) { var v = parseFloat( edit.text ); return isFinite( v ) ? v : null; }

   /* Everything a render needs, read from the controls. */
   options()
   {
      var self = this, transfer = {};
      FlyThrough.PRESET_ORDER.forEach( function( id ) { transfer[id] = self.transferCombos[id].currentItem == 1 ? "pq" : "hlg"; } );
      return { travel: this.number( this.travelEdit ), easing: this.easingCombo.currentItem == 1 ? "linear" : "smoothstep",
               growth: this.number( this.growthEdit ) || 0, brightening: this.brightCheck.checked,
               backdropMotion: this.nebulaSpin.value/100,
               twinkle: this.twinkleSpin.value/100, motionBlur: this.blurCheck.checked, bloom: this.bloomSpin.value/100,
               starSaturation: this.saturationSlider.value/100,
               starHdr: this.starHdrCheck.checked,
               starQuality: FlyThrough.STAR_QUALITIES[this.starQualityCombo.currentItem],
               logoOpacity: this.logoOpacity.value/100, logoDelay: this.logoDelaySpin.value,
               logoPath: this.logoEdit.text.trim(), logoPlace: this.logoPlaceCombo.currentItem > 0 ? Fly.LOGO_PLACES[this.logoPlaceCombo.currentItem - 1] : "off",
               music: { path: this.musicEdit.text.trim(), fade: this.fadeCheck.checked },
               duration: this.durationSpin.value, fps: FlyThrough.FPS[this.fpsCombo.currentItem],
               presets: this.checkedPresets(), dir: this.folderEdit.text.trim(),
               orientation: this.orientationCombo.currentItem == 1 ? "vertical" : "horizontal",
               loop: FlyThrough.LOOPS[this.loopCombo.currentItem][0],
               dynamic: this.dynamicCombo.currentItem == 1 ? "hdr" : "sdr", peak: this.peakSpin.value, hdrTransfer: transfer,
               video: this.videoCheck.checked && this.ffmpeg != null, ffmpeg: this.ffmpeg,
               format: this.formatIds[this.formatCombo.currentItem] || null,
               quality: this.qualityCombo.currentItem == 1 ? "standard" : "high",
               colour: this.built ? this.built.colour : null,
               tool: this.tools[this.toolCombo.currentItem], distance: this.number( this.distanceEdit ),
               type: this.typeCombo.currentItem == 1 ? "galaxy" : "nebula" };
   }

   /*
    * What Analyse is told: the type and distance only once the user has set
    * them, so an NGC/IC galaxy (PGC number) is recognised and a found
    * cluster is used unless overridden.
    */
   choices()
   {
      var c = {};
      if ( this.typeTouched ) c.type = this.typeCombo.currentItem == 1 ? "galaxy" : "nebula";
      if ( this.distanceTouched && this.number( this.distanceEdit ) > 0 ) c.distance = this.number( this.distanceEdit );
      if ( this.needsHints )
      {
         c.hints = { ra: Fly.parseAngle( this.raEdit.text, true ), dec: Fly.parseAngle( this.decEdit.text, false ),
                     focal: this.number( this.focalEdit ), pixel: this.number( this.pixelEdit ) };
         if ( !( c.hints.ra != null && c.hints.dec != null && c.hints.focal > 0 && c.hints.pixel > 0 ) )
            throw new Error( "Solving needs the centre (RA and Dec, or an object name), and a positive focal length and pixel size." );
      }
      return c;
   }

   requireTool()
   {
      if ( this.tools.length == 0 )
         throw new Error( "No star removal tool is installed. Fly-Through needs StarXTerminator, StarNet2 or SyQon Starless." );
   }

   analyse()
   {
      var self = this;
      this.analysing( function() { self.analyseImage(); } );
   }

   analyseImage()
   {
      this.requireTool();
      var choices = this.choices(), progress = this.progressFor();
      this.status.text = "Analysing\u2026";
      if ( !this.work )
      {
         // the image's solved working copy from the cache, else resampled (and solved below)
         this.cacheDirPath = FlyThrough.cacheDir( FlyThrough.cacheRoot(), this.imageWindow );
         progress.stage( "Opening the image's working copy", 0, 0 );
         this.work = FlyThrough.loadWork( this.cacheDirPath );
         this.workCached = ( this.work != null );
         if ( !this.work )
         {
            progress.stage( "Making a smaller working copy of the image", 0, 0 );
            this.work = Sky.workingCopy( this.imageWindow );
         }
         var wi = this.work.window.mainView.image;
         this.imageLabel.text = "Image: " + this.imageWindow.mainView.id + " \u2014 working at " + wi.width + "\u00d7" + wi.height +
                                ( this.workCached ? " (solved before)" : "" );
      }
      if ( choices.hints )
         choices.hints.pixel /= this.work.scale;         // the working copy's pixels are larger
      var id = FlyThrough.identify( this.work.window, choices, progress );
      if ( !this.workCached && Sky.projector( this.work.window ) )
      {
         FlyThrough.saveWork( this.cacheDirPath, this.work );          // solved: kept for next time
         this.workCached = true;
      }
      FlyThrough.touch( this.cacheDirPath );
      FlyThrough.prune( FlyThrough.cacheRoot(), FlyThrough.CACHE_KEEP );
      this.bar.set( 1, "Analysed" );
      this.hints.visible = ( Sky.projector( this.work.window ) == null );
      this.id = id;
      this.targetLabel.text = FlyThrough.describeTarget( id );
      // a type or distance set while it was analysed is the user's: kept
      if ( !this.typeTouched ) this.typeCombo.currentItem = ( id.type == "galaxy" ) ? 1 : 0;
      if ( id.D != null && isFinite( id.D ) && !this.distanceTouched ) this.distanceEdit.text = String( Math.round( id.D ) );
      this.distanceNote.text = FlyThrough.describeDistance( id );
      this.status.text = id.sources.length + " Gaia stars in the field" + ( id.sources.origin ? " (" + ( id.sources.origin == "online" ? "Gaia DR3 online" : "Gaia " + id.sources.origin ) + ")" : "" ) + ".";
   }

   /*
    * What renders is what is on screen: the type, distance and travel are
    * read from the controls every time, pushed into the scene, and travel
    * follows the distance until the user sets it. Returns the options.
    */
   prepare()
   {
      var self = this;
      return this.analysing( function() { return self.prepareScene(); } );
   }

   prepareScene()
   {
      this.requireTool();
      if ( this.id == null ) this.analyse();
      var self = this;
      function distance()
      {
         var galaxy = ( self.typeCombo.currentItem == 1 ), typed = self.number( self.distanceEdit );
         var D = galaxy ? Infinity : ( typed > 0 ? typed : null );
         if ( D == null )
            throw new Error( "Type the nebula's distance in parsecs first." );
         return D;
      }
      distance();
      this.ensureBuilt();
      // read after the build: a type or distance changed while it ran is the one that counts
      var D = distance(), galaxy = ( D === Infinity );
      this.id.type = galaxy ? "galaxy" : "nebula";
      this.id.D = D;
      if ( !galaxy && !( this.id.cluster && Math.round( this.id.cluster.distance ) == Math.round( D ) ) )
         this.id.distanceSource = "typed";
      this.distanceNote.text = FlyThrough.describeDistance( this.id );
      var want = this.travelTouched ? this.number( this.travelEdit ) : null;
      if ( !( want > 0 ) ) want = Fly.defaultTravel( this.id.type, D );
      var cap = Fly.clampTravel( want, this.id.type, D );
      this.travelEdit.text = String( Math.round( cap.travel ) );
      this.travelNote.text = cap.clamped ? "capped at 0.9 \u00d7 the distance" : "";
      this.built.scene.D = D;
      return Object.assign( this.options(), { travel: cap.travel } );
   }

   /* The scene, built once per star tool; its distance is set by prepare(). */
   ensureBuilt()
   {
      var o = this.options();
      if ( this.built && this.builtTool == o.tool )
         return;
      this.releaseBuilt();
      for ( ;; )
      {
         this.stopIfCancelled();
         o = this.options();
         this.status.text = "Removing stars and cutting sprites\u2026";
         processEvents();
         // the stars extracted with this tool before, else extracted now and kept
         var built = FlyThrough.loadBuilt( this.cacheDirPath, o.tool, this.work.window, this.imageWindow );
         if ( built ) break;
         built = this.extract( o.tool );
         if ( built ) break;                   // null: the tool was changed while it ran, so again with the new one
      }
      this.built = built;
      this.bar.set( 1, "Scene ready" );
      this.builtTool = o.tool;
      this.starsLabel.text = Fly.describeStars( this.built.counts );
      this.bar.set( null, "Timing a frame for the render estimate" );
      this.msPerMp = FlyThrough.measureSpeed( this.built.scene, o );
      this.status.text = this.built.counts.placed + " stars placed, " + this.built.counts.blended + " blended, " +
                         this.built.counts.backdrop + " in the backdrop. " + this.built.colourNote +
                         ( this.built.counts.placed < 50 ? " Few stars could be placed (under 50): the depth will be subtle." : "" );
      this.refreshEstimate();
   }

   /*
    * The stars extracted with `tool` (FlyThrough.build), and cached. Null
    * when the tool was changed while it ran (toolChanged): its stars are
    * still cached for their tool, but not used. A cancel ends the job once
    * the call returns.
    */
   extract( tool )
   {
      var built = null, failure = null;
      this.extracting = tool;
      this.toolSwitch = null;
      try { built = FlyThrough.build( this.work.window, this.id, { tool: tool, colourFrom: this.imageWindow }, this.progressFor() ); }
      catch ( e ) { failure = e; }             // the tool failed, or the user aborted it from PixInsight's console
      finally { this.extracting = null; }
      if ( failure ) { this.toolSwitch = null; this.stopIfCancelled(); throw failure; }
      try { FlyThrough.saveBuilt( this.cacheDirPath, tool, built ); }
      catch ( e ) { console.warningln( "Loom Fly-Through: the stars could not be cached: " + e ); }
      if ( this.toolSwitch ) { this.toolSwitch = null; ( built.windows || [] ).forEach( function( w ) { try { w.forceClose(); } catch ( e ) {} } ); return null; }
      this.stopIfCancelled();
      return built;
   }

   /*
    * A progress reporter for one job: every stage report moves the bar,
    * with time left extrapolated from the time spent in that stage.
    */
   progressFor()
   {
      var self = this, current = null, since = Date.now(), shown = 0;
      function stage( name, done, total, kept )
      {
         if ( self.toolSwitch ) { self.bar.set( null, self.switchingText() ); return; }   // what happens next, until it does
         if ( name != current ) { current = name; since = Date.now(); }
         self.bar.set( Fly.progressFraction( done, total ), Fly.progressText( name, done, total, Date.now() - since, kept ) );
      }
      return {
         stage: stage,
         isCancelled: function() { return self.cancelRequested; },
         onFrame: function( k, n, id, kept ) { stage( id ? "Rendering " + ( FlyThrough.PRESET_LABELS[id] || id ) : "Drafting", k, n, kept ); },
         // a render's finished frames in the preview, at most every PREVIEW_EVERY ms (turning a full frame into a bitmap costs)
         onImage: function( img, id, transfer )
         {
            if ( Date.now() - shown < FlyThrough.PREVIEW_EVERY ) return;
            shown = Date.now();
            self.player.setFrames( [ img.render() ], 1, false );
            // an HDR frame's PQ/HLG signal on an SDR screen looks flat: say what it is
            self.player.setBadge( ( transfer == "pq" || transfer == "hlg" ) ? "HDR Preview" : "" );
         },
         onEncode: function( k, n ) { stage( "Encoding the video", k, n ); }
      };
   }

   /* The render-time estimate for the chosen presets, once the speed is measured. */
   refreshEstimate()
   {
      if ( !this.estimateLabel ) return;
      if ( !( this.msPerMp > 0 ) ) { this.estimateLabel.text = ""; return; }
      var o = this.options();
      var ms = FlyThrough.estimateMs( this.msPerMp, o.presets, o.duration, o.fps, o.dynamic == "hdr", o.orientation, o.loop );
      this.estimateLabel.text = "Estimated render: about " + Math.max( 1, Math.round( ms/60000 ) ) + " min";
   }

   /* Drafts the options on screen; again while a change made during the draft (redraft) left it stale. */
   draft()
   {
      var self = this;
      do
      {
         this.redraftPending = false;
         var o = this.prepare();
         this.drafting = true;
         try { this.run( function( progress ) { return self.makeDraft( o, progress ); } ); }
         finally { this.drafting = false; }
      }
      while ( this.redraftPending );
   }

   /* A file for `edit`, remembered under `setting` when given. */
   browseFile( edit, caption, filters, setting )
   {
      var d = new OpenFileDialog;
      d.caption = "Loom Fly-Through: " + caption;
      d.filters = filters;
      if ( !d.execute() ) return;
      edit.text = d.fileName;
      if ( setting ) Settings.write( setting, DataType_String, d.fileName );
      this.redraft();
   }

   /*
    * The options the dialog restores next time: every control but those that
    * belong to one image (type, distance, travel, the solve hints).
    */
   optionControls()
   {
      var self = this, list = [
         [ "starSaturation", this.saturationSlider, "value" ], [ "easing", this.easingCombo, "currentItem" ], [ "growth", this.growthEdit, "text" ], [ "nebula", this.nebulaSpin, "value" ],
         [ "brighten", this.brightCheck, "checked" ], [ "twinkle", this.twinkleSpin, "value" ], [ "bloom", this.bloomSpin, "value" ],
         [ "blur", this.blurCheck, "checked" ], [ "starHdr", this.starHdrCheck, "checked" ], [ "starQuality", this.starQualityCombo, "currentItem" ], [ "duration", this.durationSpin, "value" ], [ "fps", this.fpsCombo, "currentItem" ],
         [ "orientation", this.orientationCombo, "currentItem" ], [ "loopMode", this.loopCombo, "currentItem" ],
         [ "dynamic", this.dynamicCombo, "currentItem" ], [ "peak", this.peakSpin, "value" ], [ "logoOpacity", this.logoOpacity, "value" ], [ "logoDelay", this.logoDelaySpin, "value" ],
         [ "music", this.musicEdit, "text" ], [ "fade", this.fadeCheck, "checked" ], [ "video", this.videoCheck, "checked" ],
         [ "quality", this.qualityCombo, "currentItem" ] ];
      FlyThrough.PRESET_ORDER.forEach( function( id )
      {
         list.push( [ "preset_" + id, self.presetChecks[id], "checked" ] );
         list.push( [ "transfer_" + id, self.transferCombos[id], "currentItem" ] );
      } );
      return list;
   }

   /* Saves the options (on closing and on rendering, never on a mere release: the suite makes many dialogs). */
   saveOptions()
   {
      var o = {};
      this.optionControls().forEach( function( c ) { o[c[0]] = c[1][c[2]]; } );
      o.format = this.formatIds[this.formatCombo.currentItem] || null;
      Settings.write( FlyThrough.OPTIONS_SETTING, DataType_String, JSON.stringify( o ) );
   }

   restoreOptions()
   {
      var o = null;
      try { o = JSON.parse( Settings.read( FlyThrough.OPTIONS_SETTING, DataType_String ) || "null" ); } catch ( e ) {}
      if ( !o ) return;
      this.optionControls().forEach( function( c ) { if ( o[c[0]] !== undefined ) c[1][c[2]] = o[c[0]]; } );
      this.logoOpacityLabel.text = this.logoOpacity.value + "%";
      this.saturationLabel.text = this.saturationSlider.value + "%";
      this.refreshPresetLabels();
      this.refreshHdr();
      this.refreshFormats();
      if ( o.format && this.formatIds.indexOf( o.format ) >= 0 ) this.formatCombo.currentItem = this.formatIds.indexOf( o.format );
   }

   /* The objects typed for image files, by path. */
   objectCache()
   {
      try { return JSON.parse( Settings.read( FlyThrough.OBJECTS_SETTING, DataType_String ) || "{}" ) || {}; } catch ( e ) { return {}; }
   }

   /* The object the image's file (or view) name names, into the hints: a start, which the user can change. */
   objectFromName()
   {
      if ( this.ngcIc === undefined ) this.ngcIc = Sky.readNgcIc();
      var hit = Fly.objectFromFileName( this.imageWindow.filePath || this.imageWindow.mainView.id, this.ngcIc || [] );
      if ( !hit ) return;
      this.objectEdit.text = hit.name || hit.id;
      this.raEdit.text = hit.ra.toFixed( 4 );
      this.decEdit.text = hit.dec.toFixed( 4 );
      this.objectMatch.text = hit.id + ( hit.name ? " \u00b7 " + hit.name : "" ) + " (from the file name)";
   }

   /* The key an image's hints are remembered under: its file, or its name when it was never saved (for the session). */
   imageKey()
   {
      if ( !this.imageWindow ) return null;
      return this.imageWindow.filePath || ( "view:" + this.imageWindow.mainView.id );
   }

   /* Remembers this image's solve hints -- object, centre, focal length, pixel size -- so its solve starts by itself next time. */
   rememberObject()
   {
      var key = this.imageKey(), ra = Fly.parseAngle( this.raEdit.text, true ), dec = Fly.parseAngle( this.decEdit.text, false );
      if ( !key || ra == null || dec == null ) return;
      var cache = this.objectCache();
      cache[key] = { object: this.objectEdit.text.trim(), ra: ra, dec: dec,
                     focal: this.number( this.focalEdit ), pixel: this.number( this.pixelEdit ) };
      Settings.write( FlyThrough.OBJECTS_SETTING, DataType_String, JSON.stringify( cache ) );
   }

   /* This image's remembered hints, into the fields (its own focal length and pixel size over the rig last used). */
   recallObject()
   {
      var key = this.imageKey(), e = key ? this.objectCache()[key] : null;
      if ( !e ) return;
      this.objectEdit.text = e.object || "";
      this.raEdit.text = e.ra.toFixed( 4 );
      this.decEdit.text = e.dec.toFixed( 4 );
      if ( e.focal > 0 ) this.focalEdit.text = String( e.focal );
      if ( e.pixel > 0 ) this.pixelEdit.text = String( e.pixel );
      if ( e.object ) this.lookUpObject();
   }

   /* A logo file chosen: remembered, placed bottom right if its place was Off, and shown at once. */
   useLogo( path )
   {
      this.logoEdit.text = path;
      Settings.write( FlyThrough.LOGO_SETTING, DataType_String, path );
      if ( this.logoPlaceCombo.currentItem == 0 )
      {
         this.logoPlaceCombo.currentItem = Fly.LOGO_PLACES.indexOf( "bottom-right" ) + 1;
         Settings.write( FlyThrough.LOGO_PLACE_SETTING, DataType_String, "bottom-right" );
      }
      this.logoChanged();
   }

   /* The logo or its place changed: the draft is drawn again, or the still shown with it. */
   logoChanged()
   {
      if ( this.hasDraft ) { this.redraft(); return; }
      if ( this.busy ) this.redraft();          // the analysis's draft, about to be made or being made, is drawn again
      if ( this.imageWindow && this.player && !this.drafting ) this.player.setFrames( [ this.stillWithLogo( this.imageWindow ) ], 30, false );
   }

   /* The preview's still of the image, the logo drawn over it (its transparency kept). */
   stillWithLogo( window )
   {
      var bmp = FlyThrough.still( window ), logo = null;
      try { logo = FlyThrough.readLogo( this.options() ); }
      catch ( e ) { this.status.text = String( e.message || e ); }
      if ( !logo ) return bmp;
      try
      {
         var o = this.options(), L = Render.logoLayer( logo, bmp.width, bmp.height, o.logoPlace, 3, o.logoOpacity );
         if ( L ) FlyThrough.paintLayer( bmp, L );
      }
      finally { logo.free(); }
      return bmp;
   }

   /*
    * A setting that changes the frame changed: the draft on screen is stale,
    * so draft again. A change made while the dialog is busy (a draft lets
    * events through between its frames) is kept for when the job ends, and
    * a draft it makes stale stops now. It used to be dropped: switching back
    * to Horizontal during the vertical draft left that draft on screen.
    */
   redraft()
   {
      var self = this;
      if ( !this.hasDraft && !this.analysisDepth && !this.drafting ) return;   // nothing drafted, nor about to be
      if ( this.busy )
      {
         this.redraftPending = true;
         if ( this.drafting ) this.cancelRequested = true;
         return;
      }
      this.guarded( function() { self.draft(); } );
   }

   /* Renders the draft for options o and plays it; the preview's still makes way for it. */
   makeDraft( o, progress )
   {
      // the draft made from the same options before, else rendered and kept
      var spec = Fly.presetSpec( o.presets[0] || "youtube_1080", o.orientation, o.loop );
      var sig = Fly.frameSignature( o, spec, [ o.tool, this.built.scene.D ].join( "|" ) );
      var d = this.cacheDirPath ? FlyThrough.loadDraft( this.cacheDirPath, sig ) : null;
      if ( !d )
      {
         o.logoImage = FlyThrough.readLogo( o );
         try { d = FlyThrough.renderDraft( this.built.scene, o, spec, progress ); }
         finally { if ( o.logoImage ) o.logoImage.free(); }
         if ( this.cacheDirPath && !( progress && progress.isCancelled && progress.isCancelled() ) )
            try { FlyThrough.saveDraft( this.cacheDirPath, sig, d ); } catch ( e ) { console.warningln( "Loom Fly-Through: the draft could not be cached: " + e ); }
      }
      this.player.setFrames( d.bitmaps, d.fps, d.pingPong );
      this.hasDraft = true;
      this.scrubber.maxValue = Math.max( 0, d.bitmaps.length - 1 );
      this.player.play();
      this.playButton.text = "Pause";
      return "Draft: " + d.bitmaps.length + " frames at " + d.fps.toFixed( 1 ) + " fps.";
   }

   renderAll()
   {
      var self = this, o = this.prepare();
      if ( o.presets.length == 0 ) throw new Error( "Choose at least one preset." );
      if ( !o.dir || !File.directoryExists( o.dir ) ) throw new Error( "Choose an output folder." );
      Settings.write( "Loom/flyFolder", DataType_String, o.dir );
      // what the scene was made from: the image (and when its file last changed), the star tool, the distance
      var path = this.imageWindow.filePath, when = "";
      try { if ( path && File.exists( path ) ) when = String( ( new FileInfo( path ) ).lastModified.getTime() ); } catch ( e ) {}
      o.sceneKey = [ path || this.imageWindow.mainView.id, when, o.tool, this.built.scene.D ].join( "|" );
      // the object the video is named after: the target found (its id and common name), else what was typed
      var t = this.id && this.id.target;
      o.objectName = t ? t.id + ( t.name ? " " + t.name : "" ) : this.objectEdit.text.trim();
      this.saveOptions();
      // the preview shows the frames as they finish (progressFor's onImage); the draft comes back after
      var draft = { frames: this.player.frames, fps: this.player.fps, pingPong: this.player.pingPong };
      this.player.pause();
      try { this.renderJob( o ); }
      finally { if ( draft.frames.length ) this.player.setFrames( draft.frames, draft.fps, draft.pingPong ); }
   }

   renderJob( o )
   {
      var self = this;
      this.run( function( progress )
      {
         o.logoImage = FlyThrough.readLogo( o );
         try { var res = FlyThrough.renderFinal( self.built.scene, o.presets, o, o.dir, progress ); }
         finally { if ( o.logoImage ) o.logoImage.free(); }
         res.commands.forEach( function( c ) { console.writeln( c ); } );
         res.failed.forEach( function( f ) { console.warningln( "ffmpeg failed for " + f.video + ":\n" + String( f.output ).slice( -2000 ) ); } );
         return ( res.cancelled ? "Cancelled after " : "Wrote " ) + res.written + " frames" +
                ( res.reused ? " (kept " + res.reused + " rendered before)" : "" ) +
                ( res.videos.length ? ", " + res.videos.length + " video(s)" : "" ) +
                ( res.commands.length ? "; the ffmpeg command is in the console" : "" ) +
                ( res.failed.length ? "; " + res.failed.length + " encode(s) failed (see console)" : "" ) + ".";
      } );
   }

   /* Runs a long job with the Render button as Cancel. */
   run( job )
   {
      this.busy = true;
      this.cancelRequested = false;
      this.refreshJobControls();
      try
      {
         this.status.text = job( this.progressFor() );
         this.bar.set( 1, this.cancelRequested ? "Cancelled" : "Done" );
      }
      catch ( e )
      {
         if ( !FlyThrough.isCancel( e ) ) throw e;
         this.cancelled();
      }
      finally
      {
         this.busy = false;
         this.refreshJobControls();
      }
   }

   releaseBuilt()
   {
      if ( this.built )
         this.built.windows.forEach( function( w ) { try { w.forceClose(); } catch ( e ) {} } );
      this.built = null;
   }

   release()
   {
      try
      {
         if ( this.player ) this.player.release();
         if ( this.bar ) this.bar.release();
         this.releaseBuilt();
         if ( this.work ) { try { this.work.window.forceClose(); } catch ( e0 ) {} this.work = null; }
         var self = this;
         [ "draftButton", "renderButton", "closeButton", "logoButton", "musicButton", "playButton", "folderButton", "ffmpegButton", "openButton" ]
            .forEach( function( k ) { if ( self[k] ) self[k].onClick = null; } );
         [ "typeCombo", "dynamicCombo", "fpsCombo", "orientationCombo", "loopCombo", "toolCombo", "logoPlaceCombo" ].forEach( function( k ) { if ( self[k] ) self[k].onItemSelected = null; } );
         if ( this.durationSpin ) this.durationSpin.onValueUpdated = null;
         if ( this.imageList ) this.imageList.onViewSelected = null;
         if ( this.logoOpacity ) this.logoOpacity.onValueUpdated = null;
         if ( this.saturationSlider ) this.saturationSlider.onValueUpdated = null;
         if ( this.autoTimer ) { this.autoTimer.stop(); this.autoTimer.onTimeout = null; this.autoTimer = null; }
         this.stopPulse();
         if ( this.scrubber ) this.scrubber.onValueUpdated = null;
         [ "objectEdit", "distanceEdit", "travelEdit", "raEdit", "decEdit", "focalEdit", "pixelEdit" ].forEach( function( k ) { if ( self[k] ) self[k].onEditCompleted = null; } );
         this.onClose = null;
         this.onShow = null;
         if ( this.presetChecks )
            FlyThrough.PRESET_ORDER.forEach( function( id ) { if ( self.presetChecks[id] ) self.presetChecks[id].onCheck = null; } );
      }
      catch ( e ) { /* releasing must never be the thing that fails */ }
   }
};

function main()
{
   console.show();
   var dlg = new FlyThrough.Dialog( ImageWindow.activeWindow );
   try { dlg.execute(); }
   finally { dlg.release(); }
}

#ifndef LOOM_FLY_UNDER_TEST
main();
#endif
