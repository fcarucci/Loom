/*
 * Choosing a target and a night from an ASIAIR card.
 *
 * Every event handler body here is wrapped in try/catch, without
 * exception. A JS exception escaping into Qt unwinds through a destructor
 * into std::terminate and takes PixInsight with it -- this project has
 * lost the application to that twice, and both times it was a handler.
 *
 * Note which headers are NOT included: Sizer, HorizontalSizer and
 * VerticalSizer are core classes and need none. <pjsr/Sizer.jsh> exists
 * on disk but including it makes the whole file fail to PARSE under the
 * v8 engine, silently -- no error reaches the console, the script simply
 * never runs. That cost a bisect down to individual includes to find.
 */

#include <pjsr/FrameStyle.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>

function NightDialog() {}

/* Local clock time from a stamp key, for labelling a span. */
NightDialog.clockOf = function( stamp )
{
   return stamp.substr( 9, 2 ) + ":" + stamp.substr( 11, 2 );
};

/*
 * What one night reads as in the tree. Pure, so the wording is testable
 * without building a dialog.
 */
NightDialog.rowFor = function( night )
{
   var from = NightDialog.clockOf( night.frames[0].stamp );
   var to   = NightDialog.clockOf( night.frames[night.frames.length-1].stamp );
   return { date: night.date,
            span: from + "-" + to,
            count: String( night.count ),
            filters: night.filters.join( " " ) };
};

/*
 * How the flats for a night read. "missing" is a warning, not an absence:
 * a filter with no flats must stay visible, because that is the thing the
 * observer needs to see before running an import.
 */
NightDialog.flatSummary = function( matches )
{
   var out = [];
   for ( var i = 0; i < matches.length; ++i )
   {
      var m = matches[i];
      out.push( { filter: m.filter,
                  count: m.flats.length,
                  strength: m.strength,
                  text: m.strength == "missing"
                      ? m.filter + ": no flats"
                      : m.filter + ": " + m.flats.length + " flat" +
                        ( m.flats.length == 1 ? "" : "s" ) +
                        ( m.strength == "weak" ? " (partial match)" : "" ) } );
   }
   return out;
};

/*
 * Everything a card offers, as plain data: the targets, their nights, and
 * which flat batches belong to each session. Separated from the dialog so
 * the arrangement can be tested without constructing a widget.
 */
NightDialog.surveyOf = function( scan, gapHours )
{
   var sessions = AsiairNames.sessions( scan.lights, gapHours );
   var nights = AsiairNames.nights( sessions );
   var batches = AsiairNames.flatBatches( scan.flats, gapHours );
   var owners = AsiairNames.assignBatches( batches, sessions );

   var flatsBySession = {};
   for ( var b = 0; b < batches.length; ++b )
   {
      var owner = owners[b];
      if ( owner < 0 )
         continue;
      if ( !( owner in flatsBySession ) )
         flatsBySession[owner] = [];
      flatsBySession[owner] = flatsBySession[owner].concat( batches[b].frames );
   }

   var targets = [], seen = {};
   for ( var n = 0; n < nights.length; ++n )
      if ( !( nights[n].target in seen ) )
      {
         seen[nights[n].target] = true;
         targets.push( nights[n].target );
      }

   return { sessions: sessions, nights: nights, targets: targets,
            flatsBySession: flatsBySession, unparseable: scan.unparseable };
};

/* The flats offered for one night, before headers are consulted. */
NightDialog.flatsForNight = function( survey, night )
{
   return survey.flatsBySession[night.sessionIndex] || [];
};

/*
 * The picker itself.
 *
 * Built from a survey, which is plain data, so everything that decides
 * WHAT is shown is tested above and this only decides how it looks.
 */
