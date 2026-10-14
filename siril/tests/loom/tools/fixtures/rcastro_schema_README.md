# Synthetic rc-astro schemas

These files are invented for the tests. They are not copies of any vendor output.

Each file maps a sub-command (`bxt`, `nxt`, `sxt`) to the object `rc-astro <sub> --json` would print: `schemaVersion`,
`tool`, `version`, `license.valid` and a `parameters` list of `{name, flag, type, min, max}` records.

* `rcastro_schema_v5.json`: schema version 5, the baseline flags.
* `rcastro_schema_v6.json`: schema version 6. Flags renamed on purpose so the tests prove that flags come from the
  live schema: `sharpen_stars` is `--stars`, `auto_psf` is `--auto-psf`, and the nxt detail parameter is
  `--detail-recovery`.

Parameter names are the keys the adapter looks up. A name missing from a schema falls back to the static table in
`loomsiril/loom/tools/rcastro.py`.
