#!/usr/bin/env bash
set -euo pipefail

ACR_NAME="praxismoveacr"
IMAGE_NAME="praxis-backend"

docker build -t "${IMAGE_NAME}:latest" .

az acr login --name "$ACR_NAME"

docker tag "${IMAGE_NAME}:latest" "${ACR_NAME}.azurecr.io/${IMAGE_NAME}:latest"
docker push "${ACR_NAME}.azurecr.io/${IMAGE_NAME}:latest"

chmod +x build-image.sh