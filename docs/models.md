# Models manifest（GPU box の ComfyUI に置くモデル）

sprite-forge 本体は**推論を持たない薄いオーケストレータ**で、生成計算はすべて GPU box の ComfyUI が行う。下記モデルを **ComfyUI の `models/<サブフォルダ>/` に正確なファイル名で**置くこと（パイプラインはこの名前を参照する）。

> このリストは作者の box で実際にダウンロードして動作確認した記録（`docs/07`）。sha256 は同梱しない（巨大ファイルを手元に持たないため）— **各 HuggingFace リポの配布物をそのまま取得**すれば整合性はソース側で担保される。バージョン/可用性は導入時に各リポで確認すること。

## 必須モデル

| 役割 | 置き場所（ComfyUI `models/…`） | ファイル名 | 概算 | 入手元（HuggingFace） |
|---|---|---|---|---|
| ベース生成(SDXL) | `checkpoints/` | `Illustrious-XL-v2.0.safetensors` | 6.94 GB | `OnomaAIResearch/Illustrious-XL-v2.0` |
| 編集/バイブル DiT | `diffusion_models/` | `qwen_image_edit_2511_fp8mixed.safetensors` | 20.5 GB | `Comfy-Org/Qwen-Image-Edit_ComfyUI` → `split_files/diffusion_models` |
| Qwen テキストエンコーダ | `text_encoders/` | `qwen_2.5_vl_7b_fp8_scaled.safetensors` | 9.38 GB | `Comfy-Org/Qwen-Image_ComfyUI` → `split_files/text_encoders` |
| Qwen VAE | `vae/` | `qwen_image_vae.safetensors` | 242 MB | `Comfy-Org/Qwen-Image_ComfyUI` → `split_files/vae` |
| Lightning 4-step LoRA | `loras/` | `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | 0.85 GB | `lightx2v`（Qwen-Image-Edit Lightning） |
| ControlNet(参照ポーズ) | `controlnet/` | `controlnet-union-sdxl-promax.safetensors` | ~2.5 GB | `xinsir/controlnet-union-sdxl-1.0`（promax） |

ファイル名やサブフォルダを変えたい場合は env で上書き可（`SPRITEFORGE_BOX_SDXL_CKPT` ほか、`backend/config.py` 参照）。ControlNet 名は `backend/workflows.py` の `controlnet_name` 既定。

## 自動DL（手動不要）
- **matte モデル `birefnet-general`** … Mac 側 backend の `rembg` が初回に自動取得（`config.MATTE_MODEL`）。手動DL不要。
- **SAM2 `sam2_t.pt`** … 任意機能。`.venv-sam2` 導入時に `ultralytics` が初回自動取得。

## 必須 ComfyUI ノード（カスタムノード/バージョン）
標準ノード（`CheckpointLoaderSimple`/`KSampler`/`VAE*`/`CLIP*`/`LoraLoader`/`EmptyLatentImage`/`Canny`/`ControlNetLoader`/`ControlNetApplyAdvanced`/`SaveImage`/`LoadImage`）に加え、**以下のサポートが要る**：
- **Qwen-Image-Edit ノード**：`TextEncodeQwenImageEditPlus`、`UNETLoader`/`CLIPLoader`/`VAELoader`（`qwen_image` 系）。編集・バイブル経路に必須。
- **Union ControlNet**：`SetUnionControlNetType`（promax の type 切替）。参照ポーズ経路に必須。
- IO は **ComfyUI 0.25.0** で検証済み。古すぎる ComfyUI はワークフローを弾く。
- `LayeredDiffusionApply`/`LayeredDiffusionDecode`（ComfyUI-layerdiffuse）は **廃止経路でのみ使用**＝通常は不要（Illustrious 非互換で不採用、参考保持）。

> 起動前チェック：ComfyUI の `/object_info` に上記 class_type が在るか確認すると、ノード不足を早期に発見できる。

## VRAM の床（重要）
DiT 20.5GB + TE/VAE + working で **~24–27GB 常駐**（編集経路）。**~30GB 空きの GPU（RTX 5090 級 32GB）を想定**。小容量GPUでは収まらない（小型 checkpoint で下げる余地はあるが、本パイプラインのプロンプト/denoise はこのモデル一式に合わせてある）。
