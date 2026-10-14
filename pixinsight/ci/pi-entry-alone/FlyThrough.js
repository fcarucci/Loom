#engine v8

/*
 * PixInsight only: FlyThrough.js loaded alone. See check.js.
 */
#define LOOM_FLY_UNDER_TEST 1
#include "../../script/FlyThrough.js"
#include "check.js"

entryAloneCheck( "FlyThrough.js",
                 File.extractDirectory( #__FILE__ ) + "/../fixtures/steps-members.json",
                 [ "Util", "Cache", "Psb", "Steps", "Fly", "Solve", "Sky", "Render", "FlyThrough" ] );
