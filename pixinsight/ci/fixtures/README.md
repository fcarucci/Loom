# Test fixtures

- `ic1396-gaia-g16.tsv`: Gaia DR3 sources within 1.4° of IC 1396
  (RA 324.745°, Dec +57.514°), G < 16, recorded 2026-09-23 with PixInsight's
  Gaia process and trimmed to the precision the finder uses. The cluster
  regression test reads it; the suite never queries a catalogue.
- `steps-members.json`: every own member of `Steps` as the node suite loads
  it, recorded 2026-09-26 from the single-file `Steps.js` (b4463a5): typeof
  and sha1 of each function's source or value's canonical text. The Steps
  member check asserts the namespace still matches it after `Steps.js` was
  split into `Steps.js`, `StepsSyqon.js` and `StepsIcc.js`; the
  `ci/pi-entry-alone` drivers check its member types in PixInsight for
  Loom.js and FlyThrough.js loaded alone. A change that deliberately edits a Steps member
  re-records that member's line in the same commit (noted in the file's
  `about`).
