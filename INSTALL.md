# INSTALL — sprite-forge-mcp セットアップ

> このリポは**推論を持たない薄いオーケストレータ**。生成計算はすべて別マシンの **ComfyUI** が行う。「クローンして即生成」はできない＝**CUDA GPU で動く ComfyUI が必須**。以下はその前提で、他人（とそのAI）が自分の環境に立てるための手順。

## 前提（これが無いと動かない）
- **CUDA GPU で稼働する ComfyUI**（HTTP+WS で到達可能）。VRAM は **~30GB 目安（RTX 5090 級 32GB）**＝編集経路で DiT が ~24–27GB 常駐する。
- **必須モデル一式**を ComfyUI に配置（→ [docs/models.md](docs/models.md)）。
- **必須カスタムノード**：Qwen-Image-Edit ノード（`TextEncodeQwenImageEditPlus` 等）＋ Union ControlNet（`SetUnionControlNetType`）。ComfyUI は **0.25.0+** で検証済み（→ docs/models.md）。
- backend 用に **Python 3.11+**（`fastmcp` 要件）。

## 構成
- **backend**（FastAPI＋FastMCP・このリポ）＝制御マシンで動く（Mac/Linux/Windows どれでも可）。
- **ComfyUI**＝GPU マシンで動く。**同一マシンでも別マシンでも可**（backend は `SPRITEFORGE_COMFY_URL` で ComfyUI を指すだけ）。

## 1. GPU 側（ComfyUI）を用意
1. ComfyUI（0.25.0+）を導入。
2. 上記**必須カスタムノード**を入れる（Qwen-Image-Edit ノードセット／Union ControlNet サポート）。
3. [docs/models.md](docs/models.md) のモデルを**正確なファイル名で** `models/<サブフォルダ>/` に配置。
4. LAN 越しに使うなら ComfyUI を `0.0.0.0:8188` で公開（同一マシンなら不要）。
5. 確認：`curl http://<GPU_HOST>:8188/system_stats` が返る。`/object_info` に必須 class_type が在るか見るとノード不足を早期発見できる。

## 2. backend を立てる
```bash
git clone https://github.com/<you>/sprite-forge-mcp && cd sprite-forge-mcp
python3.12 -m venv .venv && . .venv/bin/activate     # 3.11+ 必須
pip install -r requirements.txt
cp .env.example .env                                  # 編集して下記を設定
uvicorn backend.app:app --host 127.0.0.1 --port 8765
```
`.env` で最低限：
- `SPRITEFORGE_COMFY_URL=http://<あなたのComfyUI>:8188`
- （採用を使うなら）`SPRITEFORGE_RPGDEV_SPRITES=/書き出し先/sprites`
- 全変数は [.env.example](.env.example) と `backend/config.py` を参照。`.env` は gitignore 済み。

## 3. 動作確認
- `curl localhost:8765/api/gpu` → `{"comfy_up":true,...}`。
- WebUI：ブラウザで `http://127.0.0.1:8765/` →「① 素体生成」で1枚出す。
- MCP：`http://127.0.0.1:8765/mcp/`（`.mcp.json` で Claude/Codex に URL 型 MCP 登録。初期化時に使い方マニュアルを返す）。

## 任意機能（無くてもコア＝生成/編集/バイブルは動く）
| 機能 | 要るもの |
|---|---|
| **SAM2 自動マスク** | `python3.12 -m venv .venv-sam2 && .venv-sam2/bin/pip install ultralytics`（任意・未導入でも手描きマスク可） |
| **AIプロンプト生成** | あなた自身の `claude`（or `codex`）CLI が PATH＋ログイン済み（Anthropic APIキー不要） |
| **キャラ/画風 LoRA 学習** | GPU box に kohya **sd-scripts** ＋ accelerate/torch-CUDA。**※下記の制約参照** |
| **claude.ai/design 共有** | operator の claude.ai ログイン（任意・本体機能ではない） |

## 「何が無いと何が動くか」
| やりたいこと | 必須 |
|---|---|
| 素体生成 / ダメージ版編集 / キャラバイブル | ComfyUI＋モデル一式（**必須**） |
| 採用（rpgdev等へ書き出し） | `SPRITEFORGE_RPGDEV_SPRITES`（書き出し先ディレクトリ） |
| LoRA 学習 | GPU box の sd-scripts/accelerate（**現状 Windows 前提**） |
| SAM2 / AIプロンプト生成 / design共有 | それぞれ上表の任意セットアップ（無ければその機能だけ無効） |

## 正直な制約（盛らない）
- **LoRA 学習は現状 Windows box 前提**：`backend/training.py` が PowerShell/NSSM コマンドを生成し、box のパスを前提にする。**Linux/Mac の ComfyUI box では「学習だけ」動かない**（生成・編集・バイブルは OS 非依存で動く）。POSIX 対応は需要が出たら入れる予定。
- 長時間 GPU 学習で Windows のディスプレイが固まる場合は `TdrDelay=60`（レジストリ）＋再起動が要る（Windows 固有・Linux box は無関係）。
- モデルは各自で取得（→ docs/models.md）。リポにモデル重みは含めない。
- `adopt` は書き出し先へ**不可逆に上書き**する。設定先を確認してから使うこと。

詳しい設計は [docs/00–07](docs/)、実装の正典は [CLAUDE.md](CLAUDE.md)。
