"""Single-shot local SD1.5 generation (runs under .venv-gen, Python 3.12).

One subprocess per image, one pipeline in memory, deterministic seeds,
flushed per-step progress so detached batches stay observable.

Usage:
  .venv-gen\\Scripts\\python.exe -X utf8 src/generator/run_generate.py \
      --mode img2img --prompt "..." --negative "..." --ref <path> \
      --out data/generated/x.png --denoise 0.45 --steps 24 --seed 7

Modes:
  text2img : absent --ref, width/height from --size
  img2img  : ref photo required; ref is square-cropped via anchors.prep_square
"""

import argparse
import json
import os
import sys
import time

import psutil
import torch
from diffusers import AutoPipelineForImage2Image, AutoPipelineForText2Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.generator.anchors import prep_square

MODEL = "Lykon/dreamshaper-8"
torch.set_num_threads(4)


def _emit(msg: str) -> None:
    print(msg, flush=True)


def _progress(pipe, step_index, timestep, callback_kwargs):
    _emit(f"STEP {step_index}")
    return callback_kwargs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["text2img", "img2img"], required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--negative", default="")
    ap.add_argument("--ref", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=24)
    ap.add_argument("--denoise", type=float, default=0.45)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--size", type=int, default=512)
    args = ap.parse_args()

    t0 = time.time()
    _emit(f"load {MODEL} ({args.mode}) with {torch.get_num_threads()} threads")
    cls = (
        AutoPipelineForImage2Image if args.mode == "img2img"
        else AutoPipelineForText2Image
    )
    pipe = cls.from_pretrained(MODEL, dtype=torch.float32, safety_checker=None)
    t_load = time.time() - t0
    _emit(f"loaded {t_load:.0f}s")

    gen = torch.Generator(device="cpu").manual_seed(args.seed)
    t1 = time.time()
    kwargs = {
        "prompt": args.prompt,
        "negative_prompt": args.negative or None,
        "num_inference_steps": args.steps,
        "generator": gen,
        "callback_on_step_end": _progress,
    }
    if args.mode == "img2img":
        kwargs.update(image=prep_square(args.ref, args.size),
                      strength=args.denoise)
    else:
        kwargs.update(width=args.size, height=args.size)

    img = pipe(**kwargs).images[0]
    t_gen = time.time() - t1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    img.save(args.out)
    peak = psutil.Process().memory_info().peak_wset / 1e6
    _emit(f"DONE out={args.out} gen={t_gen:.0f}s peak={peak:.0f}MB")
    with open(args.out + ".json", "w") as fh:
        json.dump({
            "mode": args.mode, "prompt": args.prompt, "negative": args.negative,
            "ref": args.ref, "steps": args.steps, "denoise": args.denoise,
            "seed": args.seed, "size": args.size,
            "load_s": round(t_load, 1), "gen_s": round(t_gen, 1),
            "peak_mb": round(peak, 1),
        }, fh, indent=2)


if __name__ == "__main__":
    main()