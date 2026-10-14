#engine v8

/*
 * PixInsight only: Loom.js loaded alone. See check.js.
 */
#define LOOM_UNDER_TEST 1
#include "../../script/Loom.js"
#include "check.js"

entryAloneCheck( "Loom.js",
                 File.extractDirectory( #__FILE__ ) + "/../fixtures/steps-members.json",
                 [ "Util", "Cache", "Psb", "Steps", "Config", "Pipeline", "Update", "UI" ] );
