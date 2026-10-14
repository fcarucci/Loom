/*
 * Oracle for loomsiril/encode.py (E1b): the video formats, codec arguments,
 * the ffmpeg command line and its quoting, the ffmpeg candidates and install
 * hints, the ffmpeg progress count (Fly.js 929-1071, 1187-1231, 1690-1696;
 * the HDR peak default at Fly.js 1457). Each case is { args, out }: out of
 * ffmpegArgs is the argument list joined with single spaces, as the
 * selftest pins it. Process running (Render.js 1364-1450) is not recorded:
 * the Python version replaces it with subprocess and is tested against a fake
 * ffmpeg instead.
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly;
   const join = a => a.join( " " );

   const enc = " V....D libx264  H.264\n V....D libx265  H.265\n V....D prores_ks ProRes\n";
   const encHdr = " V....D libx264 \n V....D libx265 \n V....D prores_ks \n V....D libvpx-vp9 \n";
   const formats = Fly.availableFormats( enc );
   const hdrPq = { transfer: "pq", peak: 1000, maxCll: 850, maxFall: 120 }, hdrHlg = { transfer: "hlg" };
   const audio = ( path, fade, duration, loop ) => ( { path: path, fade: fade, duration: duration, loop: loop } );

   const ffArgs = ( a ) => ( { args: a, out: join( Fly.ffmpegArgs( ...a ) ) } );

   return {
      // ---- constants --------------------------------------------------
      constants: {
         VIDEO_FORMATS: Fly.VIDEO_FORMATS,
         REC709_FILTER: Fly.REC709_FILTER,
         HDR_TRC: Fly.HDR_TRC,
         HDR_PEAK_DEFAULT: Fly.HDR_PEAK_DEFAULT,
         VIDEO_ENCODING_TAGS: Fly.VIDEO_ENCODING_TAGS,
         AUDIO_FADE: Fly.AUDIO_FADE,
         AUDIO_LOOP_FADE: Fly.AUDIO_LOOP_FADE
      },

      // ---- formats (Fly 929-958) ---------------------------------------
      availableFormats: [ [ enc ], [ " libx264 " ], [ encHdr, true ], [ enc, false ], [ "" ] ]
         .map( a => ( { args: a, out: Fly.availableFormats( ...a ) } ) ),
      defaultFormat: [ [ formats, 3840 ], [ formats, 1920 ], [ Fly.availableFormats( " libx264 " ), 3840 ],
                       [ Fly.availableFormats( encHdr, true ), 1920 ], [ [], 1920 ], [ formats, 1921 ] ]
         .map( a => ( { args: a, out: Fly.defaultFormat( ...a ) } ) ),
      isHdr: [ [ "pq" ], [ "hlg" ], [ "sdr" ] ].map( a => ( { args: a, out: Fly.isHdr( a[0] ) } ) ),
      hdrFilter: [ [ "pq" ], [ "hlg" ] ].map( a => ( { args: a, out: Fly.hdrFilter( a[0] ) } ) ),
      x265Hdr10: [ [ hdrPq ], [ { transfer: "pq" } ], [ { transfer: "pq", peak: 4000, maxCll: 0.4, maxFall: 99.5 } ],
                   [ { transfer: "pq", peak: 0, maxCll: 1200, maxFall: 0 } ] ]
         .map( a => ( { args: a, out: Fly.x265Hdr10( a[0] ) } ) ),

      // ---- codecs (Fly 980-992) ----------------------------------------
      codecArgs: [ [ "h264", true ], [ "h264", false ], [ "hevc", false ], [ "hevc", true, hdrPq ], [ "hevc", false, hdrHlg ],
                   [ "prores", true ], [ "prores", false, hdrPq ], [ "vp9", false ], [ "vp9", true, hdrHlg ] ]
         .map( a => ( { args: a, out: Fly.codecArgs( ...a ) } ) ),

      // ---- the ffmpeg command (Fly 993-1017) ---------------------------
      ffmpegArgs: [
         [ "/f", 25, "/o/youtube", "h264", "high" ],
         [ "/f", 30, "/o/x", "hevc", "standard" ],
         [ "/f", 30, "/o/x", "prores", "high" ],
         [ "/f", 30, "/o/x", "vp9", "standard" ],
         [ "/f", 30, "/o/x", "h264", "standard" ],
         [ "/f", 30, "/o/x", "hevc", "standard", hdrPq ],
         [ "/f", 30, "/o/x", "hevc", "standard", hdrHlg ],
         [ "/f", 30, "/o/x", "vp9", "high", hdrHlg ],
         [ "/f", 30, "/o/x", "prores", "high", hdrPq ],
         [ "/f", 24.5, "/o/y", "h264", "standard", null ],
         [ "/f", 30, "/o/x", "h264", "high", null, audio( "/m/a.mp3", true, 20, false ) ],
         [ "/f", 30, "/o/x", "vp9", "standard", null, audio( "/m/a.mp3", false, 20, false ) ],
         [ "/f", 30, "/o/x", "prores", "high", null, audio( "/m/a.mp3", true, 2.5, false ) ],
         [ "/f", 30, "/o/x", "hevc", "high", hdrPq, audio( "/m/a.mp3", true, 10, true ) ],
         [ "/f", 30, "/o/x", "h264", "high", null, audio( "", true, 20, false ) ]
      ].map( ffArgs ),

      // ---- file names (Fly 1018-1027) ----------------------------------
      videoName: [ [ "NGC7023 Iris Nebula", "youtube_1080_vertical", "pq" ], [ "IC1396A", "social_square", "hlg" ],
                   [ "M31", "youtube_4k", "sdr" ], [ "", "youtube_1080", "sdr" ], [ null, "youtube_1080", "pq" ],
                   [ "Elephant's Trunk: a/b", "youtube_1080", "sdr" ], [ "O\u2019Neill  --  M 31", "p", "hlg" ],
                   [ "  ", "x", "sdr" ], [ "abc", "p", "weird" ], [ "A", "p", null ] ]
         .map( a => ( { args: a, out: Fly.videoName( ...a ) } ) ),

      // ---- audio (Fly 1028-1071) ----------------------------------------
      audioFade: [ [ 20 ], [ 4 ], [ 2.5 ], [ 0.5 ] ].map( a => ( { args: a, out: Fly.audioFade( a[0] ) } ) ),
      audioArgs: [ [ audio( "/m/a.mp3", true, 20, false ), "h264" ], [ audio( "/m/a.mp3", false, 20, false ), "vp9" ],
                   [ audio( "/m/a.mp3", true, 10, true ), "hevc" ], [ audio( "/m/a.mp3", true, 2.5, false ), "prores" ],
                   [ audio( "/m/a.mp3", true, 0.5, false ), "h264" ], [ audio( "/m/a.mp3", true, 10, true ), "vp9" ] ]
         .map( a => ( { args: a, out: Fly.audioArgs( ...a ) } ) ),
      loopAudio: [ [ 10, 1 ], [ 2.5, 0.625 ], [ 20, 1 ] ].map( a => ( { args: a, out: Fly.loopAudio( ...a ) } ) ),

      // ---- ffmpeg discovery (Fly 1187-1200) ----------------------------
      ffmpegCandidates: [
         [ "macos", "/Users/u", "/usr/bin:/opt/x/bin:", "" ],
         [ "macos", "/Users/u", "", "/s/ffmpeg" ],
         [ "macos", "/Users/u", "/usr/local/bin:/usr/bin:/usr/local/bin/", "/usr/bin/ffmpeg" ],
         [ "windows", "C:/Users/u", "C:\\Windows\\system32;\"C:\\Tools\\ffmpeg\\bin\";;C:\\Tools\\ffmpeg\\bin\\", "" ],
         [ "windows", "", "", "" ],
         [ "linux", "/home/u", " /usr/x/bin/ :  /bin ", "" ],
         [ "linux", "", "", "/s/ffmpeg" ]
      ].map( a => ( { args: a, out: Fly.ffmpegCandidates( ...a ) } ) ),
      ffmpegInstallHint: [ [ "windows" ], [ "macos" ], [ "linux" ] ].map( a => ( { args: a, out: Fly.ffmpegInstallHint( a[0] ) } ) ),

      // ---- the shown command line (Fly 1201-1215) ----------------------
      commandLine: [
         [ "C:/Program Files/ffmpeg/bin/ffmpeg.exe", [ "-i", "D:/My Frames/frame_%05d.tif" ], "windows" ],
         [ "/opt/homebrew/bin/ffmpeg", [ "-y", "/Volumes/My Disk/x.mp4" ], "macos" ],
         [ "ffmpeg", [ "it's", "a \"b\"", "x y", "" ], "macos" ],
         [ "ffmpeg", [ "it's", "a \"b\"", "x y", "" ], "windows" ],
         [ "ffmpeg", [ "-i", "a%b=c+d,e:f/g.mp4" ], "linux" ],
         [ "C:/x/ffmpeg.exe", [ 5, "frame_%05d.tif" ], "windows" ],
         [ "/bin/ff", [ "trail'quote'" ], "linux" ]
      ].map( a => ( { args: a, out: Fly.commandLine( ...a ) } ) ),

      // ---- progress (Fly 1690-1696) -----------------------------------
      ffmpegFrameProgress: [ [ "frame=   12 fps=3.1 q=28.0 size=  256kB\rframe=   37 fps=3.2 q=28.0" ], [ "Input #0, image2" ],
                             [ "frame=5" ], [ "frame=\t9frame=10" ], [ "frame=abc" ], [ "" ] ]
         .map( a => ( { args: a, out: Fly.ffmpegFrameProgress( a[0] ) } ) )
   };
};
