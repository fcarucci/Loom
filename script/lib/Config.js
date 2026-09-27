/*
 * Loom's configuration: its defaults, and loading and saving it.
 *
 * Moved out of Loom.js so node can run it. Every read and write goes
 * through a store -- { settings: { read, write }, parameters: { has,
 * getString, getReal, getBoolean, set } } -- which is PixInsight's
 * Settings and Parameters in production (Config.pixinsightStore) and a
 * recording fake in the suite.
 *
 * Precedence, which the keys and their order depend on: Parameters (a
 * process icon) are read first; for the keys both hold -- smoothing,
 * gradientTool/useGraXpert, graxpertNarrowband, useCache, autoUpdate -- a
 * stored Settings value then overrides; savedList comes from Settings only
 * when Parameters left it empty.
 *
 * Needs Util, Cache and Steps loaded first.
 */

var Config = {};

/*
 * The Settings key prefix. Deliberately a property, not a #define: a
 * define is shared by every file the preprocessor sees after it, and one
 * has already rewritten a property access in another file.
 */
Config.SETTINGS_PREFIX = "Loom/";

/*
 * PixInsight's own store. The globals are looked up on each call, not
 * captured, so what is read and written is always the live Settings and
 * Parameters.
 */
Config.pixinsightStore = function()
{
   return {
      settings: {
         read:  function( key, type ) { return Settings.read( key, type ); },
         write: function( key, type, value ) { Settings.write( key, type, value ); }
      },
      parameters: {
         has:        function( name ) { return Parameters.has( name ); },
         getString:  function( name ) { return Parameters.getString( name ); },
         getReal:    function( name ) { return Parameters.getReal( name ); },
         getBoolean: function( name ) { return Parameters.getBoolean( name ); },
         set:        function( name, value ) { Parameters.set( name, value ); }
      }
   };
};

/*
 * The plain settings: one "Loom/<name>" key and one config field each.
 *
 *   type  the DataType read and written.
 *   load  "set": a stored value replaces the default unless it is null.
 *         "text": the same, but an empty string is ignored too.
 *         null: never read by Config.loadFields, which refuses it.
 *   save  "bool": written as !!value. "as is": written unchanged.
 *         { or: d }: written as value || d.
 *
 * Only keys with no other rule are here. What is read or written
 * differently -- paths and filters, palettes, the gradient tool and its
 * legacy useGraXpert twin, a bandwidth that must be positive, the
 * savedList fallback -- stays as code in Config.load, its helpers and
 * Config.save, and ignoreCache is never persisted at all. The ORDER of reads and
 * writes is the order of the lists passed to loadFields and saveFields
 * there; the fixture in ci/fixtures/config.json pins it.
 */
Config.FIELDS = {
   graxpertNarrowband:  { type: DataType_Boolean, load: "set",  save: "bool" },
   narrowbandBandwidth: { type: DataType_Double,  load: null,   save: { or: 3.0 } },
   narrowbandNormalize: { type: DataType_Boolean, load: "set",  save: "bool" },
   reduceHalos:         { type: DataType_Boolean, load: "set",  save: "bool" },
   keepLinear:          { type: DataType_Boolean, load: "set",  save: "bool" },
   separateLStars:      { type: DataType_Boolean, load: "set",  save: "bool" },
   exportPsb:           { type: DataType_Boolean, load: "set",  save: "bool" },
   projectName:         { type: DataType_String,  load: "set",  save: { or: "" } },
   exportDir:           { type: DataType_String,  load: "set",  save: { or: "" } },
   stretch:             { type: DataType_Boolean, load: "set",  save: "bool" },
   stretchMethod:       { type: DataType_String,  load: "text", save: { or: Steps.STRETCH_METHOD_MTF } },
   marsPath:            { type: DataType_String,  load: "set",  save: { or: "" } },
   starTool:            { type: DataType_String,  load: "text", save: { or: "none" } },
   noiseTool:           { type: DataType_String,  load: "text", save: { or: "none" } },
   noiseLevel:          { type: DataType_String,  load: "text", save: { or: "medium" } },
   // Empty is kept: it is a choice, "follow the colour level", not a gap.
   noiseLevelL:         { type: DataType_String,  load: "set",  save: { or: "" } },
   sharpenTool:         { type: DataType_String,  load: "text", save: { or: "none" } },
   starReduction:       { type: DataType_String,  load: "text", save: { or: "none" } },
   detailLevel:         { type: DataType_String,  load: "text", save: { or: "none" } },
   useCache:            { type: DataType_Boolean, load: "set",  save: "as is" },
   autoUpdate:          { type: DataType_Boolean, load: "set",  save: "as is" },
   cacheDir:            { type: DataType_String,  load: "set",  save: { or: "" } },
   smoothing:           { type: DataType_Double,  load: "set",  save: "as is" },
   savedList:           { type: DataType_String,  load: null,   save: { or: "" } }
};

