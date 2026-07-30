# /last30days

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | 日本語 | [简体中文](README.zh-CN.md)

<p align="center">
  <img src="media/pr-assets/last30days-ad.gif" width="720" alt="last30days - an AI agent-led search engine that searches people, not editors" />
</p>

<p align="center">
  <a href="https://github.com/mvanhorn/last30days-skill">
    <img src="https://img.shields.io/badge/%231-Repository%20Of%20The%20Day-6f42c1?style=for-the-badge&logo=github&label=GITHUB%20TRENDING" alt="GitHub Trending #1 Repository Of The Day" />
  </a>
  <br/>
  <a href="https://trendshift.io/repositories/21997" target="_blank">
    <img src="https://trendshift.io/api/badge/repositories/21997" alt="mvanhorn/last30days-skill | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
  </a>
</p>

**AIエージェント主導の検索エンジンは、upvotes, likes, 実際のお金によって得点 - エディタではありません。**

このREADME は、現在の v3 パイプラインを追跡します。 ランタイムスキルのスペックは、[skills/last30days/SKILL.md](skills/last30days/SKILL.md), これは、最新のコマンドとセットアップの動作のための真実のソースです。.

**Claude Code(推奨 — マーケットプレイスによる自動更新):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI50以上のもの[Agent Skills](https://agentskills.io)ホスト:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g`すべてのプロジェクトで利用できる、ユーザーのグローバルにインストールします。 プロジェクトごとのスコープにドロップします。

その他のインストールオプション (claude.ai web,OpenClaw、マニュアル)[Install](#インストール)以下のセクション。

ゼロコンフィグReddit、HN、PolymarketとGitHubすぐに働く。 一度実行し、セットアップウィザードがXのロックを解除します。YouTube, TikTok, arXiv, Techmeme30秒以上。

---

Redditアップボット。 Xは好きです。YouTubeトランスクリプト。TikTokエンゲージメント。Polymarket実際のお金とインサイダー情報によって裏付けられたオッズ。 それは、毎日、自分の注意と財布に投票する万人の人々です。/last30days実質の人々が実際に何を従事しているか、そしてAIの代理店がそれを1つの短いに合成することを判断することによってそれを並列で捜します。

Googleエディタを集計します。/last30days人を検索します。

単一のAIがすべてにアクセスできないため、他の場所でこの検索を取得することはできません。Google検索は触れませんRedditコメントやX投稿。ChatGPTお問い合わせRedditしかし、Xを検索することはできませんTikTok. ジェミニは持っていますYouTubeしかし、そうではありませんReddit. Claudeそれらをネイティブに持っていません。 各プラットフォームは、独自のウォールガーデンですAPI、独自のトークン、独自のauth。 しかし、自分の鍵やブラウザのセッションを持参し、突然、AIエージェントは一度にすべて検索でき、お互いにスコアをつけ、実際に何かを伝えることができます。

これはロック解除です。 より良い検索エンジンではありません。 エージェントが橋渡しするダース接続プラットフォーム。

```
/last30days Peter Steinberger
```

明日は会いましょう。 お問い合わせGoogleお問い合わせ 自分を手に入れるLinkedInから 2023./last30days彼らが実際にこの月をやっていることをあなたに与えます: 参加OpenAIお問い合わせCodex, サードパーティの代理店でAnthropicの禁止を戦う, 発送 23PRs85%の合併率で、建物 "LobsterOS"クロスデバイスエージェント制御、r/Claudeコードヒット 569 彼がヒーローか「不十分」かどうかを逆転させるアップボット. Xの投稿に散らばるRedditスレッド,YouTubeトランスクリプトとGitHubコミット。 何もなかったGoogle.

## なぜこれが存在するのか

人工知能に追いつくために構築しました。 日々変化する変化や、Redditそして、X の nerds は、最初にその上に常にあります。 私はより良いプロンプトを必要とし、トレーニングデータは、コミュニティがすでに把握していたものの後ろに常に数か月後にありました。

しかし、それは何かを大きく変えました。 今、私はビジネスについての最後の30日真実を知るために販売コールの前にそれを実行します。 ミーティングの前に、誰かの最近のツイートとポッドキャストのトランスクリプトを読んでください。 前のページへDisney Worldどの乗車が閉鎖されているか、コミュニティが何を言うかを知るための旅行Genie+. 人が実際にどんな問題を抱えているかを知るために何かを造る前に.

社長に会うなら、すべてのツイートを読んでください。YouTube過去30日間の成績証明書? お問い合わせ

## 人によって得たソース

|ソース|人々があなたを教えてくれるもの|
|--------|--------------------------|
| **Reddit** |ろ過されていないテイク。 実際の上書きカウント、無料、なしのトップコメントAPIキー。 実際の意見は?Googleブリス。|
|*X / Twitter**|ホットテイク、エキスパートスレッド、ブレイク反応。 最初に知るために、最初に主張する。|
| **YouTube** |深夜45分ダイブ 問題の5つの引用語句を検索した完全なトランスクリプト。|
| **TikTok** |クリエーターが3.6Mの人々を連れて行くと、あなたは決して見つけることができませんGoogle. |
| **Instagram Reels** |単語のトランスクリプトによるインフルエンサーの視点。 視覚文化信号。|
| **Hacker News** |開発者のコンセンサス。 825 ポイント, 899コメント. 技術的な人が実際に議論する場所。|
| **Polymarket** |コメントはありません。 オッズ。 実質のお金によって支えられる。 アルバム販売に関する96%の自信 取得の4%。|
| **GitHub** |人のために:PR星、リリースノートによる速度、トップリpos。 トピック:問題と議論。|
| **Digg** |キュレーションストーリー クラスターからDigg'AI 1000 のリーダーボード (~1000 の High-signal AI アカウントが X に帰属します)。 自動使用可能時`digg-pp-cli`お問い合わせPATH. |
| **arXiv** |ハイプの背後にある紙。 窓の新しい研究、自由、いいえAPIキー。 自動使用可能時`arxiv-pp-cli`お問い合わせPATH(初回設定でインストール)|
| **Techmeme** |tech-newsの編集層、30日間に渡る日付。 無料, いいえAPIキー。 自動使用可能時`techmeme-pp-cli`お問い合わせPATH(初回設定でインストール)|
| **LinkedIn** |専門の信号。 高い信号として重くされる記事および記事。|
| **StockTwits** |トレーダーの感情。 トピックがティッカーや暗号であるときに自動アクティブ化します。|
| **Threads** |投稿Twitterテキストレイヤー。 クリエイターやブランドとの会話|
| **Pinterest** |視覚的な発見。 ピン、保存、製品やアイデアに関するコメント。|
| **Xiaohongshu (RED)** |中国のライフスタイル、製品、クリエイターの信号。 リクエストを明示的に`--search xhs`ロギングインx-mcpブラウザプラグインまたは`xiaohongshu-mcp`現地でサービスを展開しています。|
| **Bluesky** |分散型社会層。 ポストTwitterの移行からプロトコル投稿。|
|**性能**|地上のソナーの統合、未加工調査API行、深層研究|
|**ウェブ**|編集カバレッジ、ブログの比較。 1つの信号だけではありません。|

コミュニティコントリビューターが増え続ける。Truth Socialそして他のニッチの源は方法のより多くのエンジンにあります。

ツイートReddit1,500のアップボットでスレッドは、誰も読んでいないブログ投稿よりも強い信号です。 ツイートTikTok3.6M ビューでは、プレスリリースよりも文化的に関連しているものについて詳しく説明します。Polymarketボリュームの$66Kでバックされたオッズは、punditの推測よりも議論するのは困難です。

実際に従事している現実の人々によって合成のランク。 社会的関連性、ないSEO関連する。

## 実際に使用している人

**会議のため。**`/last30days Peter Steinberger`- 参加OpenAIお問い合わせCodexチーム, サードパーティのエージェントでAnthropicの禁止を戦う, 23PRs85%の合併率で合併GitHub, 建物LobsterOSクロスデバイスエージェント制御用 ログインClaudeコード: "以来OpenClawリリースされて、他のものを通してそれを実行すると広く知られていましたAPI最終的に禁止されたつもりだった」(227 投票)。 そうではありませんLinkedIn.

**採用信号を読むため**`/last30days Listen Labs --hiring-signals`- 現在のジョブとキャリアページは、フォーカスシフトの証拠を引用する:企業のセキュリティ、顧客の成功、インフラストラクチャ、または製品拡張に採用します。 レポートは、配線が信号に表示されること、ロードマップが出荷するものではありません。

**ピーク前のトピックを見つける。** お問い合わせ`/last30days what's exploding in AI agents?`そして発見モードへの技術スイッチ:エンジンの渦Redditカテゴリ リスト,Hacker Newsフロント/ベストストーリーDigg'AI 1000 フィードと X 認証時。エージェントは、指名(名前、ジャンクフィルタリング、コンテンツワース)を判断し、ポッドキャスト/ X 粒子の角度を記述します。その後、5-10の速度でランクされたトピックを取得します。 すべての結果には、クロスソース番号、勢いラベル、および準備が整ったものが含まれます。`/last30days "<topic>"`フォローアップ。

**何かが落ちるとき。**`/last30days Kanye West`- 英国は、彼のビザをブロックしました, ワイヤレスフェスティバルはキャンセルしました, スポンサーは逃げました. しかし、BULLYはビルボードで2位デビューしました。 ファンタノが「ヤ・サバティカル」から戻って、それを見直しました (653K ビュー). SoFi Homecomingは、ローレン・ヒルとトラビス・スコットを44曲引き出す。Polymarket: 「カニエのつぶやきが再びか?」 86% はい。 23Redditスレッド, 17YouTubeビデオ、86K の upvotes。

**ツールを比較する。**`/last30days OpenClaw vs Hermes vs Paperclip`- 「競合他社ではなく、レイヤーです。」OpenClawエクセプター (351K)GitHub星, ライブ), エルメスは、自己即興脳です (31K星), ペーパークリップは、orgのチャートです (49K星). スターカウントは、からライブを引っ張りましたGitHub APIブログ投稿を stale しない。 アーキテクチャ、メモリ、セキュリティ、最高のサイドバイサイドテーブル。 パープル@IMJustinBrooke: "OpenClaw= チャーマーダー、ヘルメス = チャリザード。

**世界を理解する。**`/last30days Iran vs USA`- 戦争38日目。 イランがホルムズの海峡を再開するためのトランプの火曜日の期限。 米国戦車2隻がダウン。 $126/バレルのオイル。 IEAは「世界油市場史における最大の供給破壊」と呼びました。Polymarket: 12月31日(火)74%で消火。 27 Xの投稿、10YouTubeビデオ、20の予測市場。

**旅行の前に。**`/last30days Universal Epic Universe`- 既に工事中の拡張。 "Project 680" はファイルを許可します。 インフラで確認した花火大会だが、発表されていない。 待ち時間:Mine-Cart Madness 平均 148 分. 年間パスはなく、ローカルは不満です。 スターダストレーサーは4月5日までの改装のためにダウンします。

**早く何かを学ぶため**`/last30days Nano Banana Pro prompting` - JSON-structedプロンプトはタグスープを置き換えています。@pictsbyai's のネストされたフォーマットは「出血を受け入れる」を防ぎます。 ワークフローを編集するワークフローは、再生を破ります。 その後、コミュニティが何を語ったかを正確に使用して生産のプロンプトを書きます。

## 新着情報

5月のv3.3発表以来、v3.11.1(7月2026)として:175合併PRs- 52のコミュニティコントリビューターの122 - 15回のリリースで。 これは、着陸したものです。

### ファーストクラスOpenAI Codex

/last30daysネイティブCodexガイド付きセットアップ付きプラグイン - ポートではなく、一流の市民。 Renderer-awareの引用は意味しますCodex出力は URL のスープ(#694)の代りに短いように読み、同じエンジンは動きますClaude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClaw, と 50+Agent Skillsホスト。Codexプラグインマニフェストによる[@rfoust](https://github.com/rfoust) (#686), Codexauthの修正による[@tmchow](https://github.com/tmchow) (#698).

### arXiv, TechmemeとDigg- 無料、なしAPIキーキー

arXivハイプの背後にある紙を持参し、Techmeme編集技術ニュースレイヤー - 無料、ゼロキー、および最初の実行セットアップがインストールされますCLIs なので、自動的に起動します (#709).Digg'AI 1000ストーリークラスターは、同じ方法でXのauthなしで到着 - セットアップは無料でインストールしますDigg CLIあなたのため(#590)。Trustpilot消費者ブランドのリサーチのためにオプトインを出荷します。

### 無料Reddit実際のスコアとトップコメントの増加

Reddit'sパブリック .jsonAPI死亡した; フリーパスがより強く戻ってきました。 キーレスRSS +シュレッディットスクレイピング(#457)、アークティックシフト(#696)を介して実質的なアップボットカウントと専用のサブレッドディットディスカバリー、および関連するフロアなので、ウイルスオフトピックポストがあなたのブリーフをハイジャックすることはできません(#488、おかげで[@rzachsmith](https://github.com/rzachsmith))。 いいえ。APIキー。 実際のスコア。 トップコメントが含まれています。

### すべての簡単なコメントで最高のコメント

コメントは現在、ソースを渡るデフォルトでレイヤーです。 ランクベースのダイバーシティでインスタグラムのコメントで、5つのホットが1つの投稿から来ることはありません(#751)、YouTubeコメントとコメントScrapeCreatorsyt-dlp が起動したときにトランスクリプトのバックアップ(#637)、およびクラウド投票されたコメントが重なったときBest Takesコミュニティの最も楽しいラインは、スコアリング(#592、#608)を存続させます。

### 1人の医者の命令

健康チェックを要求し、医師はすべてのソースを実行し、その後、キーが欠落している正確な修正を処方します。CLIオフPATH, クッキーが期限切れ (#753). Xが薄くなってきた理由はこれ以上推測しません。

### X検索、再構築

Xのパイプラインは、地上のオーバーホールを持っています: からとAboutレーンので、人の自身の投稿とそれらについての会話は、両方のランク(#610)、人身のサブクエリの議論(#611)、インタラクション・シグナル・ランキング(#613)、および自動バックエンド・フェイルオーバー(#622)で1つのXソース。 プラス正直`--diagnose`実際にプローブオース (#609).

### より多くのソースが加わりました

LinkedInお問い合わせScrapeCreators、高い信号として記事を使って()[@ravstr](https://github.com/ravstr), #702). StockTwitsティッカーと暗号トピックの自動アクティブ化 ()[@wtiwana](https://github.com/wtiwana), #658). 複雑さは直接育ちましたAPIモードと非同期深層研究()[@sk-holmes](https://github.com/sk-holmes), #629).

### コミュニティによって硬化

セキュリティ波は、ほぼ完全にコミュニティの作業でした。保存されたXSSは、HTMLレンダー ()[@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars))、ロックダウンクッキーの臨時雇用者ファイル、OpenSSFのスコアカードが付いている供給鎖堅くされたCIおよび作成の証明()[@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909))、SemgrepおよびOSV-ScannerのスキャンおよびaPR依存症レビューゲート()[@23241a6749](https://github.com/23241a6749))、60%で導入されたテスト カバーの床および84%に上げられたので()[@gourab5139014](https://github.com/gourab5139014))、およびすべてのCRITICALの発見(#768)でクリアされたヘルメスのセキュリティスキャン。

### さらなる回復

ヘブライ語と非ラテン語 (Hebrew)[@dudyme](https://github.com/dudyme)). CJK中国のソースのための -aware のトークン化 ([@An-idd](https://github.com/An-idd)). AWindows互換性の波。 フルクロム家族を渡るクッキー抽出 - ブレイブ、エッジ、ヴィヴァルディ、オペラ、アーク(Arc)[@andrey-esipov](https://github.com/andrey-esipov)) - プラスmacOS Keychainそして、Linuxpass(1) 認証情報。`--as-of`歴史の振り返り ([@chiyi-creator](https://github.com/chiyi-creator))。自動約束されるPython3.12 ビア uv (uv)[@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals`会社の求人ページを読んでください。 実行中のウォッチリストのデルタ。

### それでもv3から箱の中に

v3 の基礎はすべてここにあります: 単一の前に正しいハンドル、subreddits、およびハッシュタグを解決する事前調査の脳API火を呼ぶ(によって造られる)[@j-sperling](https://github.com/j-sperling)); Best Takesユーモアと死亡率の上昇、クロスソースクラスターマージ、シングルパス比較(以下「比較」)CLI対決MCP" 3 分ではなく 12 で。自動検出`--competitors`比較;GitHub人モード (`--github-user=steipete`); ELI5任意の実行後のモード(「eli5 on」)。 共有可能で、自己完結HTML簡略化(簡略化)`--emit=html`)。 構成ノブは住んでいます[CONFIGURATION.md](CONFIGURATION.md).

## インストール

|ステンレス|インストール|アップデート|
|---------|---------|---------|
| **Claude Code** (推奨)| `/plugin marketplace add mvanhorn/last30days-skill` |市場を経由して自動車、または`claude plugin update last30days@last30days-skill` |
| **Grok** (xAIビルド)CLI) | `grok plugin marketplace add mvanhorn/last30days-skill`それから`grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI50以上のもの[Agent Skills](https://agentskills.io)ホスト**| `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
|**claude.ai** (web)| [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill)claude.ai > カスタマイズ > スキル > + > スキルを作成する > スキルをアップロード|再ダウンロードおよび再アップロード|
| **Claude Desktop** | [Download the `.mcpb` for your platform](https://github.com/mvanhorn/last30days-skill/releases/latest)設定にドラッグします。 > エクステンション|再ダウンロードと新しいバンドルをドラッグ|
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code(推奨)

```
/plugin marketplace add mvanhorn/last30days-skill
```

おすすめの理由Claude Codeマーケットプレースは、新しいリリース公開時にプラグインキャッシュがバージョンアップされ、自動修正されます。 ログイン`claude plugin update last30days@last30days-skill`チェックを強制する。

エージェント・スキル・インストール・パスを使わなければClaude Code、それはまた支えられます:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

ネイティブプラグインと`npx skills`インストールは共存できます。 注意:Claude Codeインストール方法を渡ってdedupeしません: 両方のマーケットプレースプラグインと`npx skills`アクティブなコピー,`/last30days`2つのエントリが表示されます。 機械ごとの1つの取付け方法を使用して下さい。

### Grok(xAIビルド)CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) ネイティブプラグインとしてLast30daysをインストールします。 直接インストールはリポジトリを追跡します。

```bash
grok plugin install mvanhorn/last30days-skill
```

または、このリポジトリをマーケットプレースソースとして追加し、プラグイン名でインストールします。

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

追加する`--trust`インストール確認をスキップします。 更新情報`grok plugin update last30days`. Grokまた読みますClaude Code互換性のためのマニフェスト; ネイティブ`.grok-plugin/`ペアは、ファーストクラスの車線(および公式のもの)です[xAI marketplace](https://github.com/xai-org/plugin-marketplace)リストポイント`npx skills add`有効なクロスホストフォールバックのまま。

### Codex, Cursor, Copilot, Gemini CLIその他Agent Skillsホスト

オープンでインストールする[Agent Skills](https://agentskills.io) CLI— 50以上のハーネスに対応`codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose`、および多く(完全なリスト)[vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

ふりがな`-g`(グローバル) フラグはユーザーディレクトリにインストールされ、すべてのプロジェクトでスキルが利用できます。 なし`-g`, `npx skills`project-locally をインストール`./.skills/`(レポに同封) リサーチ・ザ・ワールド・ツールのグローバルは、あなたが望むものです。

Codexデスクトップや他のフォルダモードホストは、通常のフォルダだけでなく、Gitリポジトリで動作することができます。 最初の研究の前に、ホストエージェントにバンドルされた実行を依頼`scripts/last30days.py --preflight`読み込まれたスキルディレクトリから。 ソースチェックアウトでは、同等のコマンドは`python3 skills/last30days/scripts/last30days.py --preflight`. 設定ソース、ブラウザ・クッキー・プラン、計画書、オプションのコマンド、および無視されたプロジェクト・コンフィグは、クッキー、書き込みファイル、または研究を実行せずに表示します。

デフォルトでは、このインストールはどのハーネスにも使用できます。`npx skills`検出します。 特定のもの(または複数の)をターゲットにするには

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

後で更新して下さい:

```bash
npx skills update last30days -g
```

またはグローバルにインストールされているすべての更新を`npx skills`:

```bash
npx skills update -g
```

リストと削除`npx skills list -g`そして、`npx skills remove last30days -g`.

### claude.ai (web)

1. [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill)最新のリリースから
2. お問い合わせ[claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. クリックします。`+`スキルパネルのボタン > クリック`Create skill` > `Upload a skill`ファイルの閲覧/ドロップ

機能の最初の「コード実行とファイル作成」を有効にする — スキルはそれなしで実行しません。

### Claude Desktop

Claude Desktopインストール`/last30days`としてMCPサーバ経由で`.mcpb`バンドル(モデルコンテキストプロトコルパッケージをワンクリック)。

1. お問い合わせ[latest release](https://github.com/mvanhorn/last30days-skill/releases/latest)ダウンロード`.mcpb`あなたのプラットフォームのために:
   - macOSアップルシリコン:`last30days-pp-mcp-darwin-arm64.mcpb`
   - macOSインテル:`last30days-pp-mcp-darwin-amd64.mcpb`
   - Linuxx86 64:`last30days-pp-mcp-linux-amd64.mcpb`
2. オープンClaude Desktop設定 > 拡張子、ファイルをドラッグします。
3. プロンプトが表示されたら、貼り付けAPI有効にしたいソースのキー。 すべてのフィールドはオプションです。すべてのフィールドをスキップすると、エンジンはWeb専用のモードに劣化します。 鍵はOSキーホルダーに保存されます。
4. リスタートClaude Desktop. お問い合わせClaude"Research Peter Steinberger" または任意のトピックに、それは呼び出します`research`ツール。

**ホストの条件:**Python3.12+ にPATH. バンドルはエンジンのソースを出荷しますが、ローカルを使用するPython通訳者。 インストールから[python.org](https://www.python.org/downloads/)お問い合わせWindows; macOSそして最も多くLinuxdistrosは互換性のあるバージョンを出荷します。

**キーはコードスキルと同期しません。**Claude Desktopそして、Claude Codeデザインで独立したクレデンシャルストアを維持します。 既に設定されている場合`~/.config/last30days/.env`コードスキルについては、ここで同じキーを再入力します。

Windowsパープラットフォームのマニフェストエントリ ポイントがソートされるまでサポートが拒否されます。フォローアップの問題で追跡します。

### OpenClaw

```bash
clawhub install last30days-official
```

外部のX/Twitterアクションワークフローの場合`/last30days`研究、投稿など
ツイートや返信、フォロワーエクスポート、メディア処理、モニター、プレゼント
引くこと、使用[TweetClaw](https://github.com/Xquik-dev/tweetclaw)仲間として
OpenClawプラグイン。TweetClawXquik-dev によって維持され、としてだけリストされます
オプションのコンパニオンパス、Last30daysの依存性または支持ではありません。

### マニュアル(デベロッパー)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

symlink は、作業ツリーと同期して、必要な再コピーが不要になります。 お問い合わせ`claude.ai`, ビルド`.skill`ソースからファイル:`bash skills/last30days/scripts/build-skill.sh`プロデュース`dist/last30days.skill`.

Reddit(コメントあり)Hacker News, PolymarketとGitHubすぐに働く。 ゼロ構成。 ログイン`/last30days`セットアップウィザードは、30秒でより多くのソースをアンロックします。arXivそして、Techmeme CLIお問い合わせ

## 自分の鍵を持参

これらのプラットフォームは互いに関係を持たない。 Xは何を知らないReddit考えます。YouTube見えないTikTok。しかし、あなた自身を持参することができますAPIキーとブラウザトークン、そして突然、あなたは一度にそれらすべてにアクセスしている。

|ソース|必要なもの|コスト|
|---------|---------------|------|
| Reddit(コメントあり) + HN +Polymarket + GitHub + StockTwits |コメントはありません。|無料|
| arXiv + Techmeme |無料CLIs, 初回設定で自動インストール|無料|
|X / Twitterの|任意のブラウザでx.comにログインするか、または設定`XQUIK_API_KEY` / `XAI_API_KEY` |ブラウザのクッキーは無料です。キーはプロバイダ固有のものです。|
| YouTube | `brew install yt-dlp` |無料|
| Bluesky |bsky.appからのアプリパスワード|無料|
| TikTok+ インスタグラム +Threads + Pinterest + LinkedIn + YouTubeコメント| ScrapeCreatorsキーキー|10,000 の自由な呼出し、それから PAYG|
| Xiaohongshu (RED) |ログインx-mcpブラウザプラグインを実行するか、`xiaohongshu-mcp`サービスおよびオプトイン`--search xhs`実行または`INCLUDE_SOURCES=xiaohongshu`お問い合わせ`.env`; last30days 自動プローブ`http://localhost:18060`それから`http://host.docker.internal:18060`、または使用して下さい`XIAOHONGSHU_API_BASE`カスタムURLの場合|残り30日APIキー;ローカルブラウザセッションサービスに依存します|
|DripStack(プレミアムファイナンシャルニュースレター)|オプトイン:`--search dripstack`実行中、または`INCLUDE_SOURCES=dripstack`お問い合わせ`.env` |いいえキー;無料公開検索API |
|パープレクシティソナー/検索API/ ディープリサーチ|パープレックスキー、またはSonarフォールバックとしてOpenRouterキー|あなたが行くように支払う|
|ウェブ検索|ブレイブ検索キー|2,000 無料の問い合わせ/月|

### macOS Keychain(オプション)

お問い合わせmacOSシステムにキーを貯えることができますKeychain代わりに`.env`ファイル。 スキルは、最低優先ソースとして自動的にピックアップします。`.env`ファイルとプロセス環境が衝突しても勝ちます。

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

アイテムはサービス名の下に保存されます`last30days-<KEY>`現在のユーザの場合。 非ダーウィンプラットフォームでは、ローダーはノップなので、振る舞いは変化しません。Linux/Windowsユーザー。

すでに異なるキーを持っているKeychainサービス名? 非秘密の設定`LAST30DAYS_KEYCHAIN_ALIASES`記載されているマッピング[CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items)秘密をコピーする代わりに。

お問い合わせ[CONFIGURATION.md](CONFIGURATION.md)完全なパーソースキーマトリクス、推論プロバイダー優先、Web-search バックエンド優先順位。

## コンテンツ

一日中知っておきたい2つのこと:

**研究ファイルが保存されます。**`LAST30DAYS_MEMORY_DIR`デフォルトは`~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`)。 シェル内の任意のパスに var を env する設定、または`--save-dir <path>`実行ごとに。 使用条件`--output <file>`レンダリングされた結果を正確なパスで必要とするとき、選択したフォーマットを使用して`--emit`. 使用`--save-suffix=<name>`同じトピックの複数のバリエーションを別々に保つ(例えば、クライアントごとに)。 詳しくはこちら`--save-dir`実行生成`<slug>-raw[-suffix].md`. 実行`python3 skills/last30days/scripts/last30days.py --preflight`研究の実行前に計画書を見直します。

**エージェントとワークフローの厳しい出力** お問い合わせ`/last30days`機械読みやすいのためJSON安定した、バージョンアップされたエージェントプロファイルを受け取るため。 スクリプトや開発でエンジンを直接使用する場合、実行`python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`;追加`--json-profile=raw`変換されていない内部が必要なときだけ`Report`ダンプ。 詳細はこちら[JSON export field reference and versioning policy](docs/reference/json-export.md).

**トピックなしの発見。** お問い合わせ`/last30days what's trending in AI agents?`すでに知っているトピックを調べる代わりにランクされた発見の簡単な取得 - エージェントホストでは、これは3つのコマンドホスト判断プロトコル(モデル名トピック、ジャンクフィルタ、スコアの適性、およびコンテンツの角度)を実行します。 スクリプトや cron で直接エンジンを使用するには、`python3 skills/last30days/scripts/last30days.py --discover "AI agents"`(一ショット: 決定的なトピック名、角度なし); 追加`--emit=json`バージョン化された発見契約のため。 ディスカバリーは、位置情報と位置情報で相互に排他的です`--drill`.

**走行中の監視を追跡する。** デフォルトモードでは、実行ごとに新しいマークダウンスナップショットが生成されます。 時間をかけて発見を蓄積するために、追加`--store`SQLiteデータベースに永続する[`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py)スケジュールされた操業のために(新しい発見の任意Slack/webhook配達と)[`scripts/briefing.py`](skills/last30days/scripts/briefing.py)毎日/毎週の消化のために。 フル・アカデミー・パターンは[CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**サブスクライブ可能な研究ライブラリ** お問い合わせ`/last30days`ライブラリフィードの構築、または使用`python3 skills/last30days/scripts/last30days.py library feed`スクリプトや開発に直接。 保存されたブリーフをオンにします`index.html`ローカル原子`feed.xml`, 読みやすい簡単なページ. 追加する`--publish`あなたが望むときだけHTML公開は、デフォルトで明示的にオプトインし、公開されます。 Atom フィードをサブスクライブ可能にするには、生成された出力ディレクトリを static ホストにホストします。GitHubサイトマップ

**調査したすべてのものを検索します。** お問い合わせ`/last30days search my library for MCP servers`または`/last30days have I researched MCP servers before?`. 直接エンジンの使用のために、操業して下さい`python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. 検索はオフラインで決定的です: ライブラリフィードによって使用される同じ保存されたブリーフをインクリメンタルにインデックス化し、パーランストアの視覚化とグループの結果をトピックと日付でマージします。 フレッシュランは、現在のトピックをオーバーラップする前の研究では、ライブラリ**セクションから、コンパクトに面しています。`LAST30DAYS_LIBRARY_CONTEXT=off`パッシブコンテキストを無効にします。

Per-client wrapper スクリプト、カスタムカテゴリ-peer subreddits 、および in-progress のカスタマイズのための実験的なベータ チャネルも文書化されます[CONFIGURATION.md](CONFIGURATION.md).

## ショーケース:コミュニティリサーチフィード

過去30日間の再カーリングAIアップデート、マーケットウォッチ、または素晴らしい狭い obsession を公開しましたか? 公開ライブラリ URL または Atom URL をホスティング後に共有する`feed.xml`静的なホストに[the community showcase thread](https://github.com/mvanhorn/last30days-skill/issues/532). コミュニティフィードは、所有者がそれらを提出するようにここにリンクされます。 スレッドは、その間のコレクションポイントです。

## 作品紹介

1. **トピックを入力します。** 人、会社、製品、技術、X対Y。 お問い合わせ
2. **代理人は誰が重要であるかを解決します。** Xハンドル(創始者を含む)を見つけ、GitHubリポジトリ、サブreddits、TikTokハッシュタグYouTubeチャンネル. 「Kanye West」では、r/hiphopheadsを知っています。@kanyewest, と "bully review" 上のYouTube. のために "OpenClaw「openclaw/openclawを解決する」GitHubライブスターカウントをフェッチします。
3. **すべてのソースは並列で検索します。** 多品種の拡大。 エンゲージメント、関連性、鮮度で得た結果
4. **奥深さの誰も持っていません。** スタッフYouTube反応ビデオからのトランスクリプト。 トップページRedditアップ投票数のコメント。TikTokキャプション。Polymarketオッズ。 タイトルやリンクだけではありません。
5. **サメストーリー、マージ。** ワイヤレスフェスティバルが発表されましたReddit, Xで議論, チケットの価格TikTok= 1つのクラスター、3つの独立した項目ではなく。
6. **1つに合成** 特定のデータに基づいた。 ソースによって引用される。 実際に参加する人々によってランク付けされる。 「自分が見つけたもの」ではない。 「こんなこと」です。
7. **専門医になる。** 実行後、Claudeセッションは、コミュニティが知っているすべてのことを知っています。 フォローアップの質問をしてください。 プロンプト、メールのドラフト、計画旅行、アーキテクトシステム - 今のところ何が本物にすべて基づかせていました。

## 人が言うこと

> 「私は見つけましたClaude Codeあらゆるトピックを研究するスキルReddit、 X、YouTube過去30日間のHNとHN。 それからあなたのためのプロンプトを書きます。 私は手動で検索してきたRedditそしてXは、私が書いたすべてのコンテンツの前に研究のために。 タブでタブします。 糸による糸。 90分かかる部分です。 これはそれを排除します。」 -@itsjasonai

> 「この1つのスキルは、研究ワークフロー全体を置き換えました。 あなたはそれをトピックを与える、それはスクレイピングReddit、X、そして実際に話している人のためのウェブ。 古いブログ投稿はありません。 過去30日間のリアルタイム会話。 -@itswilsoncharles

> 「10のトレンドレポの5」GitHub今日はClaudeツール。 #1: マバンホーン/last30days-スキル -@yieldhunter95

## オープンソース

MITライセンス 追跡無し。 分析なし。 あなたの研究はあなたの機械にとどまります。 2,700以上のテスト

組み込みPython3.12+、yt-dlp、Node.js(ベンダー)BirdX検索のクライアントScrapeCreators API. v3エンジンアーキテクチャ[@j-sperling](https://github.com/j-sperling).

お問い合わせ[CONTRIBUTING.md](CONTRIBUTING.md)開くPR, [CONTRIBUTORS.md](CONTRIBUTORS.md)コミュニティコントリビューターの完全なリストのために、[CHANGELOG.md](CHANGELOG.md)バージョン履歴のため。

## 星の歴史

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
