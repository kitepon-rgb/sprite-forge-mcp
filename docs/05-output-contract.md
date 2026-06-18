# 05 — Output Contract（ゲームアセット契約）

> **状態（2026-06-18）**: 実装通り（RGBA・四隅 alpha=0・採用ゲート・採用先への書き出し＋adoption ログ）。**1点変更＝black-leak==0 ゲートは廃止**（キャラの暗色アウトラインを誤検出したため）。現行は四隅透過＋（Qwen-Edit経路の）識別色コンプライアンスで判定。詳細は `CLAUDE.md`「瘢痕ルール#1」。

> sprite-forge が生成・採用する成果物が満たすべき契約。**採用ゲート（`adopt`/`fit_to_base`）はこの契約を機械検証する**。出力先は採用先プロジェクトのスプライトディレクトリ（`SPRITEFORGE_RPGDEV_SPRITES`）。

## 採用キャンバス寸法
- 採用先プロジェクトは各スプライトに固有のキャンバス寸法を持つ（用途で可変：正方 ~1280²、横長、縦長など）。**固定値を仮定しない**。
- **既存ファイルの差し替え/ダメージ版**は、`adopt` が**対象ファイルの現キャンバスを基準**に一致を強制（寸法は対象から導出）。
- 新規アセットは、採用先プロジェクトの慣習寸法に合わせる。

## ダメージ版 pairing（最難関 #2）
- `<name>-damaged.png` は `<name>.png` と **キャンバス完全一致・有効 bbox 差 ≤ 1.0px**（`config.BBOX_TOL_PX`）。
- ゲーム側はベース↔ダメージを**同一 CSS 位置/倍率**で差し替える前提＝ピクセル整合が崩れると表示が破綻する。
- 許容差はこの上限（≤1.0px）に保つ。`VARIANT_DENOISE=0.7`＋衣装マスクで達成（マスク経路は bbox 0px 保証）。

## 透過（痛点 #1）
- 出力は **RGBA**。**四隅 alpha=0**。
- 採用ゲート＝四隅透過＋（Qwen-Edit経路の）識別色コンプライアンス（旧 black-leak==0 ゲートは廃止）。
- 残存クロマ経路は**黒禁止・既定 `#ff00cc`・対象色と最遠を自動選択**。

## 画風（痛点 #3/#5）
- 生成プロンプトに必須語句を必ず含む：`retro RPG / pixel art / old JRPG sprite / limited palette / chunky pixel clusters`。
- ドット粒度・パレットを既存アセットに揃える（画風 LoRA＋NEAREST 縮小→posterize≈28刻み, block≈2.4）。
- 太い暗色アウトライン維持（`thin/hairline outline` は禁止）。

## 命名・配置
- 採用先：`<採用先>/public/assets/sprites/<name>.png`（`SPRITEFORGE_RPGDEV_SPRITES`）。
- 候補/デバッグ/比較は採用先に置かない（墓場化を防ぐ。作業物はローカルの `.cache/` 側）。
- 採用は採用先の `.rpgdev/adoption.ndjson` に記録（いつ・どの候補・ゲート結果）。
