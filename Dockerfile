# The application image builds FROM the ACR base image, which already
# contains every heavy, rarely-changing layer: apt packages, pip
# dependencies (incl. Maia-3), the Maia-3 checkpoint, and the full
# 3-4-5-man Syzygy set (~984 MB). A deploy therefore only copies source
# instead of re-downloading the tablebases from Lichess on every push.
#
# The base is digest-pinned for reproducibility. Rebuild it via
# Dockerfile.base (see that file) and update the digest below when
# requirements.txt, the pre-warm scripts, or src/engines change.
#
# Building locally requires registry access:
#   az acr login --name praxismoveacr
FROM praxismoveacr.azurecr.io/praxis-base@sha256:4b262fe360d893e323ae4304ee7faf590ca3a28efdf501078dafed3aadf67b2e

WORKDIR /app

COPY . .

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
