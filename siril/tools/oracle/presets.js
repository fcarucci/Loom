/*
 * Oracle for loomsiril/presets.py (E1b): the preset, timing, file-naming and
 * progress rules of Fly-Through (Fly.js 71-95, 465-506, 1116-1128,
 * 1232-1333, 1624-1689, 1697-1734, 1803-1810; FlyThrough.js 698-702,
 * 774-776 are constants of the FlyThrough object and are pinned in the test
 * from the source, because this recorder loads only Util, Fly, Render, Sky).
 * Each case is { args: [...], out: value } so the Python test can replay it.
 * A case with fewer args than the JS default leaves the argument out, so the
 * Python default is what is exercised.
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly;
   const cases = ( list, fn ) => list.map( a => ( { args: a, out: fn( ...a ) } ) );

   const w = [ { name: "RA", value: "315.41" }, { name: "DEC", value: "68.08" } ];

   const sig = {
      o: { travel: 184, easing: "smoothstep", growth: 0.15, bloom: 1, twinkle: 0.03, duration: 20, fps: 30, format: "h264", quality: "high",
           music: { path: "/m/a.mp3", fade: true }, video: true, ffmpeg: "/usr/bin/ffmpeg", dir: "/out", presets: [ "youtube_1080" ] },
      spec: { id: "youtube_1080", w: 1920, h: 1080, pingPong: false },
      key: "image.tif|StarXTerminator|922"
   };
   const withO = change => Object.assign( {}, sig.o, change );
   const sigCase = ( opts, spec, key ) => ( { args: [ opts, spec, key ], out: Fly.frameSignature( opts, spec, key ) } );

   return {
      // ---- constants --------------------------------------------------
      constants: {
         STAR_QUALITY: Fly.STAR_QUALITY,
         DRAFT_BYTES: Fly.DRAFT_BYTES,
         DRAFT_LONG: Fly.DRAFT_LONG,
         PRESETS: Fly.PRESETS,
         RENDERER_VERSION: Fly.RENDERER_VERSION,
         ENCODE_ONLY: Fly.ENCODE_ONLY,
         CROSSFADE_SECONDS: Fly.CROSSFADE_SECONDS,
         LINEAR_SKY: Fly.LINEAR_SKY,
         WORKING_WIDTH: Fly.WORKING_WIDTH,
         WORKING_HEIGHT: Fly.WORKING_HEIGHT
      },

      // ---- star quality (Fly 71-95) -----------------------------------
      starQuality: [ [ "highest" ], [ "high" ], [ "medium" ], [ "ultra" ], [ "constructor" ], [] ]
         .map( a => ( { args: a, out: Fly.starQuality( ...a ) } ) ),
      qualityScale: cases( [ [ "highest", 2160, 1080 ], [ "high", 2160, 1080 ], [ "medium", 2160, 1080 ],
                             [ "medium", 900, 1080 ], [ "high", 600, 1080 ], [ "low", 2160, 1080 ] ], Fly.qualityScale ),

      // ---- frames and timing (Fly 465-506) ----------------------------
      frameCount: cases( [ [ 10, 30, false ], [ 10, 30, true ], [ 20, 30, true ], [ 2.5, 24, false ], [ 0.04, 25, false ],
                           [ 1, 1, false ] ], Fly.frameCount ),
      timeAt: cases( [ [ 0, 5, false ], [ 4, 5, false ], [ 300, 600, true ], [ 599, 600, true ], [ 2, 4, true ],
                       [ 3, 4, true ], [ 0, 1, false ], [ 1, 1, true ], [ 0, 1, true ] ], Fly.timeAt ),
      draftPlan: cases( [ [ 120, 60, 480, 16 / 9 ], [ 10, 60, 480, 16 / 9 ], [ 20, 30, 480, 16 / 9 ], [ 2, 30, 480, 16 / 9 ],
                          [ 60, 30, 480, 0.5 ], [ 10, 24, 480, 1 ], [ 3600, 60, 480, 16 / 9 ], [ 1800, 60, 480, 1.25 ] ], Fly.draftPlan ),
      presetCrop: cases( [ [ 6000, 4000, 5900, 2000, 1080, 1920 ], [ 6000, 4000, 3000, 2000, 3840, 2160 ],
                           [ 100, 100, 0, 0, 1080, 1920 ], [ 4000, 3000, 3999, 2999, 1080, 1080 ], [ 7669, 11957, 3800, 5900, 3840, 2160 ] ], Fly.presetCrop ),

      // ---- presets and their loops (Fly 1232-1262) --------------------
      presetTurns: cases( [ [ "youtube_1080", "vertical", [ 1920, 1080 ] ], [ "social_vertical", "vertical", [ 1080, 1920 ] ],
                            [ "youtube_1080", "horizontal", [ 1920, 1080 ] ], [ "exhibition", "vertical", [ 3840, 2160 ] ],
                            [ "social_square", "vertical", [ 1080, 1080 ] ] ], Fly.presetTurns ),
      presetLoops: cases( [ [ "exhibition", "none" ], [ "exhibition" ], [ "youtube_1080", "none" ], [ "youtube_1080", "pingpong" ],
                            [ "youtube_1080", "crossfade" ], [ "youtube_4k" ] ], Fly.presetLoops ),
      presetSpec: [ [ "exhibition" ], [ "social_vertical" ], [ "youtube_1080", "vertical", "crossfade" ],
                    [ "youtube_1080", "vertical", "pingpong" ], [ "youtube_1080", "horizontal" ], [ "youtube_4k" ],
                    [ "exhibition", "vertical", "none" ], [ "exhibition", "horizontal", "crossfade" ],
                    [ "social_square", "horizontal", "pingpong" ], [ "youtube_1080", "vertical", "none" ],
                    [ "youtube_1080", "vertical" ] ].map( a => ( { args: a, out: Fly.presetSpec( ...a ) } ) )
         .concat( [ { args: [ { id: "x", w: 10, h: 20, pingPong: false } ], out: { id: "x", w: 10, h: 20, pingPong: false } } ] ),
      frameStep: cases( [ [ 11, false ], [ 10, true ], [ 100, false ], [ 1, false ], [ 0, true ] ], Fly.frameStep ),
      frameSignature: [ sigCase( sig.o, sig.spec, sig.key ),
                        sigCase( withO( { format: "hevc", quality: "standard", music: { path: "/m/b.mp3", fade: false }, video: false } ), sig.spec, sig.key ),
                        sigCase( withO( { objectName: "NGC7023 Iris Nebula" } ), sig.spec, sig.key ),
                        sigCase( withO( { travel: 200 } ), sig.spec, sig.key ),
                        sigCase( withO( { duration: 10, fps: 24, bloom: 1.5 } ), sig.spec, sig.key ),
                        sigCase( sig.o, sig.spec, "other.tif|StarNet2|922" ),
                        sigCase( sig.o, { id: "youtube_4k", w: 3840, h: 2160, pingPong: false }, sig.key ),
                        sigCase( withO( { starQuality: "medium" } ), sig.spec, "k" ),
                        sigCase( sig.o, sig.spec, "k" ),
                        sigCase( { zeta: 1.5e-7, alpha: [ 1, 2 ], Mid: null, beta: 1e21, gamma: -0 }, { w: 30.0, h: 1e-7, pingPong: 1, crossfade: "" }, "a\"b\\c" ) ],

      // ---- crossfade loops (Fly 1270-1333) ----------------------------
      crossfadeFrames: cases( [ [ 20, 30 ], [ 2, 30 ], [ 0.5, 24 ], [ 10, 60 ] ], Fly.crossfadeFrames ),
      loopFrame: cases( [ [ 0, 100, 20 ], [ 99, 100, 20 ], [ 80, 100, 20 ], [ 79, 100, 20 ], [ 0, 1, 0 ], [ 0, 1, 1 ] ], Fly.loopFrame ),

      // ---- files (Fly 1322-1333) ---------------------------------------
      framePath: cases( [ [ "/f", 7 ], [ "/f", 12345 ], [ "/f", 123456 ], [ "dir", 0 ] ], Fly.framePath ),
      isFrameFile: [ "frame_00000.tif", "frame_12345.tif", "notes.txt", "frame_1.tif", "frame_00001.tif.bak",
                     "frame_123456.tif", "frame_0000a.tif", "Frame_00001.tif", "frame_00001.tif\n" ]
         .map( a => ( { args: [ a ], out: Fly.isFrameFile( a ) } ) ),

      // ---- header centre (Fly 1116-1128) ------------------------------
      headerCentre: [
         [ w ],
         [ [ { name: "OBJCTRA", value: "'21 01 38.0'" }, { name: "OBJCTDEC", value: "'+68 04 48'" } ] ],
         [ [ { name: "CTYPE1", value: "'RA---TAN'" }, { name: "CRVAL1", value: "10.5" }, { name: "CRVAL2", value: "-5" } ] ],
         [ [ { name: "CRVAL1", value: "10.5" }, { name: "CRVAL2", value: "-5" } ] ],
         [ [ { name: "EXPTIME", value: "300" } ] ],
         [ [ { name: "RA", value: "400" }, { name: "DEC", value: "10" } ] ],
         [ [ { name: " ra ", value: "0" }, { name: "dec", value: "-90" } ] ],
         [ [ { name: "RA", value: "10" }, { name: "DEC", value: "-91" } ] ],
         [ [ { name: "CTYPE1", value: "'RA---TAN'" }, { name: "CRVAL1", value: "abc" }, { name: "RA", value: "1.25" }, { name: "DEC", value: "2.5" } ] ],
         [ [ { name: "OBJCTRA", value: "nonsense" }, { name: "OBJCTDEC", value: "'+1 00 00'" } ] ],
         [ [] ]
      ].map( a => ( { args: [ a[0] ], out: Fly.headerCentre( a[0] ) } ) ),

      // ---- playing a sequence (Fly 1624-1640) -----------------------
      sequenceLength: cases( [ [ 4, true ], [ 4, false ], [ 1, true ], [ 1, false ] ], Fly.sequenceLength ),
      sequenceFrame: cases( [ [ 0, 4, false ], [ 4, 4, false ], [ 5, 4, true ], [ 6, 4, true ], [ 7, 4, true ], [ 8, 4, true ],
                              [ 1, 1, true ], [ 0, 4, true ], [ 12, 4, true ] ], Fly.sequenceFrame ),

      // ---- angles typed by a person (Fly 1644-1660) -------------------
      parseAngle: cases( [ [ "21 36 42", true ], [ "21:36:42", true ], [ "-57 30 00", false ], [ "324.18", true ],
                           [ "north", false ], [ "+10.5", false ], [ "  21 36  ", true ], [ "12 30", false ], [ "-0", true ],
                           [ "1 2 3.5", false ], [ "", true ] ], Fly.parseAngle ),

      // ---- progress (Fly 1665-1689) ------------------------------------
      formatElapsed: cases( [ [ 75000 ], [ 42000 ], [ 0 ], [ 59600 ], [ 60000 ], [ 1000 ], [ 125400 ] ], Fly.formatElapsed ),
      progressText: cases( [ [ "Rendering YouTube 1920×1080", 150, 600, 90000 ], [ "x", 590, 600, 60000 ],
                             [ "Removing stars (StarXTerminator)", 0, 0, 75000 ], [ "Rendering", 6, 10, 30000, 5 ],
                             [ "Rendering", 6, 10, 30000 ], [ "Solving", 0, 0, 0 ], [ "Solving", 0, 0, 400 ],
                             [ "Solving", 0, 0, 1000 ], [ "Solving", 0, 0, 65000 ], [ "x", 0, 10, 0 ], [ "x", 10, 10, 9000 ],
                             [ "x", 0, 10, 9000 ] ], Fly.progressText ),
      progressFraction: cases( [ [ 150, 600 ], [ 0, 0 ], [ 700, 600 ], [ -1, 10 ] ], Fly.progressFraction ),

      // ---- the working image (Fly 1697-1734) --------------------------
      workingScale: cases( [ [ 6000, 4000 ], [ 12000, 4000 ], [ 3000, 2000 ], [ 7669, 11957 ], [ 3840, 2160 ], [ 1, 1 ],
                             [ 4000, 6000 ] ], Fly.workingScale ),
      needsRotation: cases( [ [ 3000, 5000, 3840, 2160 ], [ 3000, 5000, 1080, 1920 ], [ 5000, 3000, 1080, 1920 ],
                              [ 3000, 5000, 1080, 1080 ], [ 3000, 3000, 1920, 1080 ], [ 4000, 3000, 3840, 2160 ] ], Fly.needsRotation ),

      // ---- sky (Fly 1803-1810) ----------------------------------------
      looksLinear: cases( [ [ 0.004 ], [ 0.08 ], [ 0.02 ], [ 0 ] ], Fly.looksLinear )
   };
};
