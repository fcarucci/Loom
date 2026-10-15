# Test image for Loom for Siril: the full pytest suite (ffmpeg, Siril AppImage, StarNet2 CLI when present).
# Built and run on a Docker host (see docker/run-tests.sh); the image is never pushed.
#   docker build -f docker/Dockerfile -t loomsiril-test:e8 .
#   docker run --rm loomsiril-test:e8
FROM python:3.12-slim-trixie

# Siril 1.4.4, Linux x86_64 AppImage, from https://free-astro.org/download/ (sha256 pinned 2026-10-09).
ARG SIRIL_APPIMAGE_URL=https://free-astro.org/download/Siril-1.4.4-x86_64.AppImage
ARG SIRIL_APPIMAGE_SHA256=47f3f299f6771888516bebed076580a57f7abe76484981f387a325115547ed78
# StarNet2 Linux CLI 2.6.2 (official download, licence rev 2026-09-07; the test image only, never published).
# Built with both args empty (--build-arg STARNET_URL= STARNET_SHA256=) the image uses docker/vendor/starnet/ instead.
ARG STARNET_URL=https://download.starnetastro.com/starnet2_linux_2.6.2-0242_ORT_x64_cli.zip
ARG STARNET_SHA256=066a7683f0321ae8400d3bc1023a48747ac228e1d14e76b5d39c4e48cbce4920

# APPDIR: the extracted AppImage's root. siril-cli needs it to find its bundled Python module when it builds its
# Python environment for `pyscript` (without it the environment setup fails and no Python script runs).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOOMSIRIL_PERF=1 \
    LOOMSIRIL_STARNET=/opt/starnet/starnet2 \
    APPDIR=/opt/siril \
    SIRIL_PYTHON_MODULE=/opt/siril/usr/share/siril/python_module

# git: docker/network-setup.sh fetches Siril's SPCC database for the opt-in network tests (never at build time).
# libtk8.6 is the Tk runtime that python:3.12-slim's _tkinter links against (it pulls in libtcl8.6). No X server:
# the tests that need a display skip on tkinter.Tk() TclError.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip xz-utils libtk8.6 git \
 && rm -rf /var/lib/apt/lists/*

# Siril: extract the AppImage (no FUSE needed). AppRun picks usr/bin by its own basename, so the
# siril-cli symlink runs the bundled siril-cli. The bundled libraries run on trixie's glibc (siril-cli 1.4.4
# was probed here); the RUN below only reports that probe and never fails the build.
RUN set -eux; \
    mkdir -p /opt/dl; \
    curl -fL "$SIRIL_APPIMAGE_URL" -o /opt/dl/siril.AppImage; \
    echo "$SIRIL_APPIMAGE_SHA256  /opt/dl/siril.AppImage" | sha256sum -c -; \
    chmod 755 /opt/dl/siril.AppImage; \
    (cd /opt/dl && ./siril.AppImage --appimage-extract >/dev/null); \
    mv /opt/dl/squashfs-root /opt/siril; \
    rm -rf /opt/dl; \
    ln -s /opt/siril/AppRun /usr/local/bin/siril-cli; \
    ( siril-cli --version 2>&1 | head -n 3 ) || echo "siril-cli did not run headless in this image"

# StarNet2: fetched at build time from the official URL (sha256 checked; the build fails on a mismatch) or taken from
# the gitignored docker/vendor/starnet/. Installed as /opt/starnet/{starnet2, StarNet2_weights.onnx, lib/, LICENSE.txt,
# README.txt}: the layout the zip documents, so the binary finds its weights and runtime libraries next to itself, and
# the licence text sits beside it for the Siril script (loomsiril.starnet_licence). LOOMSIRIL_STARNET points here.
COPY docker/vendor/starnet/ /opt/starnet/
RUN set -eux; \
    if [ -n "$STARNET_URL" ]; then \
      mkdir -p /opt/dl/starnet; \
      curl -fL "$STARNET_URL" -o /opt/dl/starnet.zip; \
      echo "$STARNET_SHA256  /opt/dl/starnet.zip" | sha256sum -c -; \
      unzip -q /opt/dl/starnet.zip -d /opt/dl/starnet; \
      top="$(find /opt/dl/starnet -mindepth 1 -maxdepth 1 -type d | head -n 1)"; \
      cp -a "$top"/. /opt/starnet/; \
      rm -rf /opt/dl; \
      chmod 755 /opt/starnet/starnet2; \
      /opt/starnet/starnet2 --version; \
    fi; \
    if [ ! -f /opt/starnet/starnet2 ] && [ -f /opt/starnet/starnet++ ]; then \
      mv /opt/starnet/starnet++ /opt/starnet/starnet2; \
    fi; \
    if [ -f /opt/starnet/starnet2 ]; then \
      chmod 755 /opt/starnet/starnet2; \
    else \
      echo "StarNet not installed: no /opt/starnet/starnet2; the starnet tests will skip"; \
    fi

WORKDIR /work
# Python deps first (layer cache). sirilpy is NOT installed here: the pure tests use the stub; Siril scripts run in
# Siril's own Python environment (below).
RUN pip install --no-cache-dir numpy scipy tifffile pytest

# Tests run as a non-root user with a writable HOME (Siril writes ~/.config/siril and ~/.local/share/siril).
RUN useradd --create-home --uid 1000 --shell /bin/bash loom
ENV HOME=/home/loom
USER loom

# Siril's Python environment, built once here (its first `pyscript` creates ~/.local/share/siril/venv and installs
# sirilpy's requirements from the network), plus the packages the Loom script needs inside it. The test run itself
# then works without a network connection.
RUN set -eux; \
    mkdir -p /tmp/warm; \
    printf 'import sirilpy\nsirilpy.SirilInterface().connect()\nprint("siril python ready")\n' > /tmp/warm/warm.py; \
    printf 'requires 1.4.0\npyscript /tmp/warm/warm.py\n' | siril-cli -d /tmp/warm -s - 2>&1 | grep -E "siril python ready"; \
    "$HOME/.local/share/siril/venv/bin/python" -m pip install --no-cache-dir numpy scipy tifffile; \
    "$HOME/.local/share/siril/venv/bin/python" -c "import numpy, scipy, tifffile, sirilpy"; \
    rm -rf /tmp/warm

COPY . /work
# The tree is on PYTHONPATH (no install step).
ENV PYTHONPATH=/work

CMD ["python", "-m", "pytest", "-q", "-rs"]