/* Reads the named plain settings, in the order given, into config. */
Config.loadFields = function( config, store, names )
{
   for ( var i = 0; i < names.length; ++i )
   {
      var f = Config.FIELDS[names[i]];
      if ( f.load != "set" && f.load != "text" )
         throw new Error( "Config: " + names[i] + " is not loaded as a plain setting" );
      var v = store.settings.read( Config.SETTINGS_PREFIX + names[i], f.type );
      if ( v != null && ( f.load != "text" || v.length > 0 ) )
         config[names[i]] = v;
   }
};

/* Writes the named plain settings from config, in the order given. */
Config.saveFields = function( config, store, names )
{
   for ( var i = 0; i < names.length; ++i )
   {
      var f = Config.FIELDS[names[i]], v = config[names[i]];
      if ( f.save == "bool" )
         v = !!v;
      else if ( f.save != "as is" )
         v = v || f.save.or;
      store.settings.write( Config.SETTINGS_PREFIX + names[i], f.type, v );
   }
};

Config.defaults = function()
{
   return {
      paths: { L: "", R: "", G: "", B: "", H: "", S: "", O: "" },
      views: {},
      savedList: "",
      filters: {},
      // None, GraXpert or SyQon Studio's Deep Gradient. Replaces the old
      // useGraXpert checkbox, which Steps.migrateConfig still reads.
      gradientTool: Steps.GRADIENT_TOOL_GRAXPERT,
      // Default OFF: narrowband channels have never been through GraXpert,
      // and an upgrade must not silently change what a repeat run
      // produces -- nor re-key a cached H/S/O result nobody asked to redo.
      graxpertNarrowband: false,
      smoothing: 0.5,
      validateOnly: false,
      keepWindowsOnError: false,
      palettes: [],
      // nm. Filter-dependent, so it must be settable: SPCC's own default
      // is 3.0, Baader narrowband is commonly 3.5 or 6.5.
      narrowbandBandwidth: 3.0,
      // Default ON: this ran unconditionally before there was a switch, and
      // an upgrade must not silently change what a repeat run produces.
      narrowbandNormalize: true,
      reduceHalos: false,
      sharpenTool: "none",
      stretch: false,
      // Loom's own deterministic MTF stretch; MultiscaleAdaptiveStretch
      // is the alternative -- see Steps.STRETCH_METHOD_MAS.
      stretchMethod: Steps.STRETCH_METHOD_MTF,
      keepLinear: false,
      // Frequency-separate the L stars plate into _low/_high on export.
      separateLStars: false,
      // One layered .psb beside the TIFFs -- see Steps.buildPsbDocument.
      exportPsb: false,
      // Names the PSB. Defaulted in the dialog from the masters' folder,
      // since PJSR exposes nothing about the open PixInsight project.
      projectName: "",
      exportDir: "",
      marsPath: "",
      starTool: "none",
      noiseTool: "none",
      noiseLevel: "medium",
      // Empty means "follow the colour level", so a configuration saved
      // before L had its own strength behaves exactly as it did.
      noiseLevelL: "",
      starReduction: "none",
      detailLevel: "none",
      // Keep the installed copy current. See lib/Update.js: the update is
      // spawned detached and takes effect on the NEXT launch, so this can
      // never delay or block startup.
      autoUpdate: true,
      useCache: true,
      // Empty means the system temp dir -- see Cache.dir().
      cacheDir: "",
      // Not persisted -- "for this run" is exactly what it means; it must
      // not silently stay on across sessions.
      ignoreCache: false
   };
};

