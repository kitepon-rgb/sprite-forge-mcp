# 06 — WebUI UX（人間向け：生成→比較→注釈→編集→採用）

> **状態（2026-06-18）**: WebUI は全面刷新済み＝**ステップ導線**（①素体生成→②キャラバイブル→③キャラLoRA→④活用/共有＋副モード 編集/バリアント・設定/LoRA）。本書当初のフロー（生成→比較→注釈→編集の単一ページ）とは画面構成が異なる。**Konva は不採用**（素の canvas ＋ ESM モジュール分割）。現行UIの正典は `CLAUDE.md`「web/」。瘢痕ルール（checker背景・手描き禁止・マスク/点で指す）は踏襲。

> フロントは **vanilla ESM ＋ Konva.js（ビルド工程ゼロ）**。rpgdev の overlay/control ページ流儀を踏襲。過去のパス探し・インライン要求・ゲーム表示サイズ確認の煩わしさを根絶する。

## 画面構成（単一ページ）

1. **生成バー**（上）：プロンプト入力、element/pose/canvas、`count`（案数）、seed、LoRA 選択。「生成」ボタン。
2. **ギャラリー**（中央）：生成中の各候補をカードで**自動表示**（パス不要）。カードに **SSE 進捗バー**（pending/generating%/done）。クリックで拡大＋編集モーダル。
3. **編集/注釈パネル**（モーダル/右）：Konva canvas。ベース画像＋注釈レイヤ。
4. **採用バー**（下）：採用先名・base 指定（ダメージ版 pairing）・「採用」ボタン。採用ゲート結果（canvas/bbox/黒漏れ）をその場表示。

## 生成→比較フロー（痛点 #6 を殺す）
- 「生成」→ `POST /api/generate`（count=4）→ 即ギャラリーに4カードが並ぶ。
- 各カードは `GET /api/progress`（SSE）で**個別に**進捗更新。完了で画像差し替え（`GET /api/image/{id}`）。
- **ゲーム表示サイズプレビュー**トグル：採用先の CSS 表示幅（火469/地372/風435/水418px 等）に縮小表示して即判断。
- 並べて見比べ→気に入った1枚を選ぶ。パスは一切扱わない。

## 注釈（Konva・痛点 #2/#4 を殺す）
- **ブラシマスク**：編集領域を塗る（Qwen-Edit の inpaint マスク）。アルファ強度可。
- **赤ドット（点マーカー）**：ドラッグ可能円で「この部位」を指す。`mode:sam2` で SAM2 に渡し精密マスク化。
- **分離線（ポリライン）**：上下分割の壁（flood-fill 障壁／recompose 用）。
- **配置ドラッグ**：レイヤを動かして `placement:{dx,dy}` を得る（recompose）。
- 拡大ズーム＋グリッド表示。**手描きペイントは無し**（塗るのはマスク＝指し示しのみ。創作改変はしない＝手作業禁止の継承）。
- 送信 → `POST /api/annotation` → `mask_id`/points/line/placement を返し、`generate_variant`/`recompose` が消費。

## 編集（差分＝ダメージ版・痛点 #2）
- 編集モーダルで base＋マスク＋指示文 →「差分生成」→ `POST /api/variant`（Qwen-Edit, cfg=1.0, count=4）。
- 結果はギャラリーに**新しい候補列**として並ぶ。各候補に `fit_to_base` の事前監査バッジ（canvas一致/bbox差/黒漏れ）。

## 採用（痛点 #1/#2/#4 のゲート）
- 「採用」→ `POST /api/adopt {candidate_id, target_name, pair_with?}`。
- サーバが採用ゲート（canvas 一致・黒漏れ=0・ダメージ版 bbox 差≤1.0px）を実行。
- **不通過なら採用せず、理由（どの条件で落ちたか）をその場表示**（黒漏れ px・bbox 差・canvas 不一致）。
- 通過で `rpgdev/public/assets/sprites/<name>.png` へ書き出し＋ `.rpgdev/adoption.ndjson` 記録。採用後はギャラリーに「採用済み」表示。

## 実装メモ
- `EventSource('/api/progress')` で進捗購読。`fetch` で各 API。状態は最小限のモジュールスコープ。
- Konva は ESM/CDN 読み込み。レイヤ：背景(ベース) / 注釈(マスク・点・線) / 配置。`toDataURL`/`getImageData` でマスク PNG 出力。
- 重ければ補助のみ追加（過剰なフレームワークは入れない＝no-build 維持）。
