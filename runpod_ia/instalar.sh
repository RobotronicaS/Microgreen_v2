#!/usr/bin/env bash
set -euo pipefail
umask 077
raiz="/workspace/microgreen"
mkdir -p "$raiz/modelos"
python -m venv --system-site-packages "$raiz/venv-ia"
SAM2_BUILD_CUDA=0 "$raiz/venv-ia/bin/python" -m pip install --no-build-isolation \
  'git+https://github.com/facebookresearch/sam2.git@2b90b9f5ceec907a1c18123530e92e794ad901a4' \
  'fastapi==0.115.11' 'uvicorn==0.32.0' 'Pillow==11.1.0' 'numpy==2.2.4' 'python-multipart==0.0.20' 'httpx==0.28.1'
if [[ ! -s "$raiz/modelos/sam2.1_hiera_tiny.pt" ]]; then
  curl --fail --location --max-time 300 https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt \
    -o "$raiz/modelos/sam2.1_hiera_tiny.pt.part"
  mv "$raiz/modelos/sam2.1_hiera_tiny.pt.part" "$raiz/modelos/sam2.1_hiera_tiny.pt"
fi
"$raiz/venv-ia/bin/python" -c 'import torch,sam2,fastapi; assert torch.cuda.is_available(); print("SAM2 y CUDA disponibles.")'
printf '%s\n' 'Configurá el acceso con python -m runpod_ia.configurar_token antes de iniciar la API.'
