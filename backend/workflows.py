"""ComfyUI API-format workflow builders.

Each builder returns a {node_id: {class_type, inputs}} graph ready for /prompt.
Node IO verified against ComfyUI 0.25.0 /object_info on the box (Phase 1).
Transparency for the EDIT path is handled in the backend (rembg matting), because
Qwen-Image-Edit is an RGB model and returns the subject on an opaque background.
"""
from __future__ import annotations
from . import config

M = config.MODELS


def qwen_edit_variant(image_name: str, prompt: str, *, negative: str = "",
                      seed: int = 0, steps: int = 4, cfg: float = 1.0,
                      sampler: str = "euler", scheduler: str = "simple",
                      denoise: float = 1.0, prefix: str = "sf_variant") -> dict:
    """Qwen-Image-Edit-2511 masked/instruction edit of an uploaded image.

    Preserves pose/canvas; cfg pinned to 1.0 for the Lightning 4-step LoRA
    (scar tissue: cfg>1 doubles the working set and thrashes). Verified working
    in Phase 1c (cold 12.4s / warm 6.2s, resident).
    """
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": M["qwen_dit"], "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": M["qwen_lightning_lora"],
                         "strength_model": 1.0}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": M["qwen_te"], "type": "qwen_image"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": M["qwen_vae"]}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "6": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["3", 0], "prompt": prompt,
                         "vae": ["4", 0], "image1": ["5", 0]}},
        "7": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["3", 0], "prompt": negative,
                         "vae": ["4", 0], "image1": ["5", 0]}},
        "8": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["4", 0]}},
        "9": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": sampler, "scheduler": scheduler,
                         "positive": ["6", 0], "negative": ["7", 0],
                         "latent_image": ["8", 0], "denoise": denoise}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["4", 0]}},
        "11": {"class_type": "SaveImage",
               "inputs": {"images": ["10", 0], "filename_prefix": prefix}},
    }


def sdxl_generate(prompt: str, *, negative: str = "",
                  width: int = 1024, height: int = 1024,
                  seed: int = 0, steps: int = 28, cfg: float = 6.0,
                  sampler: str = "euler", scheduler: str = "normal",
                  prefix: str = "sf_sprite",
                  lora_name: str | None = None, lora_strength: float = 0.8,
                  control_image: str | None = None,
                  control_type: str = "canny/lineart/anime_lineart/mlsd",
                  control_strength: float = 0.55, control_end: float = 0.85,
                  controlnet_name: str = "controlnet-union-sdxl-promax.safetensors",
                  canny_low: float = 0.4, canny_high: float = 0.8) -> dict:
    """Plain SDXL (Illustrious) txt2img, optionally with a style LoRA (LoraLoader) AND/OR
    a ControlNet (Union SDXL) structure hint: control_image (an uploaded reference) is
    run through ComfyUI's built-in Canny node and applied via ControlNetApplyAdvanced, so
    the new sprite follows the reference's pose/structure. Transparency = backend rembg
    matting. NOT LayerDiffuse (incoherent on Illustrious-XL)."""
    g = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": M["sdxl_checkpoint"]}},
    }
    model_ref, clip_ref = ["1", 0], ["1", 1]
    if lora_name:
        g["8"] = {"class_type": "LoraLoader",
                  "inputs": {"model": ["1", 0], "clip": ["1", 1], "lora_name": lora_name,
                             "strength_model": lora_strength, "strength_clip": lora_strength}}
        model_ref, clip_ref = ["8", 0], ["8", 1]
    g["2"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip_ref, "text": prompt}}
    g["3"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip_ref, "text": negative}}
    pos_ref, neg_ref = ["2", 0], ["3", 0]
    if control_image:
        g["9"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": controlnet_name}}
        g["10"] = {"class_type": "SetUnionControlNetType",
                   "inputs": {"control_net": ["9", 0], "type": control_type}}
        g["11"] = {"class_type": "LoadImage", "inputs": {"image": control_image}}
        g["12"] = {"class_type": "Canny",
                   "inputs": {"image": ["11", 0], "low_threshold": canny_low, "high_threshold": canny_high}}
        g["13"] = {"class_type": "ControlNetApplyAdvanced",
                   "inputs": {"positive": ["2", 0], "negative": ["3", 0], "control_net": ["10", 0],
                              "image": ["12", 0], "strength": control_strength,
                              "start_percent": 0.0, "end_percent": control_end, "vae": ["1", 2]}}
        pos_ref, neg_ref = ["13", 0], ["13", 1]
    g["4"] = {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}}
    g["5"] = {"class_type": "KSampler",
              "inputs": {"model": model_ref, "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": sampler, "scheduler": scheduler,
                         "positive": pos_ref, "negative": neg_ref,
                         "latent_image": ["4", 0], "denoise": 1.0}}
    g["6"] = {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}}
    g["7"] = {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": prefix}}
    return g


def sdxl_layerdiffuse_generate(prompt: str, *, negative: str = "",
                               width: int = 1024, height: int = 1024,
                               seed: int = 0, steps: int = 28, cfg: float = 6.0,
                               sampler: str = "euler", scheduler: str = "normal",
                               prefix: str = "sf_gen") -> dict:
    """SDXL (Illustrious) + LayerDiffuse -> native RGBA sprite (no chroma-key).

    The mandatory style phrase is injected by the caller (services.generate_sprite).
    NOTE: LayerDiffuse node IO is finalized against /object_info at first run; this
    uses LayeredDiffusionApply (sd_version=sdxl) + LayeredDiffusionDecodeRGBA.
    """
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": M["sdxl_checkpoint"]}},
        # NOTE (2026-06-17, Phase 4 UNRESOLVED): LayerDiffuse produces INCOHERENT output
        # on Illustrious-XL — "SDXL, Conv Injection" -> pure noise; "SDXL, Attention
        # Injection" -> broken tiled pattern. Plain SDXL (no LayerDiffuse) on the same
        # checkpoint is clean. Likely the LayerDiffuse SDXL layer weights don't transfer
        # to this heavily-finetuned checkpoint. Needs box R&D (a base/compatible SDXL
        # checkpoint, or weight verification). The Sprite tab stays DISABLED until fixed.
        "2": {"class_type": "LayeredDiffusionApply",
              "inputs": {"model": ["1", 0],
                         "config": "SDXL, Attention Injection",
                         "weight": 1.0}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": prompt}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": negative}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": sampler, "scheduler": scheduler,
                         "positive": ["3", 0], "negative": ["4", 0],
                         "latent_image": ["5", 0], "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        # Decode RGB + alpha SEPARATELY (LayeredDiffusionDecode), NOT the RGBA node:
        # LayeredDiffusionDecodeRGBA calls JoinImageWithAlpha().join_image_with_alpha()
        # which no longer exists in current ComfyUI -> AttributeError. We re-join in the
        # backend (audit.layerdiffuse_rgba) so the box's custom node stays untouched.
        "8": {"class_type": "LayeredDiffusionDecode",
              "inputs": {"samples": ["6", 0], "images": ["7", 0],
                         "sd_version": "SDXL", "sub_batch_size": 16}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": prefix + "_rgb"}},
        "10": {"class_type": "MaskToImage", "inputs": {"mask": ["8", 1]}},
        "11": {"class_type": "SaveImage",
               "inputs": {"images": ["10", 0], "filename_prefix": prefix + "_mask"}},
    }
