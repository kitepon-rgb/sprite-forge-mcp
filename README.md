# sprite-forge-mcp

rpgdev 専用のローカル GPU 画像生成スタジオ。RTX 5090 + ComfyUI を土台に、**1つの Python バックエンドが2つの顔**を持つ：

- **人間向け WebUI**（FastAPI/SSE）：ステップ導線で ①素体生成 → ②キャラバイブル → ③キャラLoRA → ④活用/共有 を迷わず辿れる。
- **エージェント向け MCP**（FastMCP）：Claude/Codex が同一パイプラインを LAN 越しに駆動。初期化時に全体マニュアルを返す自己文書化済み。

両者は**同じ `backend/services.py`** を呼ぶ（ロジック二重化なし・瘢痕ゲート回避不可）。rpgdev のアセット制作で繰り返した失敗（クロマキー黒漏れ・ダメージ版の pose/canvas ズレ・画風ドリフト・手作業加工・主観の版爆発）を、**ツールに焼いた瘢痕ルール**で構造的に殺すのが目的。

## 状態（2026-06-20）
**キャラ制作パイプラインが一周完成・実機検証済み。** テキスト → 素体 → 設定資料（バイブル）→ キャラLoRA → 任意ポーズ量産 → rpgdev へ採用。
- ✅ 素体生成（SDXL/Illustrious + BiRefNet matte）、ダメージ版編集（Qwen-Image-Edit・pose-lock）、画風LoRA v2、ControlNet 参照ポーズ。淡色(水)キャラは bg=auto で緑背景matte。
- ✅ キャラバイブル（マスターシート参照方式・自己完結HTML・claude.ai/design 共有）。
- ✅ キャラLoRA（バイブルのパネルで1クリック学習 → 任意ポーズ量産）。
- ✅ AIプロンプト生成（`claude -p`/`codex` を cwd=プロジェクトで実行・追加課金なし）。
- ✅ WebUI 全面刷新（ステップ導線・vanilla ESM・デザインシステム）／ MCP 自己文書化（全15ツール）。

導入は **[INSTALL.md](INSTALL.md)**、詳細な実装現状・設計判断は **[CLAUDE.md](CLAUDE.md)**（実装の正典）を参照。

## アーキテクチャ
- **バックエンドは Mac で動かす**（rpgdev も Claude/Codex も Mac＝「採用＝rpgdev へ書き出し」がローカル完結）。
- **ComfyUI は GPU機 `<GPU_HOST>`** で headless 稼働（NSSMサービス `ComfyUI`・RTX5090・torch2.12+cu130）。Mac のバックエンドが LAN 越しに HTTP+WS で駆動。
- 1 uvicorn プロセスに FastAPI（`/api/*`）と FastMCP（`/mcp`）を同居。

## 起動
> **初めて導入する人（と そのAI）は [INSTALL.md](INSTALL.md) を読む** — 前提（CUDA GPU で動く ComfyUI）・モデル配置（[docs/models.md](docs/models.md)）・任意機能・制約まで一通り。

```bash
cd sprite-forge-mcp
python3.12 -m venv .venv && . .venv/bin/activate   # 初回のみ（Python 3.11+ 必須）
pip install -r requirements.txt                     # 初回のみ
cp .env.example .env                                # SPRITEFORGE_COMFY_URL 等を設定
uvicorn backend.app:app --host 127.0.0.1 --port 8765
```
- WebUI: `http://127.0.0.1:8765/`
- MCP: `http://127.0.0.1:8765/mcp/`（`.mcp.json` 登録済み・uvicorn 起動中のみ）
- 到達確認: `curl localhost:8765/api/gpu` / `curl http://<GPU_HOST>:8188/system_stats`

## 構成
```
backend/    Python: FastAPI + FastMCP + 共有サービス層 + ComfyUI client + LoRA学習 + バイブル生成 + AIプロンプト生成
web/        vanilla ESM の ステップ導線 WebUI（ビルド工程なし）: shell/router + api/state/ui/jobs + features
design/     claude.ai/design 用ショーケース（デザインシステム・キャラバイブル）
docs/       設計ドキュメント（00〜07・設計の正典／実装は CLAUDE.md が最新）
```

## ドキュメント
導入は [INSTALL.md](INSTALL.md)＋[docs/models.md](docs/models.md)。実装の最新は [CLAUDE.md](CLAUDE.md)。以下 docs/ は設計の正典（一部は実装で進化＝各冒頭の状態注記参照）。
- [INSTALL](INSTALL.md) — セットアップ手順（前提・任意機能・制約）
- [Models manifest](docs/models.md) — 必須モデルの入手先・配置・必須ノード
- [00 Context & Pain](docs/00-context-and-pain.md) — なぜ作るか（rpgdev の苦闘史と痛点）
- [01 Research / SOTA](docs/01-research-sota.md) — 2026 現行調査の結論＋出典
- [02 Architecture](docs/02-architecture.md) — 1バックエンド2フェイス
- [03 Models & Runtime](docs/03-models-and-runtime.md) — モデル・VRAM・ランタイム判定
- [04 Tool Surface](docs/04-tool-surface.md) — サービス＝MCP＝HTTP
- [05 Output Contract](docs/05-output-contract.md) — rpgdev アセット契約
- [06 WebUI UX](docs/06-webui-ux.md) — 人間向け UI フロー
- [07 Validate on Box](docs/07-open-questions-validate-on-box.md) — 実機実証チェックリスト

## 環境
- GPU 機：`<GPU_HOST>`（Win11, RTX 5090 32GB, ComfyUI headless）。SSH = `<user>@<GPU_HOST>`。
- バックエンド/WebUI/matting は **Mac 側**で稼働。ComfyUI だけ GPU 機、LAN の IP 直指定で接続。