/* Reads one Parameter into config, with the getter its type needs, when the icon has it. */
Config.loadParameter = function( config, parameters, name, get )
{
   if ( parameters.has( name ) )
      config[name] = get.call( parameters, name );
};

/*
 * What a saved process instance carries: the channel paths and the few
 * settings that travel with the icon.
 */
Config.loadParameters = function( config, store )
{
   var p = store.parameters;
   for ( var i = 0; i < Util.CHANNELS.length; ++i )
   {
      var key = Util.CHANNELS[i];
      if ( p.has( "path_" + key ) )
         config.paths[key] = p.getString( "path_" + key );
      // Views are not saveable process-instance state -- an open view id
      // from a previous session/run has no guaranteed meaning now, so
      // only file paths round-trip through Parameters.
   }
   Config.loadParameter( config, p, "savedList", p.getString );
   Config.loadParameter( config, p, "smoothing", p.getReal );
   /*
    * A process icon saved before the gradient dropdown carries only
    * useGraXpert. Loaded into the legacy field and cleared from the new
    * one, so Steps.migrateConfig maps it: true is GraXpert, false is
    * none.
    */
   if ( p.has( "gradientTool" ) )
      config.gradientTool = p.getString( "gradientTool" );
   else if ( p.has( "useGraXpert" ) )
   {
      config.useGraXpert = p.getBoolean( "useGraXpert" );
      config.gradientTool = "";
   }
   Config.loadParameter( config, p, "graxpertNarrowband", p.getBoolean );
   Config.loadParameter( config, p, "useCache", p.getBoolean );
   Config.loadParameter( config, p, "autoUpdate", p.getBoolean );
};

/*
 * Remembered filter choices. A FITS FILTER of L/R/G/B names the channel,
 * not the physical filter, so these are the only record of which filter
 * each channel was actually shot through.
 */
Config.loadFilters = function( config, store )
{
   config.filters = {};
   var fkeys = Util.BROADBAND;
   for ( var fi = 0; fi < fkeys.length; ++fi )
   {
      var fv = store.settings.read( Config.SETTINGS_PREFIX + "filter_" + fkeys[fi], DataType_String );
      if ( fv != null && fv.length > 0 )
         config.filters[fkeys[fi]] = fv;
   }
};

/* The gradient tool from Settings, or from the checkbox it replaced. */
Config.loadGradientSetting = function( config, store )
{
   var K = Config.SETTINGS_PREFIX;
   var gt = store.settings.read( K + "gradientTool", DataType_String );
   if ( gt != null && gt.length > 0 )
   {
      config.gradientTool = gt;
      return;
   }
   // settings from before the dropdown: the checkbox's value decides
   var gx = store.settings.read( K + "useGraXpert", DataType_Boolean );
   if ( gx != null )
   {
      config.useGraXpert = gx;
      config.gradientTool = "";
   }
};

/*
 * Restores from a saved process instance when launched from one, and
 * from Settings otherwise. This is what makes the script draggable to
 * the workspace as a reusable icon.
 */
