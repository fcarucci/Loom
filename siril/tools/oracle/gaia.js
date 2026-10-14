/*
 * Oracle for loomsiril/gaia.py (E2b): Fly.gaiaOnlineUrl, Fly.parseGaiaCsv and the
 * Sky.js online constants (Sky.GAIA_ONLINE_TIMEOUT in seconds; the stall, first
 * byte and total limits in ms there, recorded as ms and converted by the port).
 * Inputs are stored next to the outputs; the Python test replays them.
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly, Sky = js.Sky;
   const centres = [ { ra: 315.4, dec: 68.16, r: 0.1, g: 10 }, { ra: 324.745, dec: 57.514, r: 1.4, g: 16 },
                     { ra: 0.0001, dec: -89.5, r: 0.5, g: 12.5 } ];
   const header = "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag";
   const texts = {
      good: header + "\n315.40392650999,68.16326234645,2.8175,7.1,-2.2,7.179865,7.3,6.9\n316.1,68.2,,,,17.1,,\n",
      crlf: header + "\r\n315.5,68.1,1.25,,-2.2,9.5,,6.9\r\n315.6,68.2,0.5,0.1,0.2,11.5,11.8,11.1\r\n",
      wrongHeader: "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag\n1,2,3,4,5,6,7\n",
      errorPage: "<VOTABLE><INFO name=\"QUERY_STATUS\" value=\"ERROR\"/></VOTABLE>",
      empty: "",
      headerOnly: header + "\n",
      shortRow: header + "\n1,2,3,4,5,6,7\n10,20,30,40,50,60,70,80\n",
      blanks: header + "\n,,,,,,,\n1.5,2.5,,,,,,\n",
      trailingBlank: header + "\n1,2,3,4,5,6,7,8\n\n"
   };
   const cases = {
      constants: { url: Fly.GAIA_ONLINE_URL, columns: Fly.GAIA_ONLINE_COLUMNS, maxRows: Fly.GAIA_ONLINE_MAX_ROWS,
                   timeoutS: Sky.GAIA_ONLINE_TIMEOUT, stallMs: Sky.GAIA_ONLINE_STALL,
                   firstByteMs: Sky.GAIA_ONLINE_FIRST_BYTE, totalMs: Sky.GAIA_ONLINE_TOTAL },
      url: centres.map( c => ( { args: [ { ra: c.ra, dec: c.dec }, c.r, c.g ], out: Fly.gaiaOnlineUrl( { ra: c.ra, dec: c.dec }, c.r, c.g ) } ) ),
      parseGaiaCsv: Object.keys( texts ).map( k => ( { name: k, args: [ texts[k] ], out: Fly.parseGaiaCsv( texts[k] ) } ) )
   };
   cases.parseGaiaCsv.push( { name: "null", args: [ null ], out: Fly.parseGaiaCsv( null ) } );
   return cases;
};
