#engine v8

/*
 * PixInsight only: FrameSelector.js loaded alone. See check.js. It
 * includes no Steps file, so only its own namespaces are checked.
 */
#define LOOM_FRAME_SELECTOR_UNDER_TEST 1
#include "../../script/FrameSelector.js"
#include "check.js"

entryAloneCheck( "FrameSelector.js", null,
                 [ "Util", "Cache", "AsiairNames", "Asiair", "NightDialog", "Frames", "FrameSelector" ] );