Config.load = function( store )
{
   var K = Config.SETTINGS_PREFIX;
   var config = Config.defaults();

   Config.loadParameters( config, store );

   if ( config.savedList.length == 0 )
   {
      var savedList = store.settings.read( K + "savedList", DataType_String );
      if ( savedList != null )
         config.savedList = savedList;
   }

   Config.loadFilters( config, store );

   var pal = store.settings.read( K + "palettes", DataType_String );
   if ( pal != null && pal.length > 0 )
      config.palettes = pal.split( "," ).filter( function( x ) { return x.length > 0; } );

   Config.loadFields( config, store, [ "narrowbandNormalize", "graxpertNarrowband" ] );

   // only a positive width: 0 was never a bandwidth, and neither is less
   var nbw = store.settings.read( K + "narrowbandBandwidth", DataType_Double );
   if ( nbw != null && nbw > 0 )
      config.narrowbandBandwidth = nbw;

   Config.loadFields( config, store, [
      "reduceHalos", "projectName", "exportPsb", "separateLStars", "keepLinear", "exportDir",
      "stretchMethod", "stretch", "marsPath", "starTool", "noiseTool", "noiseLevel", "noiseLevelL",
      "sharpenTool", "starReduction", "detailLevel" ] );

   Config.loadGradientSetting( config, store );

   Config.loadFields( config, store, [ "autoUpdate", "useCache", "smoothing", "cacheDir" ] );

   /*
    * Point the cache at the restored folder before anything reads it --
    * the dialog shows the cache's size and entry count as soon as it is
    * constructed, and that readout has to describe the folder in use.
    */
   Cache.setDir( config.cacheDir );

   /*
    * The gradient dropdown from useGraXpert, SyQon Studio Prism
    * Essential's old name, Studio's one Prism entry (2.0, or Essential
    * while 2.0 is refused), and the retired "Studio Parallax (correct
    * only)", so a run saved with any of them opens with what is offered.
    */
   var studioFound = Steps.studioAvailable();
   Steps.migrateConfig( config, studioFound ? !Steps.studioPrism2Unavailable() : undefined );

   /*
    * A chosen cache folder that is not there disables the cache for this
    * launch, rather than being created.
    *
    * The folder lives on an external volume, and an unmounted volume is
    * indistinguishable from a missing folder. Creating it would put a
    * decoy cache on the boot disk, fill it with tens of gigabytes, and
    * leave the real one -- with all its entries -- ignored the next time
    * the drive appeared. Running uncached is slower; that is the cheaper
    * mistake by a wide margin.
    *
    * Not persisted: the setting still names the folder, so plugging the
    * drive back in and relaunching restores the cache with no clicking.
    */
   Cache.disableIfDirMissing( config );

   return config;
};

Config.save = function( config, store )
{
   var K = Config.SETTINGS_PREFIX;
   for ( var i = 0; i < Util.CHANNELS.length; ++i )
   {
      var key = Util.CHANNELS[i];
      store.parameters.set( "path_" + key, config.paths[key] );
   }
   store.parameters.set( "savedList", config.savedList || "" );
   store.parameters.set( "smoothing", config.smoothing );
   store.parameters.set( "gradientTool", Steps.gradientToolOf( config ) );
   // still written, so an older Loom opening this icon reads what it can
   store.parameters.set( "useGraXpert",
                         Steps.gradientToolOf( config ) == Steps.GRADIENT_TOOL_GRAXPERT );
   store.parameters.set( "graxpertNarrowband", !!config.graxpertNarrowband );
   store.parameters.set( "useCache", config.useCache );
   store.parameters.set( "autoUpdate", config.autoUpdate );

   store.settings.write( K + "gradientTool", DataType_String, Steps.gradientToolOf( config ) );
   store.settings.write( K + "useGraXpert", DataType_Boolean,
                         Steps.gradientToolOf( config ) == Steps.GRADIENT_TOOL_GRAXPERT );
   Config.saveFields( config, store,
                      [ "graxpertNarrowband", "narrowbandBandwidth", "narrowbandNormalize", "reduceHalos" ] );
   store.settings.write( K + "palettes", DataType_String,
                         ( config.palettes || [] ).join( "," ) );
   Config.saveFields( config, store, [
      "keepLinear", "separateLStars", "exportPsb", "projectName", "exportDir", "stretch",
      "stretchMethod", "marsPath", "starTool", "noiseTool", "noiseLevel", "noiseLevelL",
      "sharpenTool", "starReduction", "detailLevel", "useCache", "autoUpdate", "cacheDir",
      "smoothing", "savedList" ] );
   var fk = Util.BROADBAND;
   for ( var i = 0; i < fk.length; ++i )
      store.settings.write( K + "filter_" + fk[i], DataType_String,
                            ( config.filters && config.filters[fk[i]] ) ? config.filters[fk[i]] : "" );
};
