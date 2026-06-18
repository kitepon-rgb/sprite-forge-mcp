# 07 — Open Questions / Validate on Box（実機で実証する項目）

> **状態（2026-06-18）**: 主要な実証項目は解決済み＝Qwen-Edit-2511 の~32GB常駐／識別色方式の透過／ダメージ版 pose-lock（denoise0.7＋マスク保証）／LayerDiffuse は Illustrious 非互換と判定し廃止／LoRA学習（画風v2・キャラ）／ControlNet-Union。本書は実証履歴。残課題は `CLAUDE.md`「現状」を参照。

> 実測知見の規律（「約10構成を試して初めて分かった」）に倣い、**設計の前提を実機 <GPU_HOST> で必ず実証**する。推測で確定しない。各項目は Phase 1〜2 で検証し結果をここへ追記する。

## ランタイム
- [ ] Windows ネイティブ ComfyUI を導入し起動（NSSM headless）。`0.0.0.0:8188` で LAN 到達。
- [ ] RTX5090(Blackwell sm_120) で PyTorch cu128 系が動く（attention：SDPA / SageAttention の可否）。
- [ ] **Windows-native vs WSL2 の最終判定**：同一モデルで safetensors ロード時間・サンプリング速度を実測比較（既定 native）。

## VRAM 常駐（最重要・実測知見の直系）
- [ ] Qwen-Image-Edit-2511 **best_quality（Q6_K or fp8）が 32GB に常駐**：`nvidia-smi dmon -s ut` の rxpci が低い（~数十MB/s）＝ストリーミング無を確認。
- [ ] masked-crop 編集の所要時間（5080 r32 で 28s だった。32GB best_quality で実測）。
- [ ] TE(fp8 8.74GB) が DiT ロード前に退避されることを VRAM 推移で確認。
- [ ] SDXL+LayerDiffuse+ControlNet×2 同時の VRAM。LoRA 学習時の VRAM。
- [ ] headless（未ログイン）で dwm ~2GB が解放されるか。

## 透過（痛点 #1）
- [ ] LayerDiffuse(SDXL) のこの画風での RGBA 品質（髪/布/細線の alpha）。
- [ ] ToonOut / BiRefNet matting の実地品質（フォールバック）。
- [ ] 黒漏れ監査が LayerDiffuse 出力で 0 になるか。

## 差分編集（痛点 #2）
- [ ] Qwen-Image-Edit-2511 masked 編集が **pose/canvas/alpha を保存**するか（bbox 差≤1.0px を満たすか）。
- [ ] ControlNet(openpose/lineart) で pose 固定が効くか。
- [ ] **Qwen-Image-Edit vs Flux Kontext** の A/B（同一指示で品質・保存性比較）。
- [ ] Lightning LoRA 時 cfg=1.0 クランプの効果。

## 画風/ピクセル（痛点 #3/#5）
- [ ] pixel LoRA が既存キャラ群の粒度に合うか（合わなければ posterize 主体）。
- [ ] 画風 LoRA（既存キャラ群で学習）でドリフトが止まるか。
- [ ] キャラ LoRA で同一キャラを変種間で保てるか。

## WebUI / バックエンド
- [ ] FastAPI + FastMCP 同居（単一 uvicorn）で /api と /mcp が両立。
- [ ] ComfyUI WS の進捗を SSE でブラウザにストリームできる（候補ごと）。
- [ ] vanilla ESM + Konva の注釈（ブラシ/赤ドット/分離線/配置）＋SSE ギャラリーの操作感。
- [ ] SAM2 点→マスクの実用性（box に SAM2 を入れるか、ComfyUI ノードで賄うか）。
- [ ] Claude/Codex から `http://<GPU_HOST>:<PORT>/mcp` を URL 型 MCP として登録できる。

## 結果ログ（追記用）

### Phase 1 実機結果（2026-06-17・Windows ネイティブ）
ComfyUI 稼働まで検証済み。再現手順：

