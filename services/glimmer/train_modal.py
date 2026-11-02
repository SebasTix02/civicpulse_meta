"""Fine-tune Muse Glimmer 30B (open weights, Apache 2.0) for stage `spec` with LoRA, on Modal.

    modal run services/glimmer/train_modal.py --probe-only # cheap: library versions, chat template,
                                                          # which layers LoRA will touch (CPU only)
    modal run services/glimmer/train_modal.py             # download (first time), train, evaluate
    modal run services/glimmer/train_modal.py --eval-only # evaluate the saved adapter again

Needs data/glimmer/train.jsonl and holdout.jsonl (services/glimmer/make_dataset.py).
One H100 80GB: bf16 weights (about 60 GB) + LoRA rank 16 on the text layers only; the vision
tower stays frozen. The weights are cached in the Modal volume `civicpulse-glimmer` (first run
downloads about 60 GB), the adapter is saved there as adapters/civicpulse-spec and served by
serve_modal.py. Expect about 1 hour of H100 the first time (USD 4 to 6 of Modal credits).

Evaluation: the same 40-60 held-out reports, greedy decoding, scored by civicpulse.spec_grade
(no LLM judge): catalog keywords (today's last fallback), base Glimmer, tuned Glimmer.
The result is written to docs/eval/glimmer_spec_eval.md and data/glimmer/eval_answers.jsonl.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import modal

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2] if len(HERE.parents) > 2 else HERE.parent
MODEL_ID = "meta-models/Muse-Glimmer-30B"
VOL = "/vol"
WEIGHTS = f"{VOL}/weights/Muse-Glimmer-30B"
ADAPTER = f"{VOL}/adapters/civicpulse-spec"
# Text layers only: any projection whose path does not go through the vision tower or projector.
TARGET = r"^(?!.*(vision|visual|projector|mm_|patch|merger)).*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)$"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch>=2.8", "torchvision", "pillow>=10", "transformers>=5.15", "accelerate>=1.10", "peft>=0.17",
                 "huggingface_hub>=0.34",
                 "pydantic>=2.8", "httpx>=0.27", "pyyaml>=6", "fastapi>=0.115", "meta-sam-parser>=0.1")
    .env({"HF_XET_HIGH_PERFORMANCE": "1", "TOKENIZERS_PARALLELISM": "false"})
    .add_local_python_source("civicpulse")
)
app = modal.App("civicpulse-glimmer-train", image=image)
vol = modal.Volume.from_name("civicpulse-glimmer", create_if_missing=True)


def _download() -> None:
    from huggingface_hub import snapshot_download

    if not Path(WEIGHTS, "config.json").exists():
        print(f"downloading {MODEL_ID} to the volume (first run only)...")
        snapshot_download(MODEL_ID, local_dir=WEIGHTS)
        vol.commit()


def _content(text: str) -> list[dict]:
    return [{"type": "text", "text": text}]


def _render(proc, messages: list[dict], add_generation_prompt: bool) -> str:
    msgs = [{"role": m["role"], "content": _content(m["content"])} for m in messages]
    return proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=add_generation_prompt)


@app.function(cpu=4, memory=16384, volumes={VOL: vol}, timeout=3600)
def probe() -> dict:
    """No GPU: versions, one rendered training row, and the LoRA target layers (empty weights)."""
    import accelerate
    import peft
    import torch
    import transformers
    from huggingface_hub import snapshot_download
    from transformers import AutoConfig, AutoModelForMultimodalLM, AutoProcessor

    small = snapshot_download(MODEL_ID, allow_patterns=["*.json", "*.jinja", "*.txt", "*.model", "tokenizer*"])
    proc = AutoProcessor.from_pretrained(small)
    cfg = AutoConfig.from_pretrained(small)
    with accelerate.init_empty_weights():
        model = AutoModelForMultimodalLM.from_config(cfg, dtype=torch.bfloat16)
    import re

    names = [n for n, m in model.named_modules() if isinstance(m, torch.nn.Linear)]
    targets = [n for n in names if re.fullmatch(TARGET, n)]
    row = [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"},
           {"role": "assistant", "content": '{"category_id":"pothole"}'}]
    return {"versions": {"torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__},
            "linear_layers": len(names), "lora_targets": len(targets), "first_targets": targets[:6],
            "skipped_examples": [n for n in names if n not in set(targets)][:12],
            "prompt": _render(proc, row[:2], True), "full": _render(proc, row, False)}


def _load():
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    proc = AutoProcessor.from_pretrained(WEIGHTS)
    tok = proc.tokenizer if hasattr(proc, "tokenizer") else proc
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForMultimodalLM.from_pretrained(WEIGHTS, dtype=torch.bfloat16, device_map="cuda")
    return proc, tok, model


def _encode(proc, tok, messages: list[dict], max_len: int) -> tuple[list[int], list[int]] | None:
    """Token ids and labels with the loss only on the assistant turn."""
    prompt = _render(proc, messages[:-1], True)
    full = _render(proc, messages, False)
    if not full.startswith(prompt):
        return None
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    f_ids = tok(full, add_special_tokens=False)["input_ids"]
    if len(f_ids) > max_len or f_ids[: len(p_ids)] != p_ids:
        return None
    return f_ids, [-100] * len(p_ids) + f_ids[len(p_ids):]


def _generate(proc, tok, model, rows: list[dict], max_new: int, batch: int = 6) -> list[str]:
    import torch

    tok.padding_side = "left"
    out: list[str] = []
    for i in range(0, len(rows), batch):
        prompts = [_render(proc, r["messages"][:-1], True) for r in rows[i:i + batch]]
        enc = tok(prompts, return_tensors="pt", padding=True, add_special_tokens=False).to("cuda")
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=max_new, do_sample=False, pad_token_id=tok.pad_token_id)
        out += tok.batch_decode(gen[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
        print(f"  generated {len(out)}/{len(rows)}")
    return out


@app.function(gpu="H100", volumes={VOL: vol}, timeout=4 * 3600, memory=65536)
def train(train_rows: list[dict], holdout: list[dict], epochs: int = 2, lr: float = 1e-4, rank: int = 16,
          accum: int = 8, max_len: int = 3072, eval_only: bool = False) -> dict:
    import random

    import torch
    from peft import LoraConfig, PeftModel, get_peft_model

    _download()
    proc, tok, model = _load()
    stats: dict = {"train_rows": len(train_rows), "holdout_rows": len(holdout)}
    if eval_only:
        model = PeftModel.from_pretrained(model, ADAPTER)
    else:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()
        model = get_peft_model(model, LoraConfig(r=rank, lora_alpha=rank, lora_dropout=0.0, target_modules=TARGET,
                                                 task_type="CAUSAL_LM"))
        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
        print(f"LoRA trainable parameters: {trainable / 1e6:.1f} M")
        data = [e for r in train_rows if (e := _encode(proc, tok, r["messages"], max_len))]
        stats["encoded_rows"] = len(data)
        opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr, weight_decay=0.0)
        total = epochs * len(data) // accum
        sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / 10) * max(0.0, 1 - s / max(1, total)))
        model.train()
        step, started, losses = 0, time.time(), []
        rng = random.Random(0)
        for epoch in range(epochs):
            rng.shuffle(data)
            for i, (ids, labels) in enumerate(data):
                x = torch.tensor([ids], device="cuda")
                y = torch.tensor([labels], device="cuda")
                loss = model(input_ids=x, labels=y).loss / accum
                loss.backward()
                losses.append(loss.item() * accum)
                if (i + 1) % accum == 0:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    opt.step()
                    sched.step()
                    opt.zero_grad(set_to_none=True)
                    step += 1
                    if step % 5 == 0:
                        recent = sum(losses[-accum * 5:]) / len(losses[-accum * 5:])
                        print(f"epoch {epoch + 1} step {step}/{total} loss {recent:.3f} ({time.time() - started:.0f} s)")
        stats.update(steps=step, train_seconds=round(time.time() - started), final_loss=round(
            sum(losses[-accum * 5:]) / max(1, len(losses[-accum * 5:])), 4))
        model.save_pretrained(ADAPTER)
        vol.commit()
        model.eval()

    if hasattr(model, "gradient_checkpointing_disable"):
        model.gradient_checkpointing_disable()
    model.config.use_cache = True
    started = time.time()
    with model.disable_adapter():
        base = _generate(proc, tok, model, holdout, max_new=1024)  # the base model may think first
    tuned = _generate(proc, tok, model, holdout, max_new=400)
    stats["eval_seconds"] = round(time.time() - started)
    return {"stats": stats, "base": base, "tuned": tuned}


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@app.local_entrypoint()
def main(probe_only: bool = False, eval_only: bool = False, epochs: int = 2) -> None:
    import sys

    sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
    if probe_only:
        print(json.dumps(probe.remote(), indent=2, ensure_ascii=False))
        return
    from civicpulse.catalog import Catalog
    from civicpulse.models import SpecLLMOutput
    from civicpulse.spec_grade import CHECKS, grade, summarize

    data = ROOT / "data" / "glimmer"
    train_rows, holdout = _read(data / "train.jsonl"), _read(data / "holdout.jsonl")
    print(f"train {len(train_rows)}, holdout {len(holdout)}; starting on Modal (H100)...")
    out = train.remote(train_rows, holdout, epochs=epochs, eval_only=eval_only)

    catalog = Catalog.load(ROOT / "packages/catalog/civic_catalog.json")
    known = set(catalog.active_ids())

    def keywords(text: str) -> SpecLLMOutput:  # build_spec's fallback when every model fails
        cat = catalog.keyword_match(text) or "other"
        return SpecLLMOutput(category_id=cat, summary_en="keyword fallback", severity=3, sam_prompts=[],
                             photo_verifiable=cat != "other", confidence=0.5, contains_personal_data=True)

    results = {}
    answers = []
    for name, outputs in (("keywords", [keywords(r["messages"][1]["content"]) for r in holdout]),
                          ("glimmer_base", out["base"]), ("glimmer_tuned", out["tuned"])):
        grades = [grade(a, r["gold_category"], r["teacher"], known) for a, r in zip(outputs, holdout, strict=True)]
        results[name] = summarize(grades)
        for a, r, g in zip(outputs, holdout, grades, strict=True):
            answers.append({"model": name, "key": r["key"], "gold": r["gold_category"],
                            "answer": a if isinstance(a, str) else a.model_dump(mode="json"), **g.as_dict()})
    tuned_ok = [g["category"] for g in answers if g["model"] == "glimmer_tuned"]
    base_ok = [g["category"] for g in answers if g["model"] == "glimmer_base"]
    fixed = sum(t and not b for t, b in zip(tuned_ok, base_ok, strict=True))
    broken = sum(b and not t for t, b in zip(tuned_ok, base_ok, strict=True))

    (data / "eval_answers.jsonl").write_text("\n".join(json.dumps(a, ensure_ascii=False) for a in answers) + "\n",
                                             encoding="utf-8")
    s = out["stats"]
    lines = [f"# Muse Glimmer for stage `spec`, {time.strftime('%Y-%m-%d')}", "",
             (f"LoRA rank 16 on the text layers of Muse Glimmer 30B, {s.get('steps', '-')} steps over "
             f"{s.get('encoded_rows', s['train_rows'])} synthetic reports labeled by Muse Spark Open "
             f"(teacher kept only when it agreed with the intended category). One H100 on Modal, "
             f"training {s.get('train_seconds', '-')} s. Held out: {s['holdout_rows']} reports from batches never "
             "trained on. Greedy decoding, scored by `civicpulse/spec_grade.py` (no LLM judge)."), "",
             "| | " + " | ".join(CHECKS) + " |", "|---|" + "---|" * len(CHECKS)]
    for name, summ in results.items():
        lines.append(f"| {name} | " + " | ".join(f"{summ[c]:.0%}" for c in CHECKS) + " |")
    lines += ["", f"Category: tuned fixed {fixed} answers of the base model and broke {broken}.", "",
              ("Checks: valid spec JSON; category equals the intended one (unknown ids count as other); severity "
              "within 1 of the teacher; personal-data flag equals the teacher's; SAM prompts present when the "
              "problem is photo-verifiable. `keywords` is today's last fallback when every model fails.")]
    report = ROOT / "docs" / "eval" / "glimmer_spec_eval.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwritten {report} and {data / 'eval_answers.jsonl'}")
