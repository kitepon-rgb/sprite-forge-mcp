# CLAUDE.md — sprite-forge-mcp

rpgdev 専用のローカルGPU画像生成スタジオ。**1つの Python バックエンド＝2つの顔**（人間 WebUI [FastAPI] ＋ エージェント MCP [FastMCP]）。生成計算は別マシンの ComfyUI。詳細設計は `docs/00`〜`07`。

## アーキテクチャ（実装の現実）
- **バックエンドは Mac で動かす**（rpgdev も Claude/Codex も Mac。採用＝rpgdev へ書き出しがローカル完結）。`docs/02` の図は box と書くが実装は Mac。
- **ComfyUI は GPU機** で headless 稼働（`http://<GPU_HOST>:8188`、NSSMサービス名 `ComfyUI`、RTX5090/torch2.12+cu130）。Mac のバックエンドが LAN 越しに HTTP+WS で駆動。
- SSH = `<user>@<GPU_HOST>`（鍵認証・PowerShell着地・管理者）。
- 1 uvicorn プロセスに FastAPI（`/api/*`）と FastMCP（`/mcp`）を同居。両者は**同じ `backend/services.py`** を呼ぶ（ロジック二重化なし・ゲート回避不可）。

## 起動
```bash
cd ~/Developer/sprite-forge-mcp
python3.12 -m venv .venv && . .venv/bin/activate   # 初回のみ。Python 3.11+ 必須(fastmcp)
pip install -r requirements.txt                     # 初回のみ
uvicorn backend.app:app --host 127.0.0.1 --port 8765
```
- WebUI: ブラウザで `http://127.0.0.1:8765/`（**ステップ導線** ①素体生成→②キャラバイブル→③キャラLoRA→④活用/共有＋副モード 編集/バリアント・設定/LoRA。AIプロンプト生成パネル・チェッカー背景ギャラリー・統一ジョブドック・採用モーダル）。
- 到達確認: `curl http://<GPU_HOST>:8188/system_stats`（GPU機）/ `curl localhost:8765/api/gpu`（backend）。
- MCP は `.mcp.json`（URL型 `http://127.0.0.1:8765/mcp/`）で登録済み。**uvicorn 起動中**でのみ使える。
- SAM2 自動マスク（任意の隔離コンポーネント）: `python3.12 -m venv .venv-sam2 && .venv-sam2/bin/pip install ultralytics`（torch CPU・初回 `sam2_t.pt` 自動DL）。未導入でも手描きマスクは使える。box には何も入れない。

## 必守の瘢痕ルール（過去の痛点をツールに焼いたもの。緩めない）
- **透過（痛点#1）**: 出力は RGBA・四隅 alpha=0。**背景は黒禁止**。編集モデル(Qwen-Edit)経路は「キャラ色から最遠の識別色を自動選択(既定マゼンタ)→単色背景を維持させて生成→その色を色抜き→監査」。near-black はキャラの暗色アウトライン（正当）であり黒漏れではない＝**black-leak==0 ゲートは廃止**、四隅透過＋識別色コンプライアンスで判定。
- **ダメージ版 pose/bbox（痛点#2・最難関）**: base と canvas 完全一致・bbox 中心差 **≤1.0px**（厳格・緩めない）。`denoise=0.7`（既定 `config.VARIANT_DENOISE`）でベースのポーズを保つ（1.0は再ポーズで破綻）。**完全保証**が要るときは**衣装マスク**を渡す＝マスク外はベースのピクセルをそのまま合成（`audit.composite_inside_mask`）で bbox 0px。
- **画風（痛点#3）**: 生成プロンプトに必須語句 `retro RPG / pixel art / old JRPG sprite / limited palette / chunky pixel clusters` を自動注入（`config.STYLE_PHRASE`、`generate_sprite` で強制）。太い暗色アウトライン維持。
- **手作業禁止（痛点#4）**: 手描きペイント機能は持たない。指し示す＝マスク/点/線のみ。修正は再生成/inpaint。
- **採用＝rpgdev へ書き出し**: `~/Developer/rpgdev/public/assets/sprites/<name>.png` ＋ `.rpgdev/adoption.ndjson` 記録。採用ゲート不通過は書き出さない（理由明示）。**採用は明示指示があるときだけ**（既存アセットの上書き＝不可逆）。

