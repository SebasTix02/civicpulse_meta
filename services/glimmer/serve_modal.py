"""Serve the tuned Muse Glimmer (base weights + the civicpulse-spec LoRA) as an OpenAI-compatible
endpoint on Modal, for the router's last model link (`muse_glimmer` in civicpulse/config.py).

    modal secret create civicpulse-glimmer GLIMMER_API_KEY=<a long random string>
    modal deploy services/glimmer/serve_modal.py

Then add to the Modal secret `civicpulse-llm` (and your .env to test locally):
    GLIMMER_BASE=https://<workspace>--civicpulse-glimmer-serve-serve.modal.run/v1
    GLIMMER_MODEL=civicpulse-spec
    GLIMMER_API_KEY=<the same string>

Scales to zero (no cost while idle). A cold start loads about 60 GB of weights, so the first call
after a quiet period takes minutes: fine for the last fallback, not for the first provider.
Uses the weights and adapter that train_modal.py left in the volume `civicpulse-glimmer`.
"""

from __future__ import annotations

import subprocess

import modal

VOL = "/vol"
WEIGHTS = f"{VOL}/weights/Muse-Glimmer-30B"
ADAPTER = f"{VOL}/adapters/civicpulse-spec"

image = modal.Image.debian_slim(python_version="3.12").pip_install("vllm>=0.12")
app = modal.App("civicpulse-glimmer-serve", image=image)
vol = modal.Volume.from_name("civicpulse-glimmer", create_if_missing=False)


@app.function(gpu="H100", volumes={VOL: vol}, secrets=[modal.Secret.from_name("civicpulse-glimmer")],
              min_containers=0, scaledown_window=300, timeout=3600)
@modal.concurrent(max_inputs=16)
@modal.web_server(port=8000, startup_timeout=900)
def serve() -> None:
    import os

    cmd = ["vllm", "serve", WEIGHTS, "--served-model-name", "muse-glimmer", "--host", "0.0.0.0", "--port", "8000",
           "--max-model-len", "8192", "--enable-lora", "--max-lora-rank", "16",
           "--lora-modules", f"civicpulse-spec={ADAPTER}", "--limit-mm-per-prompt", '{"image": 8}',
           "--api-key", os.environ["GLIMMER_API_KEY"]]
    subprocess.Popen(cmd)
