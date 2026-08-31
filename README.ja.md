# obsidian2date

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | 日本語 | [简体中文](README.zh-CN.md)

**直近の任意の期間をリサーチし、有用な部分を Obsidian に残す。**

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Tests](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml/badge.svg)](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml)

`obsidian2date` は、Reddit、X、YouTube、HN、GitHub、Polymarket、ウェブ全体に
わたって、あるトピックについて人々が実際に何を言っているかをリサーチします —
任意の期間を対象に（先週、直近 7 日間、直近 90 日間。30 日はあくまで
デフォルト）— 各ランを、リンクされた永続的な Obsidian ノートに変換します。

各ランが生成するもの:

- ソースに基づいた **ランノート**
- コンパクトな **ブリーフィング**
- 関連ランへの `[[ウィキリンク]]`
- 更新された **インデックス** と **ダッシュボード**

トラッキングなし。MIT。
[last30days-skill](https://github.com/mvanhorn/last30days-skill) の公開
フォーク。上流のリサーチエンジンはマージ可能な状態を保ちます。Python 3.12+
と Obsidian ボールトが必要です。ソースと API キーは任意です — 詳細は
[CONFIGURATION.md](CONFIGURATION.md) を参照。

## スラッシュコマンドとして使う（メインの経路）

`obsidian2date` は Agent Skill です。リポジトリを一度インストールすれば、
あとはエージェントで `/obsidian2date <トピック>` と打つだけです。スキルが
リサーチエンジンを実行し、ボールトを解決し、ノートを書き込み、パスを報告します。
覚えるべきフラグはありません — 「先週」「直近 90 日間」とリクエストに
書けば、スキルが適切なエンジンフラグに翻訳します。

| ホスト | インストール | その後 |
| --- | --- | --- |
| Claude Code | `npx skills add pauleschwarz/obsidian2date -g -y`（またはこのリポジトリを `.claude-plugin` として追加） | `/obsidian2date <トピック>` |
| Codex | リポジトリに `.codex-plugin/plugin.json` 同梱 | `/obsidian2date <トピック>` |
| Grok | `grok plugin marketplace add pauleschwarz/obsidian2date` | `/obsidian2date <トピック>` |
| Gemini CLI | リポジトリに `gemini-extension.json` 同梱 | `/obsidian2date <トピック>` |
| OpenClaw / agents.md ホスト | リポジトリに `.agents/` マニフェスト同梱 | `/obsidian2date <トピック>` |
| pi / スキル対応エージェント全般 | `skills/obsidian2date/` をエージェントのスキルディレクトリにシムリンクまたはコピー | `/obsidian2date <トピック>` |

スキルが各ランで行うこと（
[`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md) — モデルが
読む正規のランタイム仕様 を参照）:

1. ボールトを解決する（一度尋ね、セッション中は記憶する）
2. リクエストから期間を導く（デフォルトは 30 日）
3. `--emit=obsidian` でリサーチエンジンを実行する
4. ブリーフィングのパス、ランノートのパス、および部分的または利用不可のソースを正直に報告する

## クイックスタート（CLI フォールバック）

スクリプト、cron、開発時のエンジンテストには、CLI を直接呼び出します。
これはフォールバックの経路であり、メインではありません — 上のスラッシュ
コマンドが本体です。

```bash
git clone https://github.com/pauleschwarz/obsidian2date.git
cd obsidian2date

python3 skills/last30days/scripts/last30days.py \
  "local LLM agent frameworks" \
  --emit=obsidian \
  --obsidian-vault /path/to/your/vault
```

ボールトを一度設定しておくなら:

```bash
export OBSIDIAN2DATE_VAULT=/path/to/your/vault
python3 skills/last30days/scripts/last30days.py "topic" --emit=obsidian
```

### 期間

`30` 日はあくまでデフォルトです。自由に指定できます:

```bash
python3 skills/last30days/scripts/last30days.py "AI video tools" --emit=obsidian --days 7    # 先週
python3 skills/last30days/scripts/last30days.py "rust async runtimes" --emit=obsidian --days 90  # 四半期スイープ
python3 skills/last30days/scripts/last30days.py "election odds" --emit=obsidian --days 14 --as-of 2026-08-15
```

スラッシュコマンドでは、ただ言うだけです: 「AI video tools の直近 7 日間を
リサーチして」。

### ボールトの解決

エクスポート先はこの順で解決されます:

1. `--obsidian-vault PATH`（明示的に指定された存在しないパスはエクスポート用に作成される）
2. `OBSIDIAN2DATE_VAULT`
3. `LAST30DAYS_OBSIDIAN_VAULT`
4. 既存の `~/Desktop/brain-paul`

環境変数とデスクトップの候補は、すでにディレクトリとして存在している必要が
あります。空または空白のみのボールト環境変数が設定されている場合、暗黙の
フォールバックは意図的に無効化されます。どれも解決しない場合、コマンドは
次のメッセージとともに停止します:

```text
No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT.
```

`.env` ファイルでは `~/...` または絶対パスを使ってください。`$HOME` はそこでは
展開されません。既存のノートは決して上書きされません。ファイル名の衝突には
数値の接尾辞が付きます。

## 書き出されるもの

ボールトルート直下のデフォルト構成:

```text
90_Quellen/obsidian2date/
  runs/YYYY-MM-DD-<slug>.md
  briefings/YYYY-MM-DD-<slug>-briefing.md
  Index.md
  Dashboard.md
```

ノートは上書きされません。同日の衝突には数値の接尾辞が付きます。トークンの
重なりが検出された場合、関連する過去のランは Obsidian の `[[ウィキリンク]]`
で結ばれます。

## ソースとキー

上流と同じ土台:

- **デフォルトでキー不要:** Reddit、Hacker News、Polymarket、GitHub、Web
- **オプション:** X（ブラウザ Cookie / バックエンド）、YouTube（`yt-dlp`）,
  TikTok/IG（ScrapeCreators）、その他の有料/オプトイン バックエンド

完全なマトリクスとキーの設定は
[`CONFIGURATION.md`](CONFIGURATION.md) を参照してください。

## 安全な診断

リサーチの前に、権限のみのチェックを実行できます:

```text
$ python3 skills/last30days/scripts/last30days.py --preflight
last30days preflight
Status: Ready to research with safe defaults.
...
Local writes:
- none planned
```

`--preflight` は安全です: **Cookie の読み取り、ファイルの書き込み、
リサーチの実行を一切行わずに**動きます。ソースやインストール済みバックエンドの
トラブルシューティングには、代わりにヘルスチェックを使ってください:

```bash
python3 skills/last30days/scripts/last30days.py doctor
```

## 上流のモードもそのまま動く

```bash
# オリジナルのコンパクトな合成エンベロープ
python3 skills/last30days/scripts/last30days.py "topic" --emit=compact

# エージェント用 JSON
python3 skills/last30days/scripts/last30days.py "topic" --emit=json

# 本番向けブリーフ
python3 skills/last30days/scripts/last30days.py "topic" --emit=brief
```

## 上流との関係

| 項目 | ポリシー |
| --- | --- |
| リサーチエンジン | `upstream/main` とマージ可能な状態を保つ |
| Obsidian エクスポート | 追加モジュール: `lib/obsidian_export.py` |
| ブランディング / スキル | `obsidian2date` |
| ライセンス | MIT。上流の著作権表示を保持 |

```bash
git remote add upstream https://github.com/mvanhorn/last30days-skill.git
git fetch upstream
git merge upstream/main
```

## クレジット

- 上流リサーチエンジン: [Matt Van Horn / last30days](https://github.com/mvanhorn/last30days-skill)
- Obsidian エクスポート経路 + 公開フォークのパッケージング: [pauleschwarz](https://github.com/pauleschwarz)

## ライセンス

MIT。[LICENSE](LICENSE) を参照。
