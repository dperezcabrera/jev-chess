"""A Hugging Face token for the demo Spaces: their shared GPUs give an anonymous caller a handful of runs
and a signed-in one many more. Read from HF_TOKEN, else from the token file `huggingface-cli login` writes."""

import os
from pathlib import Path


def hf_token() -> str:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""
    if token:
        return token.strip()
    for path in (Path.home() / ".cache" / "huggingface" / "token", Path.home() / ".huggingface" / "token"):
        try:
            return path.read_text().strip()
        except OSError:
            continue
    return ""


def hf_headers() -> dict[str, str]:
    token = hf_token()
    return {"Authorization": f"Bearer {token}"} if token else {}
