# 03 — Models and Runtime（実機事実・VRAM・ランタイム判定）

> **状態（2026-06-18）**: 実機確定。素体＝Illustrious-XL-v2.0 txt2img＋**BiRefNet** matte、編集/バイブル＝Qwen-Image-Edit-2511 fp8mixed（~32GB常駐）、構造誘導＝ControlNet-Union-SDXL(promax)、LoRA＝Lightning(4steps)/画風`sprite-style-v2`/キャラLoRA群。torch2.12+cu130 / RTX5090 sm_120。最新は `CLAUDE.md`。モデルの入手先・配置は `models.md`。

## 実機（点検済み 2026-06-17）

- ホスト `<GPU_HOST>`（Win11 + WSL2）。SSH = `<user>@<GPU_HOST>`（公開鍵認証可、着地は Windows PowerShell・Shift-JIS）。
- GPU = **NVIDIA GeForce RTX 5090 / VRAM 32GB / CUDA 13.x / WDDM**。
- WSL2 からも `nvidia-smi -L` で 5090 を認識＝**GPU パススルー可**（参考。実運用は Windows ネイティブ）。
- 初期状態は ComfyUI 未導入（後述の手順で導入する）。

## モデル一式（採用）

| 役割 | モデル | 形式/重量目安 |
|---|---|---|
| ベース生成 | Illustrious XL v2（または Pony V6） | SDXL fp16 UNet ~5GB |
| 透過 | LayerDiffuse（SDXL transparent VAE/decoder） | 小 |
| matting フォールバック | ToonOut / BiRefNet | 小〜中 |
| 差分編集（最重要） | **Qwen-Image-Edit-2511** DiT | Q6_K GGUF=15.7GB / nunchaku NVFP4 r128=13.6–14.6GB / r32=11.95GB |
| 編集 TE | Qwen2.5-VL-7B | fp8=8.74GB |
| 編集 対抗 | Flux Kontext（A/B 用） | （別途） |
| pose 固定 | ControlNet（openpose / lineart, SDXL） | ~2.5GB/各 |
| ピクセル | Pixel Art XL LoRA（nerijs 等） | 小 |
| LoRA 学習 | kohya（画風＋キャラ） | — |

## VRAM 収まり試算（実測ベース＝最重要）

**結論：32GB に常駐で収まり、余裕あり。** 16GB 破綻の本質は「DiT＋TE が同時に縁を超えた」こと。ComfyUI は fp8 TE で符号化→**DiT ロード前に TE を退避**するので、サンプリング中の常駐は基本「DiT 側のみ」（実測：fp8 TE は clean に退避、r32 で rxpci 16–18MB/s＝ストリーミング消失）。

| ワークロード | サンプリング中の常駐 | 32GB 判定 |
|---|---|---|
| Qwen-Image-Edit best_quality（Q6_K 15.7GB） | DiT 15.7 + VAE 0.3 + working ~2 + ctx 1.5 ≈ **~19.5GB**（TE非退避でも +8.74 ≈ ~26GB で収まる） | ◎ |
| 同 fp8 DiT（~20GB・更に高品質寄り） | ≈ **~24GB** | ○（16GB 機が強いられた r32 妥協が不要に） |
| SDXL 生成＋LayerDiffuse＋ControlNet×1–2 | ≈ **~12–17GB** | ◎ |
| SDXL LoRA 学習（kohya, 8bit Adam, 1024²） | ≈ **~12–18GB** | ○ ローカル学習可 |

**収まらないのは Qwen full bf16 DiT(~40GB) だけ** → DiT は fp8/Q6_K 量子化を採用（品質劣化ではなく、16GB 機の r32 妥協の回避）。

## 運用ルール（実測知見由来・必守）

- ComfyUI は **NSSM で headless 自動起動**（未ログイン）＝Windows dwm の ~2GB VRAM を解放（idle 空き 13.5→15.5GB を 16GB 機で確認済み。32GB でも有効）。
- **1ワークフローずつ直列**実行。SDXL・Qwen-Edit・ControlNet を全部同時常駐させない（ComfyUI smart-memory に任せる）。
- Lightning LoRA は **cfg=1.0 を強制**（cfg>1 は CFG 二重 forward で working set 倍化→thrash。pipeline 側でクランプ）。
- `--reserve-vram` は offload を増やす逆効果なので**使わない**。
- 採用前に **`nvidia-smi dmon -s ut` の rxpci を確認**（低い＝常駐／~50GB/s＝PCIe ストリーミング失敗）。これが「収まった」の唯一の正しい判定。

## ランタイム判定：Windows ネイティブ ComfyUI（推奨）

- 研究＝native 強推奨（NTFS I/O・SageAttention 2.x 既製 wheel・custom node コンパイル容易）。
- 実測知見の実績＝この box の **Windows portable ComfyUI で nunchaku Qwen-Image-Edit が稼働済み**（NSSM headless 含む）。
- WSL2 は GPU パススルー自体は動くが、安全機構の ext4↔NTFS 越し safetensors I/O 税で「可だが非推奨」。
- **唯一の要確認点**：最終判断は実機で I/O・ノード整備性を見て確定（既定は native）。

## RTX 5090（Blackwell sm_120）対応メモ

- PyTorch は CUDA 12.8+/cu130 系（Blackwell 対応ビルド）を使う。
- attention は PyTorch SDPA / SageAttention（Blackwell 対応版）。導入可否は実機で確認（→ [07](07-open-questions-validate-on-box.md)）。
