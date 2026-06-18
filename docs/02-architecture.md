# 02 — Architecture（1バックエンド＝2フェイス）

> **状態（2026-06-18）**: 実装と1点相違＝**backend / MCP / matting は box でなく Mac で稼働**（GPU機 は ComfyUI のみ）。理由＝採用先 rpgdev も Claude/Codex も Mac でローカル完結。下図の box 配置はこの点だけ読み替えること。1バックエンド2フェイス・共有 `services.py`・FastAPI＋FastMCP 同居は実装通り。最新は `CLAUDE.md`「アーキテクチャ」。

## 全体像

```
Mac (Claude/Codex 開発・ブラウザ)            box Windows (RTX5090 32GB)
  │                                          ┌────────────────────────────────────┐
  │  ブラウザ → 人間 WebUI (HTTP/SSE)        │ ComfyUI (headless / NSSM :8188)     │
  ├──────────────────────────────────────────┤        ▲ HTTP + WebSocket           │
  │  Claude/Codex → MCP (HTTP, LAN IP)        │ sprite-forge backend (Python)       │
  │  http://<GPU_HOST>:<PORT>               │  ├ FastAPI: /api/* + SSE /progress  │ ← 顔A 人間UI
  │                                          │  ├ FastMCP: /mcp ツール              │ ← 顔B エージェント
  │                                          │  ├ 共有サービス層（生成/編集/採用）  │
  │                                          │  │   └ ComfyUI WS client + Pillow    │
  │                                          │  └ 静的配信: vanilla ESM + Konva SPA │
  └───────────────────────────────────────────└────────────────────────────────────┘
                       採用時 → rpgdev/public/assets/sprites/ へ書き出し（採用ゲート後）
```

## 原則

1. **1つの Python バックエンド＝2つのフェイス**。人間 WebUI（FastAPI）とエージェント MCP（FastMCP）が**同一の共有サービス層**を呼ぶ。ロジックを二重化しない＝同期ズレを防ぐ。
2. **ComfyUI は内部詳細**。headless デーモン（NSSM サービス、`:8188`）。ユーザーはノードグラフを見ない。バックエンドが WS API（`/ws`・`/prompt`・`/history`・`/view`・`/upload`）でワークフロー JSON を投げ、進捗を購読。
3. **静かなフォールバック禁止**（rpgdev の鉄則を継承）。失敗はエラー全文＋段階＋対象パスで明示し、非サイレントに記録する。
4. **採用ゲートを必ず通す**。AI 出力をそのまま採用しない。bbox/canvas 一致・黒漏れ監査を通った物だけ `public/assets/sprites/` へ。

## プロセス構成

- 単一 `uvicorn` プロセスで FastAPI と FastMCP（`/mcp`）を同居マウント。
- ComfyUI クライアントは startup（FastAPI lifespan）で1接続を張り共有。WS タイムアウト/ハングは指数バックオフ再接続。
- `asyncio.Queue` でジョブを直列化（GPU は1ワークフローずつ＝実測知見の「同時常駐させない」規律）。
- バッチ生成は seed を振って 4–6 案。進捗は候補ごとに **SSE** で並列ストリーム。
- 生成物は box のローカル（例 `./.cache/generated/`）にキャッシュし、`GET /api/image/{id}` で配信＝ブラウザに即表示（パス探し不要）。

## HTTP / MCP 表面（同一サービスの2投影）

| サービス | HTTP（人間UI） | MCP（エージェント） |
|---|---|---|
| 状態 | `GET /api/state`, `GET /api/gpu` | `gpu_status` |
| 生成 | `POST /api/generate` | `generate_sprite` |
| 差分編集 | `POST /api/variant` | `generate_variant` |
| 注釈 | WebUI 内 Konva → `POST /api/annotation` | `annotate`（URL 返却＋待受） |
| 透過 | `POST /api/transparent` | `make_transparent` |
| ピクセル化 | `POST /api/pixelize` | `pixelize` |
| 採用ゲート | `POST /api/adopt` | `adopt` / `fit_to_base` |
| 進捗 | `GET /api/progress`（SSE） | （ツール戻り値＋ログ） |
| 比較 | `GET /api/compare/{id}` | `compare_grid` / `inspect` |

詳細仕様は [04-tool-surface.md](04-tool-surface.md)。

## ネットワーク / 配置

- LAN の IP 直指定（WAN 公開は不要）。`http://<GPU_HOST>:<PORT>`（既定ポートは実装時確定）。
- ComfyUI は `0.0.0.0:8188`（同一 box 内ループバックでも可）。バックエンドは `0.0.0.0:<PORT>` で LAN 公開。
- Claude/Codex には `http://<GPU_HOST>:<PORT>/mcp` を URL 型 MCP として登録。

## フロント（vanilla ESM ＋ Konva・ビルド工程ゼロ）

- rpgdev の overlay/control ページ流儀を踏襲（素の ESM、`EventSource` で SSE 購読、`fetch`）。
- 注釈は Konva.js（ESM/CDN）。ギャラリーは素の JS グリッド。詳細は [06-webui-ux.md](06-webui-ux.md)。
