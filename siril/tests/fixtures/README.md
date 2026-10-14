# Test fixtures

- `ic1396-gaia-g16.tsv`: Gaia DR3 sources within 1.4 deg of IC 1396
  (RA 324.745 deg, Dec +57.514 deg), G < 16: 8093 sources (8094 lines with the
  header), columns `ra dec plx pmra pmdec G BP RP`, tab separated. Copied
  unchanged from Loom v0.4.1 `ci/fixtures/ic1396-gaia-g16.tsv`
  (https://github.com/fcarucci/Loom, MIT), where it was recorded on 2026-09-23
  with PixInsight's Gaia process and trimmed to the precision the cluster finder
  uses. The cluster regression test reads it; the suite never queries a
  catalogue.

  Attribution: this work has made use of data from the European Space Agency
  (ESA) mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia
  Data Processing and Analysis Consortium (DPAC,
  https://www.cosmos.esa.int/web/gaia/dpac/consortium). Funding for the DPAC
  has been provided by national institutions, in particular the institutions
  participating in the Gaia Multilateral Agreement. Gaia DR3: Gaia
  Collaboration, Vallenari et al. (2023), A&A 674, A1.

Everything else the tests use is synthetic and built in code (`tests/synth.py`).
Never add a real frame here.