- OS: Windows 11 Pro / C: 空き159GB / Python 3.12.10(native) / git / nssm 既設。SSH `<user>@<GPU_HOST>` は**管理者実効権限あり**。
- ComfyUI: `git clone` → `C:\Users\<boxuser>\ComfyUI`、`py -3.12 -m venv .venv`。
- **PyTorch = `torch 2.12.0+cu130`（`--index-url https://download.pytorch.org/whl/cu130`）。Blackwell sm_120 実機OK**（`cuda.get_device_capability=(12,0)`、cuda matmul 成功）。cu128 でなく **cu130 で stable wheel が存在**（torchvision 0.27.0+cu130 / torchaudio 2.11.0+cu130）。
- ComfyUI 起動ログで **NVFP4/SVDQuant カーネルがネイティブ**（`comfy_kitchen` backend: `quantize_nvfp4`/`scaled_mm_nvfp4`/`quantize_svdquant_w4a4`/fp8）。triton は未導入（SageAttention 入れる場合は別途）。
- **headless NSSM サービス（LocalSystem/session0）で起動**＝idle VRAM 使用 ~1.6GB・**空き 30.9GB**（dwm 不在の 実測知見の恩恵を 32GB でも確認）。ComfyUI 0.25.0。
- Firewall: 8188（ComfyUI）/ 8765（backend 予約）を inbound 許可（`netsh advfirewall`）。Mac から `http://<GPU_HOST>:8188/system_stats` 到達OK。
- カスタムノード: ComfyUI-Manager ＋ ComfyUI-layerdiffuse（`LayeredDiffusionApply` 等 読込確認、`EmptyQwenImageLayeredLatentImage` も存在）。
- モデル（curl.exe -L で各 `models/<subdir>` へ、匿名DL可）：
  - `checkpoints/Illustrious-XL-v2.0.safetensors` 6.94GB（OnomaAIResearch/Illustrious-XL-v2.0）
  - `diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors` 20.5GB（Comfy-Org/Qwen-Image-Edit_ComfyUI/split_files/diffusion_models）
  - `text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` 9.38GB（Comfy-Org/Qwen-Image_ComfyUI/split_files/text_encoders。NVFP4版 TE `qwen_2.5_vl_7b_nvfp4` も同repoに在り＝将来最適化候補）
  - `vae/qwen_image_vae.safetensors` 242MB
  - `loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` 0.85GB（lightx2v）

### Phase 1c 結果：Qwen-Image-Edit-2511 fp8mixed の 32GB 常駐＝実証済み（2026-06-17）★最重要
あるベーススプライト(1280×1280) を「衣装が戦闘ダメージで破れる」指示で編集（4-step Lightning, cfg=1.0, euler/simple, denoise=1.0, VAEEncode→KSampler）。ComfyUI API 経由。

- **速度**：コールド（モデルロード込み）**12.4s** / ウォーム（常駐）**6.2s**。5080 16GB の masked-crop 638s〜full 21分から**約50〜100倍速**。
- **常駐**：サンプリング中 **sm=99%（計算律速）なのに rxpci ピーク 455 MB/s**（典型 178〜234）。5080 のストリーミング 30,000〜62,000 MB/s の**約100分の1**＝**重みストリーミング皆無＝完全常駐**。
- **VRAM**：実行後 used ≈ **27.2GB / free 5.4GB**（DiT 20.5＋TE/VAE/working が 32GB に収まる）。
- **キャンバス**：出力 1280×1280＝入力と完全一致（#2 のキャンバス保存 OK）。
- **キャラ/ポーズ/identity**：保持（炎の髪・扇・リボン・ブーツ）。ただしやや正面寄りに回転する傾向→ **ControlNet(openpose/lineart) で pose ロック**＋編集強度の調整で抑える（Phase 1d/2）。
- **透過（重要な再現）**：出力は**黒背景の RGB（alpha 喪失）**。Qwen-Edit は RGB モデルゆえ透過が消える＝痛点 #1 が出る。→ **編集後に必ず matting/透過＋黒漏れ監査ゲートを通す**設計が正しいことを実機が裏付け。元の alpha を base から復元 or matting で再付与する。

結論：**5090 32GB 換装は当初目的（Qwen-Edit を常駐で高速編集）を達成**。5080 16GB 級の「~1分常駐は 24GB+ 必須」という制約を満たした。

### 未実施（次の検証）
- [ ] 編集後 matting/透過の実地品質（ToonOut/BiRefNet で黒背景→alpha、黒漏れ監査=0）。
- [ ] ControlNet(openpose/lineart) で pose ロック（回転抑制・bbox 差≤1.0px）。
- [ ] LayerDiffuse SDXL 重み投入 → Illustrious で RGBA ネイティブ生成の品質。
- [ ] pixel LoRA / posterize で既存キャラ群の粒度合わせ。
