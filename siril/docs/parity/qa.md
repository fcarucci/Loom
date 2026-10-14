# Acceptance tests: parity rows

The black-box acceptance tests (tests/acceptance/**) port no JS function.

| JS | Python | Status | Test |
|---|---|---|---|
| (none: the acceptance tests port no JS) | -- | D | tests/acceptance/test_pr8_parity.py checks docs/PARITY.md against tests/acceptance/js_inventory.txt |

## Temporary gates (all removed at E9; none remain)

| Gate | Test (tests/acceptance/) | Reason |
|---|---|---|
| xfail(strict=False) | test_pr8_parity.py::test_every_js_name_appears_in_parity_md | PARITY.md completed in E9 |
| skipif CHANGELOG.md missing (`DOCS_PENDING`) | test_pr9_docs_hygiene.py::test_readme_has_section[*], test_readme_mentions_starnet_and_gaia_attribution, test_changelog_has_unreleased | README sections + CHANGELOG land in E8 |
| skipif ci.yml missing (`CI_PENDING`) | test_pr9_docs_hygiene.py::test_ci_workflow_present_and_yamlish | ci.yml lands in E8 |
| importorskip loomsiril.cli | test_pr2_3_5_cli.py (module), test_pr4_frame0.py (module), test_pr6_network.py::test_gaia_online_is_refused_under_pytest | cli lands in E6 |
| importorskip loomsiril.parallax | test_pr6_network.py::test_ic1396_cluster_distance | parallax lands in E1a |
| skip if siril script/stub missing | test_pr7_siril_script.py (3 tests) | E7 |
Permanent skip (documented): `ffmpeg` marker test test_pr2_3_5_cli.py::test_video_is_encoded_when_ffmpeg_exists when ffmpeg is absent.

## Decisions

- Hygiene size rule (2026-10-09): files over 1 MB are refused except the Gaia fixture and synthetic oracle
  JSON (tests/oracle/*.json, 5 MB cap per file). Reason: the rule keeps real frames and binaries out; oracle JSON
  is synthetic data recorded from the JS (starfit.json 2.4 MB, starlayer.json 2.0 MB at fly/e6).

E9: every gate above removed; the README usage regex no longer accepts a bare `cli`.