## ソース構成
- `backend/config.py` 接続/閾値/モデル名/識別色パレット/`VARIANT_DENOISE`。
- `backend/comfy_client.py` 非同期 ComfyUI クライアント（WS進捗・submit/wait/run/upload/get_image）。完了判定は `execution_success` ＋ `executing node==null`、history は outputs が出るまでポーリング（取得レース対策）。
- `backend/workflows.py` `qwen_edit_variant`（実証済み）/ `sdxl_generate`（素SDXL txt2img＝Sprite経路で使用）/ `sdxl_layerdiffuse_generate`（廃止・Illustrious非互換、参考保持）。
- `backend/audit.py` 色自動選択/色抜き/マスク合成/監査ゲート/checker。決定的処理のみ。
- `backend/services.py` gpu_status / generate_variant / generate_sprite / make_transparent / pixelize / fit_to_base / adopt。瘢痕ゲート内蔵。
- `backend/training.py` **LoRA学習オーケストレータ**：`train_style_lora`（画風）＋`start_character`/`train_character_lora`（**バイブルのパネル→キャラLoRA**、ComfyUI停止でクリーンGPU→学習→再開→配置）。データセット構築→box scp→ssh で sd-scripts 学習→ComfyUI配置→実行時アクティブ化。WebUIボタン/MCP/`/api/train_lora`・`/api/train_character`。**手作業box操作ゼロ**（バックエンドが ssh。エージェントだけがサンドボックス）。
- `backend/bible.py` **キャラバイブル生成**（`generate_character_bible`、**マスターシート参照方式**）：①Qwen一発で“詰め込みマスターシート”を生成（全ビュー一括描画＝一貫性アンカー・後ろ姿も正しく向く）→②**それを参照に**23パネル（ターンアラウンド/体型ref/表情/アクション/別衣装/ちび/装備）を**個別高解像でon-model生成**→③Pillowで整列レイアウト合成（先頭にマスター）＋個別パネル保存(=キャラLoRA教材)。WebUIボタン/MCP/`/api/bible`。「N回独立編集より1回協調生成」の方が一貫＝この設計の核。完了時に**自己完結HTMLバイブル**（base64・`/api/bible_html/<name>`・WebUIにリンク）も自動生成。**claude.ai/design 共有**＝DesignSync(`/design-sync`)でHTMLを `@dsCard` 付きでpush（agentのclaude.aiログイン要・backend機能ではない。projectId は design 上）。**入力は rpgdev スプライト名 OR 生成候補ID**（`bible._job` が `services.load_candidate` で解決）＝**「Sprite新規生成で素体を複数作る→ギャラリーで選ぶ→カードの『この素体でバイブル』」**で完全新規キャラもWebで設定資料化（D完了）。
- `backend/app.py` FastAPI REST ＋ FastMCP(/mcp、lifespanネスト) ＋ 静的 `web/` 配信（"/" は最後にマウントし /mcp を覆わない）。**MCPは外部AI向けに自己文書化**：`FastMCP(instructions=MCP_INSTRUCTIONS)` で初期化時に全体マニュアル（推奨パイプライン craft→generate→bible→train→adopt・用語・瘢痕ルール）を返す。全15ツール（discovery `list_sprites`/`list_loras` 含む＝外部AIが有効なbase名/LoRA名を取得可能）。
- `web/` vanilla ESM の**ステップ導線WebUI**（ビルド工程なし）：`index.html`(shell：topbar/stepper/#stage/#jobs-dock)＋`main.js`(hash router＋GPU/SSE/lists)＋`api.js`/`state.js`(observable store)/`ui.js`(card/badge/modal/toast)/`jobs.js`(統一ジョブドック＋SSE)＋features `gen.js`(①素体生成/④活用＋**AIプロンプト生成パネル**)/`bible.js`(②)/`lora.js`(③＋設定/LoRA)/`variant.js`(編集/バリアント＋マスク)。`style.css` はデザインシステム(トークン＋component class)。`design/` は claude.ai/design 用ショーケース。導線＝①素体→②バイブル→③キャラLoRA→④活用/共有。
- `backend/promptcraft.py` **AIプロンプト生成**：ユーザー自身の `claude -p`(or `codex`)を **cwd=プロジェクト**でサブプロセス実行→ラフ発想を英語タグ詳細プロンプトに展開（Anthropic API不使用＝追加課金なし・プロジェクトCLAUDE.md文脈付き）。`/api/craft_prompt`＋MCP `craft_prompt`。`config.PROMPT_CLI/MODEL/PROJECT_DIR`。

## 現状（2026-06-18 セッション後）
**キャラ制作パイプラインが一周完成**：テキスト → ①素体生成 → ②キャラバイブル → ③キャラLoRA → ④任意ポーズ量産 / 採用 / 共有。人間＝WebUI、AI＝MCP の二つの顔が同一バックエンドで稼働。
- ✅ **基盤**: E2E（Mac→GPU→生成→透過→監査）/ 透過の識別色方式 / ダメージ版 pose-lock（denoise0.7+マスク保証）/ SSE進捗 / SAM2マスク（隔離`.venv-sam2`）。
- ✅ **Sprite新規生成（素体）**: 素SDXL(Illustrious)+matte。matteは **BiRefNet**（`config.MATTE_MODEL`＝淡色キャラの手/毛先の削れ対策）。クリーン背景強制で透過安定。LayerDiffuse廃止。
- ✅ **画風LoRA v2 既定**（`sprite-style-v2`/`sfrpg`/強度0.65・自動学習）＋**ControlNet Union**（`control_base`＝参照ポーズ）。
- ✅ **キャラバイブル**（`bible.py`・マスターシート参照方式）＝多角度ターンアラウンド＋表情＋アクション＋別衣装＋体型ref＋ちび＋装備＋配色を一貫生成→整列シート＋**自己完結HTML**＋**claude.ai/design 共有**。入力は生成候補ID or 既存スプライト名。
- ✅ **キャラLoRA**（`train_character_lora`＝バイブルのパネルで1クリック学習）→ `generate_sprite(lora_name=,lora_trigger=)` で任意ポーズ量産。エンジン＝Qwen-edit reposing（IPAdapterはIllustriousで破綻＝不採用）。
- ✅ **AIプロンプト生成**（`promptcraft.py`）＝ラフ発想→`claude -p`(Opus)/`codex`を cwd=プロジェクトで実行→詳細プロンプト（追加課金なし）。
- ✅ **WebUI全面刷新**（ステップ導線①〜④＋編集/バリアント・設定/LoRA、vanilla ESMモジュール、デザインシステム）。E2E検証済み。
- ✅ **MCP自己文書化**：`FastMCP(instructions=…)`＋discovery `list_sprites`/`list_loras`＝全15ツール、文脈ゼロの外部AIでも使える。
- ⚠️ **box学習の前提＝TdrDelay=60**（長時間GPU学習のWDDM TDRフリーズ対策・再起動済み）。
- 既知の残課題: 淡色（水）キャラの**生成物透過抽出**の詰め / pixel LoRA（posterizeで概ね代替済み）/ v2画風LoRAの過学習軽減（教材増）。

## 規約メモ
- git は `git init` 済み・コミットは依頼があってから（rpgdev 規約準拠）。
- `.cache/` は作業物（候補PNG・ログ・検証スクリプト）。gitignore 済み。判断が要る所はユーザーに聞く。
