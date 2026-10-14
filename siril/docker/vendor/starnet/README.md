# Vendored StarNet2 CLI (optional, never committed)

StarNet2 is distributed by https://www.starnetastro.com/cli-tools/starnet/ (Linux, Windows, Intel and
Apple Silicon Mac). Its licence forbids redistribution, so the binary and its weights are NOT in this
repository: everything in this directory except this README is gitignored.

The Docker build downloads the official zip by default. To build without that download (`--build-arg STARNET_URL=
--build-arg STARNET_SHA256=`), unpack the Linux zip yourself into this folder, so that it holds the zip's contents:
`starnet2`, `StarNet2_weights.onnx`, `lib/`, `LICENSE.txt` and `README.txt`.

`docker/Dockerfile` copies this directory to `/opt/starnet/` when no URL is given. Without the binary the image still
builds and the `starnet`-marked tests skip with a reason. The image is for local tests only and is never published.
