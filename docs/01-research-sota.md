# 01 — Research / SOTA（2026 現行ソース調査の結論）

> **状態（2026-06-18）**: 調査に基づく採用は実装で確定＝Illustrious-XL-v2.0（素体）/ Qwen-Image-Edit-2511 fp8mixed（編集・バイブル）/ BiRefNet（matte）/ ControlNet-Union-SDXL / kohya sd-scripts（LoRA学習）。**LayerDiffuse は Illustrious-XL 非互換で廃止**。本書は調査記録、最新の実機事実は `docs/03`・`CLAUDE.md`。

> 「既存知識で断定しない・最新の根拠を確認する」原則に従い、2026-06 時点の現行ソースで調査した結論。各観点に出典 URL。判断は rpgdev の痛点（→ [00](00-context-and-pain.md)）に紐づく。

## A. 生成パイプライン（7観点）

### A1. ベース生成モデル → **SDXL 系（Illustrious XL v2 / Pony V6）**
- 理由：アニメ/イラスト品質に最適化、**LoRA/ControlNet/IP-Adapter/LayerDiffuse の生態系で圧倒**。5090 で LoRA 学習が ~3000 step（20枚で 1–2h）。
- 却下：Flux.1/Flux.2/SD3.5 は ControlNet 未成熟・負プロンプト非対応（flow matching）・SDXL LoRA 資産と非互換。
- 出典例：toolhalla「ComfyUI vs InvokeAI vs Fooocus 2026」、Civitai/Illustrious・Pony 各ページ。

### A2. キャラ同一性・画風統一 → **画風 LoRA（既存キャラ群で学習）＋ キャラ別 LoRA**（主）、**IP-Adapter FaceID + PuLID**（新キャラ即席・学習不要、従）
- 画風 LoRA がドリフト #3 を抑え、キャラ LoRA が同一性 #5 を固定。ダメージ再生成中もキャラ LoRA を効かせて pose ズレを抑制。

### A3. 差分＝ダメージ版（最難関 #2）→ **Qwen-Image-Edit-2511 の masked/regional 編集 ＋ ControlNet pose ロック**
- 編集はマスク領域だけを変え **pose/canvas/alpha を保存**。本人が「キャラ/ポーズ/絵柄維持」を実機実証（実測知見）。研究も Qwen-Edit を high 評価。
- 対抗：**Flux Kontext**（local 編集 semantics）を A/B 比較対象に残す。
- 補助：ControlNet（openpose/lineart）で骨格固定、低 denoise img2img、固定 seed。

### A4. 透過（最大痛点 #1）→ **LayerDiffuse ネイティブ RGBA（SDXL）＋ ToonOut/BiRefNet matting フォールバック**
- 生成時点で RGBA＝クロマキー黒漏れを**構造的に根絶**。ComfyUI に `LayeredDiffusionDecodeRGBA` 系ノードあり。
- ToonOut（2025/9・アニメ 1,228枚で 99.5% px 精度、BiRefNet 95.3% を上回る）が髪/布/細線に強い。LayerDiffuse-Flux は dev-only で不安定＝不採用。
- 出典例：runware「LayerDiffuse」、ToonOut 発表（2025-09）。

### A5. ピクセル/レトロ → **決定的 NEAREST 縮小 ＋ パレット posterize/quantize**（既存 rpgdev 手順）＋ pixel LoRA（nerijs Pixel Art XL 等）
- 既存キャラ群のドット粒度に再現的に寄せる。seed 固定で主観の版爆発 #6 を抑制。Retro Diffusion/PixelLab は API/local 制約で従位。

### A6. ランタイム → **Windows ネイティブ ComfyUI（headless）**（→ 詳細は [03](03-models-and-runtime.md)）
- 研究＝native 強推奨（NTFS I/O・SageAttention 既製 wheel）。実測知見の実績＝この box の Windows portable ComfyUI で nunchaku Qwen-Edit 稼働済み。WSL2 は ext4 越し I/O 税で「可だが非推奨」。

### A7. 代替（クラウド特化）→ **却下、ローカル ComfyUI が最強**
- Scenario/PixelLab/Layer は **bbox 一致(#2)・透過漏れ防止(#1) を構造的に解けず**、クラウドで制御弱。ControlNet OpenPose が唯一の bbox 保存機構。

## B. WebUI（3観点）

### B1. 既存フロント採用候補
- **SwarmUI**（MIT, ComfyUI backend）：バッチ/グリッド比較は最強。だが**ブラシマスク/点・線注釈/採用ゲート/MCP は無く自作必須**、拡張は **C#/.NET** 畑違い。
- **InvokeAI**（Apache2, 独自 backend）：Unified Canvas のブラシ編集 UX は秀逸。だが**バッチ比較グリッド無し**・ノード生態系小・MCP 無し。
- **A1111/Forge**：X/Y/Z プロット比較・inpaint canvas はあるが開発停滞（Forge は 2026-03 以降公開更新無）・非 ComfyUI。

### B2. 自作スタック（採用）
- 注釈 canvas：**Konva.js**（scene-graph・ヒット判定・Transformer・ドラッグ・点/線/ブラシが primitive。tldraw=商用ライセンス制限・whiteboard 向き、Fabric=毎オブジェクト再描画で重い、excalidraw=手描き風で pixel 不向き、で却下）。
- ギャラリー/進捗：素の JS グリッド＋**SSE（EventSource）**で候補ごとライブ進捗（WebSocket より単純）。
- バックエンド：**FastAPI（人間UI）＋ FastMCP（エージェント）同居**、共有 ComfyUI クライアント（singleton/lifespan）、`asyncio.Queue` でジョブ直列化。これが 2025-2026 の定石。
- 出典例：gofastmcp（FastAPI 統合）、Konva 比較記事、MUI/Tanstack（※今回は vanilla 採用のため参考）。

### B3. 採用 vs フォーク vs 自作 → **自作の薄い WebUI＋MCP over headless ComfyUI**
- rpgdev 専用 UX（採用ゲート・ダメージ版 pairing・赤ドット/分離線・1バックエンド2フェイス）は generic を拡張しても結局自作部分が大半。**ComfyUI は基盤、UI は内部詳細を隠す薄い自作層**が理想。フロントは **vanilla ESM＋Konva**（rpgdev の no-build 思想踏襲）。

## 主要出典（抜粋）
- 生成：toolhalla 2026 比較、runware LayerDiffuse、nextdiffusion「Qwen-Image-Edit-2509 + ControlNet」、ToonOut 発表、Illustrious/Pony（Civitai）。
- WebUI：github mcmonkeyprojects/SwarmUI、invoke.ai/releases、gofastmcp.com、konvajs.org、artokun/comfyui-mcp。
- 実機：5080 16GB では Qwen-Edit を常駐できないという実測（5090 換装の根拠）。
