# 04 — Tool Surface（共有サービス層＝MCP ツール＝HTTP エンドポイント）

> **状態（2026-06-18）**: 実装済み＝services=MCP=HTTP の三位一体。**MCP は全15ツール**（gpu_status / list_sprites / list_loras / craft_prompt / generate_sprite / generate_variant / make_transparent / pixelize / fit_to_base / adopt / generate_character_bible / bible_status / train_style_lora / train_character_lora / train_status）で、初期化時に全体マニュアル（`MCP_INSTRUCTIONS`）を返す自己文書化済み。最新の一覧は `CLAUDE.md`。

> 各機能は **1つのサービス関数**として実装し、それを **MCP ツール**と **HTTP エンドポイント**の両方へ投影する（→ [02](02-architecture.md)）。瘢痕ルール（→ [00](00-context-and-pain.md)）はサービス層に内蔵し、UI/エージェントのどちらから来ても必ず通る。

## 共通の戻り規約
- すべて構造化 JSON を返す。生成物は `id`（キャッシュキー）＋ `GET /api/image/{id}` で取得可能。
- 失敗は**握りつぶさない**：`{ok:false, stage, error, artifact_path}` を返し、`.cache/errors.ndjson` に記録（rpgdev の no-silent-fallback 継承）。

## ツール一覧

### `gpu_status`
- 目的：ComfyUI 到達性／VRAM 使用・空き／モデル常駐（`nvidia-smi dmon` の rxpci でストリーミング有無）。
- 返り：`{comfy_up, vram_used, vram_free, resident:bool, rxpci_mbps}`。

### `generate_sprite`
- 目的：SDXL+LayerDiffuse(+画風/キャラ LoRA) で **RGBA スプライト**を固定キャンバスで**複数案バッチ**生成。
- params：`prompt, element?, pose?, canvas:[w,h], seed?, count=4, style_lora?, char_lora?`。
- 内部：必須語句（`retro RPG / pixel art / old JRPG sprite / limited palette / chunky pixel clusters`）を**自動注入**。seed は `seed+i` で振る。
- 返り：`{candidates:[{id, seed, w, h}]}`（RGBA, alpha ネイティブ）。

### `generate_variant`（=`damage_sprite`）★最重要・最初に作る
- 目的：ベース sprite ＋ **人手マスク** ＋ 指示で **Qwen-Image-Edit-2511 masked 編集**。**pose/canvas/alpha 保存**。
- params：`base_id|base_path, mask_id, instruction, controlnet?=openpose, count=4, cfg=1.0(固定)`。
- 内部：マスク外は不変。ControlNet で pose ロック。Lightning LoRA 時 cfg=1.0 強制。
- 返り：`{candidates:[...]}` ＋ 各候補に対し `fit_to_base` の事前監査結果を付す。

### `annotate`
- 目的：Konva 注釈 UI を開く URL を返し、送信を待って成果物を返す（→ [06](06-webui-ux.md)）。
- 返り：`{mask_id?, points?:[[x,y]...], split_line?:[[x,y]...], placement?:{dx,dy}}`。
- 補助：`points` を **SAM2** に渡して精密マスク化するモードあり（`mode:"sam2"`）。

### `recompose` / `split`
- 目的：分離線（注釈由来）で上下分割→配置オフセットで再合成（風スプライトの `/draw`+`/place` 相当を機械化）。
- params：`base_id, split_line, placement:{dx,dy}`。

### `make_transparent`
- 目的：LayerDiffuse 非経由素材の透過。**安全クロマ既定（`#ff00cc`／対象色と最遠を自動選択・黒禁止）→ alpha**、または ToonOut/BiRefNet matting。
- params：`image_id, method:"chroma"|"toonout"|"birefnet", chroma?`。
- 必ず**黒漏れ監査**を実行（下記 `fit_to_base` のサブ）。

### `pixelize`
- 目的：決定的 NEAREST 縮小 ＋ パレット posterize/quantize（再現可能）。
- params：`image_id, block≈2.4, posterize_step=28, palette?`。

### `fit_to_base`
- 目的：採用ゲートの計測部。bbox/height/canvas を base と機械比較＋**黒漏れ監査**（`alpha>24 & r,g,b<28` の画素数）＋compare-grid/checker 生成。
- 返り：`{canvas_match:bool, bbox_delta_px, black_leak_px, compare_id, checker_id, pass:bool}`。

### `adopt`
- 目的：候補を採用ゲートに通し（canvas 一致・黒漏れ=0・ダメージ版 pairing の bbox 差≤1.0px）、`rpgdev/public/assets/sprites/<name>.png` へ書き出し＋`.rpgdev/adoption.ndjson` 記録。
- params：`candidate_id, target_name, pair_with?(base for damaged)`。
- **ゲート不通過は採用しない**（理由を明示）。

### `train_style_lora` / `train_character_lora`（Phase 5）
- 目的：5090 上で参照セットから LoRA 学習（画風＝既存キャラ群、キャラ別）。
- params：`name, images:[...], steps≈3000`。

### `compare_grid` / `inspect`
- 目的：view 用の比較・checker 合成（候補の並べ表示、採用前確認）。

## ゲートの不変条件（必守）
1. 透過アセットは **採用前に黒漏れ監査 = 0**。
2. ダメージ版は base と **canvas 完全一致・bbox 差 ≤ 1.0px**。
3. 生成プロンプトに**必須語句**が必ず含まれる。
4. 手描きペイント機能は**持たない**（指し示す＝マスク/点/線のみ。修正は再生成/inpaint）。
