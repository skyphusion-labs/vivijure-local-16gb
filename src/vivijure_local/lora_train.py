"""Per-character SDXL LoRA training for the local-consumer door.

Same job as vivijure-backend's lora_train: a DreamBooth-style UNet-only adapter from the bundle's
reference images, written as `pytorch_lora_weights.safetensors` for the door's `preview` stage to
load. Lives on the homelab card so cast identity does not require a cloud GPU endpoint.

Heavy imports (torch / diffusers / peft) are deferred into `train_slot` so CPU tests cover captions
and config without a GPU image.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .core.bundle import Character

# Same base the local preview keyframe stage draws with (preview_sdxl.DEFAULT_MODEL).
DEFAULT_BASE_REPO = "SG161222/RealVisXL_V5.0"

UNET_TARGET_MODULES = ["to_k", "to_q", "to_v", "to_out.0"]


@dataclass
class LoraTrainConfig:
    """Knobs for one slot. Defaults fit a few-reference cast on a consumer card (gradient
    checkpointing keeps 1024 SDXL UNet train inside mid-tier VRAM)."""
    rank: int = 16
    resolution: int = 1024
    learning_rate: float = 1e-4
    max_steps: int = 1000  # actual steps = max(50, max_steps * ref_count // 5)
    batch_size: int = 1
    gradient_accumulation_steps: int = 1
    seed: int = 0
    random_flip: bool = True
    gradient_checkpointing: bool = True
    caption_template: str = "{name}, {prompt}"
    save_every: int = 0
    lora_alpha: int | None = None


@dataclass
class TrainedLora:
    slot: str
    path: Path
    trigger: str
    steps: int
    rank: int
    ref_count: int
    base_repo: str
    checkpoint_dirs: list[Path] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def default_base_repo() -> str:
    return DEFAULT_BASE_REPO


def caption_for(char: Character, template: str) -> str:
    """Safe str.replace caption; only {name} and {prompt} are allowed placeholders."""
    if "{" in template or "}" in template:
        filled = template.replace("{name}", char.name or char.slot).replace(
            "{prompt}", char.prompt or "")
        if any(c in filled for c in "{}"):
            raise ValueError(
                f"caption_template contains unsupported placeholders: {template!r}; "
                "only {name} and {prompt} are allowed")
        text = filled
    else:
        text = template
    return ", ".join(part.strip() for part in text.split(",") if part.strip())


def config_from_overrides(raw: dict | None) -> LoraTrainConfig:
    """Parse render_overrides.lora into LoraTrainConfig (forgiving + clamped)."""
    base = LoraTrainConfig()
    d = raw if isinstance(raw, dict) else {}

    def _int(key: str, lo: int, hi: int, default: int) -> int:
        v = d.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return default
        return max(lo, min(hi, int(v)))

    def _float(key: str, lo: float, hi: float, default: float) -> float:
        v = d.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return default
        return max(lo, min(hi, float(v)))

    alpha = d.get("lora_alpha")
    return LoraTrainConfig(
        rank=_int("rank", 1, 128, base.rank),
        resolution=_int("resolution", 512, 1536, base.resolution),
        learning_rate=_float("learning_rate", 1e-6, 1e-2, base.learning_rate),
        max_steps=_int("max_steps", 1, 5000, base.max_steps),
        batch_size=_int("batch_size", 1, 8, base.batch_size),
        gradient_accumulation_steps=_int(
            "gradient_accumulation_steps", 1, 32, base.gradient_accumulation_steps),
        seed=_int("seed", 0, 2**31 - 1, base.seed),
        random_flip=bool(d.get("random_flip", base.random_flip)),
        gradient_checkpointing=bool(d.get("gradient_checkpointing", base.gradient_checkpointing)),
        caption_template=str(d.get("caption_template", base.caption_template)),
        save_every=_int("save_every", 0, 5000, base.save_every),
        lora_alpha=(_int("lora_alpha", 1, 256, base.rank)
                    if isinstance(alpha, (int, float)) and not isinstance(alpha, bool) else None),
    )


def train_slot(
    char: Character,
    out_dir: Path,
    *,
    config: LoraTrainConfig | None = None,
    base_repo: str | None = None,
    progress_cb=None,
) -> TrainedLora:
    """Train one character's SDXL LoRA from its reference images and save the adapter."""
    cfg = config or LoraTrainConfig()
    base = base_repo or default_base_repo()
    refs = list(char.ref_paths)
    if not refs:
        raise ValueError(f"slot {char.slot} ({char.name}) has no reference images to train on")

    import torch
    import torch.nn.functional as F
    from diffusers import (
        AutoencoderKL,
        DDPMScheduler,
        StableDiffusionXLPipeline,
        UNet2DConditionModel,
    )
    from diffusers.utils import convert_state_dict_to_diffusers
    from peft import LoraConfig
    from peft.utils import get_peft_model_state_dict
    from transformers import CLIPTokenizer, CLIPTextModel, CLIPTextModelWithProjection

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    device = "cuda"
    weight_dtype = torch.bfloat16
    torch.manual_seed(cfg.seed)

    # Homelab doors download weights on first use (same as preview); do not force offline-only.
    vae = AutoencoderKL.from_pretrained(base, subfolder="vae", torch_dtype=torch.float32).to(device)
    tokenizer_one = CLIPTokenizer.from_pretrained(base, subfolder="tokenizer")
    tokenizer_two = CLIPTokenizer.from_pretrained(base, subfolder="tokenizer_2")
    text_encoder_one = CLIPTextModel.from_pretrained(
        base, subfolder="text_encoder", torch_dtype=weight_dtype).to(device)
    text_encoder_two = CLIPTextModelWithProjection.from_pretrained(
        base, subfolder="text_encoder_2", torch_dtype=weight_dtype).to(device)
    unet = UNet2DConditionModel.from_pretrained(
        base, subfolder="unet", torch_dtype=weight_dtype).to(device)
    noise_scheduler = DDPMScheduler.from_pretrained(base, subfolder="scheduler")

    for module in (vae, text_encoder_one, text_encoder_two, unet):
        module.requires_grad_(False)

    lora_alpha = cfg.lora_alpha if cfg.lora_alpha is not None else cfg.rank
    unet.add_adapter(LoraConfig(
        r=cfg.rank,
        lora_alpha=lora_alpha,
        init_lora_weights="gaussian",
        target_modules=UNET_TARGET_MODULES,
    ))
    if cfg.gradient_checkpointing:
        unet.enable_gradient_checkpointing()
    lora_params = [p for p in unet.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(lora_params, lr=cfg.learning_rate)

    caption = caption_for(char, cfg.caption_template)
    prompt_embeds, pooled_prompt_embeds = _encode_prompt(
        caption, [tokenizer_one, tokenizer_two], [text_encoder_one, text_encoder_two],
        device, weight_dtype)

    target_size = (cfg.resolution, cfg.resolution)
    latents: list[torch.Tensor] = []
    time_ids: list[torch.Tensor] = []
    flipped_time_ids: list[torch.Tensor] = []
    for ref in refs:
        pixels, original_size, crop_top_left = _load_image(ref, cfg.resolution)
        pixels = pixels.to(device, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            latent = vae.encode(pixels).latent_dist.sample() * vae.config.scaling_factor
        latents.append(latent.to(weight_dtype).squeeze(0))
        time_ids.append(_time_ids(original_size, crop_top_left, target_size, device, weight_dtype))
        scale = cfg.resolution / min(original_size[1], original_size[0])
        new_w = round(original_size[1] * scale)
        flipped_left = new_w - crop_top_left[1] - cfg.resolution
        flipped_time_ids.append(_time_ids(
            original_size, (crop_top_left[0], flipped_left), target_size, device, weight_dtype))

    # Drop frozen encode path off the card so the UNet train loop has headroom.
    del vae, text_encoder_one, text_encoder_two
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    unet.train()
    generator = torch.Generator(device=device).manual_seed(cfg.seed)
    n = len(latents)
    add_text_embeds = pooled_prompt_embeds
    effective_steps = max(50, cfg.max_steps * n // 5)
    last_loss = 0.0
    checkpoint_dirs: list[Path] = []
    for step in range(effective_steps):
        bs = min(cfg.batch_size, n)
        idxs = [int(torch.randint(0, n, (1,), generator=generator, device=device).item())
                for _ in range(bs)]
        latent_b = torch.stack([latents[i].clone() for i in idxs])
        add_time = torch.cat([time_ids[i] for i in idxs], dim=0)
        if cfg.random_flip:
            for b_i, idx_i in enumerate(idxs):
                if torch.rand(1, generator=generator, device=device).item() < 0.5:
                    latent_b[b_i] = torch.flip(latent_b[b_i], dims=[-1])
                    add_time[b_i] = flipped_time_ids[idx_i]

        noise = torch.randn(latent_b.shape, generator=generator, device=device, dtype=weight_dtype)
        timestep = torch.randint(
            0, noise_scheduler.config.num_train_timesteps, (bs,),
            generator=generator, device=device).long()
        noisy = noise_scheduler.add_noise(latent_b, noise, timestep)

        model_pred = unet(
            noisy, timestep, prompt_embeds.expand(bs, -1, -1),
            added_cond_kwargs={
                "text_embeds": add_text_embeds.expand(bs, -1),
                "time_ids": add_time,
            },
            return_dict=False,
        )[0]
        target = _loss_target(noise_scheduler, latent_b, noise, timestep)
        loss = F.mse_loss(model_pred.float(), target.float(), reduction="mean")
        loss = loss / cfg.gradient_accumulation_steps
        loss.backward()
        if (step + 1) % cfg.gradient_accumulation_steps == 0:
            torch.nn.utils.clip_grad_norm_(lora_params, 1.0)
            optimizer.step()
            optimizer.zero_grad()
        last_loss = float(loss.item()) * cfg.gradient_accumulation_steps

        if cfg.save_every and (step + 1) % cfg.save_every == 0 and (step + 1) < effective_steps:
            ckpt_dir = out_dir / f"checkpoint-{step + 1}"
            _save_adapter(unet, ckpt_dir, StableDiffusionXLPipeline,
                          get_peft_model_state_dict, convert_state_dict_to_diffusers)
            checkpoint_dirs.append(ckpt_dir)
        if (step + 1) % 50 == 0 or step == 0:
            print(f"[lora {char.slot}] step {step + 1}/{effective_steps} loss={last_loss:.4f}",
                  flush=True)
            if progress_cb is not None:
                try:
                    progress_cb(step + 1, effective_steps, last_loss)
                except Exception:
                    pass

    _save_adapter(unet, out_dir, StableDiffusionXLPipeline,
                  get_peft_model_state_dict, convert_state_dict_to_diffusers)
    saved = out_dir / "pytorch_lora_weights.safetensors"

    # Release train-resident modules so the next preview/i2v job can reclaim the card.
    del unet, optimizer, lora_params
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return TrainedLora(
        slot=char.slot,
        path=saved,
        trigger=char.name or char.slot,
        steps=effective_steps,
        rank=cfg.rank,
        ref_count=n,
        base_repo=base,
        checkpoint_dirs=checkpoint_dirs,
        meta={"caption": caption, "final_loss": round(last_loss, 4), "device": device},
    )


def _encode_prompt(prompt, tokenizers, text_encoders, device, dtype):
    import torch

    embeds_list = []
    pooled = None
    for tokenizer, text_encoder in zip(tokenizers, text_encoders):
        ids = tokenizer(
            prompt, padding="max_length", max_length=tokenizer.model_max_length,
            truncation=True, return_tensors="pt").input_ids.to(device)
        out = text_encoder(ids, output_hidden_states=True, return_dict=False)
        pooled = out[0]
        embeds_list.append(out[-1][-2])
    prompt_embeds = torch.concat(embeds_list, dim=-1).to(dtype)
    return prompt_embeds, pooled.to(dtype)


def _time_ids(original_size, crop_top_left, target_size, device, dtype):
    import torch
    return torch.tensor([list(original_size) + list(crop_top_left) + list(target_size)],
                        device=device, dtype=dtype)


def _loss_target(noise_scheduler, latent, noise, timestep):
    if noise_scheduler.config.prediction_type == "v_prediction":
        return noise_scheduler.get_velocity(latent, noise, timestep)
    return noise


def _load_image(path: Path, resolution: int):
    import torch
    from PIL import Image

    img = Image.open(path).convert("RGB")
    original_size = (img.height, img.width)
    scale = resolution / min(img.width, img.height)
    new_w, new_h = round(img.width * scale), round(img.height * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - resolution) // 2
    top = (new_h - resolution) // 2
    img = img.crop((left, top, left + resolution, top + resolution))

    import numpy as np
    arr = torch.from_numpy(np.asarray(img, dtype="float32") / 255.0)
    pixels = arr.permute(2, 0, 1) * 2.0 - 1.0
    return pixels, original_size, (top, left)


def _save_adapter(unet, out_dir, pipeline_cls, get_peft_model_state_dict, convert_state_dict_to_diffusers):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    unet_lora_layers = convert_state_dict_to_diffusers(get_peft_model_state_dict(unet))
    pipeline_cls.save_lora_weights(save_directory=str(out_dir), unet_lora_layers=unet_lora_layers)