NightDialog.Dialog = class extends Dialog
{
   constructor( survey, cardRoot )
   {
      super();
      var self = this;
      this.survey = survey;
      this.cardRoot = cardRoot;
      this.selectedNight = null;

      this.windowTitle = "Import from ASIAIR";
      this.scaledMinWidth = 640;

      this.where = new Label( this );
      this.where.text = "Card: " + cardRoot;
      this.where.wordWrapping = true;

      this.tree = new TreeBox( this );
      this.tree.alternateRowColor = true;
      this.tree.headerVisible = true;
      this.tree.numberOfColumns = 4;
      this.tree.setHeaderText( 0, "Target / night" );
      this.tree.setHeaderText( 1, "Frames" );
      this.tree.setHeaderText( 2, "Filters" );
      this.tree.setHeaderText( 3, "Flats" );
      this.tree.setScaledMinHeight( 260 );

      this.fill();

      /*
       * currentNode, never an assignment to selectedNodes: that property
       * is READ-ONLY, and assigning it threw from inside a handler --
       * which is to say it took the application with it.
       */
      this.tree.onCurrentNodeUpdated = function()
      {
         try { self.remember(); }
         catch ( e ) { /* a selection must never kill the dialog */ }
      };
      this.tree.onNodeDoubleClicked = function()
      {
         try { self.remember(); if ( self.selectedNight != null ) self.ok(); }
         catch ( e ) {}
      };

      this.detail = new Label( this );
      this.detail.frameStyle = FrameStyle_Sunken;
      this.detail.wordWrapping = true;
      this.detail.useRichText = true;
      this.detail.setScaledMinHeight( 56 );
      this.detail.text = "Select a night.";

      this.okButton = new PushButton( this );
      this.okButton.text = "Use this night";
      this.okButton.enabled = false;
      this.okButton.onClick = function()
      {
         try { if ( self.selectedNight != null ) self.ok(); }
         catch ( e ) {}
      };

      this.cancelButton = new PushButton( this );
      this.cancelButton.text = "Cancel";
      this.cancelButton.onClick = function()
      {
         try { self.cancel(); }
         catch ( e ) {}
      };

      var buttons = new HorizontalSizer;
      buttons.addStretch();
      buttons.add( this.okButton );
      buttons.addSpacing( 6 );
      buttons.add( this.cancelButton );

      this.sizer = new VerticalSizer;
      this.sizer.margin = 8;
      this.sizer.spacing = 6;
      this.sizer.add( this.where );
      this.sizer.add( this.tree, 100 );
      this.sizer.add( this.detail );
      this.sizer.add( buttons );

      this.adjustToContents();
   }

   /* One parent per target, one child per night. */
   fill()
   {
      for ( var t = 0; t < this.survey.targets.length; ++t )
      {
         var target = this.survey.targets[t];
         var parent = new TreeBoxNode( this.tree );
         parent.expanded = true;
         parent.setText( 0, target );

         var total = 0;
         for ( var n = 0; n < this.survey.nights.length; ++n )
         {
            var night = this.survey.nights[n];
            if ( night.target != target )
               continue;
            total += night.count;

            var row = NightDialog.rowFor( night );
            var node = new TreeBoxNode( parent );
            node.nightRef = night;
            node.setText( 0, row.date + "  " + row.span );
            node.setText( 1, row.count );
            node.setText( 2, row.filters );
            node.setText( 3, String(
               NightDialog.flatsForNight( this.survey, night ).length ) );
         }
         parent.setText( 1, String( total ) );
      }
      for ( var c = 0; c < this.tree.numberOfColumns; ++c )
         this.tree.adjustColumnWidthToContents( c );
   }

   /*
    * What the current row means. A target row selects nothing -- only a
    * night can be imported -- and Run stays disabled until one is chosen.
    */
   remember()
   {
      var node = this.tree.currentNode;
      this.selectedNight = ( node != null && node.nightRef ) ? node.nightRef : null;
      this.okButton.enabled = this.selectedNight != null;
      this.detail.text = this.describe();
   }

   describe()
   {
      if ( this.selectedNight == null )
         return "Select a night.";
      var flats = NightDialog.flatsForNight( this.survey, this.selectedNight );
      return "<b>" + this.selectedNight.target + "</b> &mdash; " +
             this.selectedNight.date + ", " + this.selectedNight.count +
             " frames in " + this.selectedNight.filters.join( ", " ) +
             ".<br/>" + flats.length + " flat" + ( flats.length == 1 ? "" : "s" ) +
             " in this observing session.";
   }

   /* Detached before teardown, like every other dialog here. */
   release()
   {
      try
      {
         this.tree.onCurrentNodeUpdated = null;
         this.tree.onNodeDoubleClicked = null;
         this.okButton.onClick = null;
         this.cancelButton.onClick = null;
      }
      catch ( e ) {}
   }
};
