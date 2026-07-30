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

**編集者ではなく、アップボート、いいね、リアルマネーで評価されるAIエージェント主導の検索エンジン。**

このREADMEは現在のv3パイプラインを追跡しています。ランタイムスキル仕様は [skills/last30days/SKILL.md](skills/last30days/SKILL.md)に存在し、これが最新のコマンドおよびセットアップ動作の真実の源です。

**Claude Code (推奨 — マーケットプレイス経由の自動更新):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex、 Cursor、 Copilot、 Gemini CLI、または50+ [Agent Skills](https://agentskills.io) ホストのいずれか:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` ユーザーのグローバルにインストールされ、すべてのプロジェクトで利用可能です。プロジェクトごとにスコープに切り替えてください。)

claude.ai ウェブ、 OpenClaw、マニュアルなどのインストールオプションは下の [Install](#設置) セクションにあります。

設定はゼロです。 Reddit、HN、 Polymarket、 GitHub はすぐに動作します。一度実行すると、セットアップウィザードが30秒で X、 YouTube、 TikTok、 arXiv、 Techmemeなどをアンロックします。

---

Reddit アップボート。 X いいね。トランスクリプト YouTube 。関与 TikTok 。実際のお金と内部情報に裏付けられた Polymarket オッズ。毎日何百万人もの人々が自分の注意と財布を使って投票しているのです。 /last30days それらすべてを並行して検索し、実際に人々が関わっているもので評価し、AIエージェントの審査員がそれを一つのブリーフにまとめます。

Google 編集者を集約します。 /last30days 人を検索します。

この検索は他のどこでも得られません。なぜなら、単一のAIがすべてにアクセスできるわけではないからです。Google検索はコメントやX投稿Reddit触れません。ChatGPTRedditと契約していますが、XやTikTokは検索できません。GeminiはYouTubeはありますが、Redditはありません。Claudeはネイティブにそれらのどれも持っていません。各プラットフォームは独自のAPI、トークン、認証を持つ囲い庭園のようなものです。しかし、自分のキーやブラウザセッションを持ち込めば、AIエージェントがそれらを一度に検索し、互いにスコアリングし、何が本当に重要かを教えてくれます。

それがアンロックだ。これ以上の検索エンジンは一つもない。12の切り離されたプラットフォームがエージェントでつながっている。

```
/last30days Peter Steinberger
```

明日会議がある。彼らをGoogle。2023年の彼らのLinkedInを手に入れる。/last30days今月実際にやっていることを教えてくれます:Codexに関わるためにOpenAIに参加し、Anthropicのサードパーティエージェント禁止に反対し、23PRsを85%のマージ率で出荷し、クロスデバイスエージェント制御のための「LobsterOS」を構築し、r/Claudeコードでは彼がヒーローか「耐え難い」かで569のアップボートを獲得しました。X投稿、Redditスレッド、YouTubeのトランスクリプト、そしてGitHubコミットに散らばっています。どれも入っていなかったGoogle。

## なぜこれが存在するのか

AIに追いつくために作りました。すべてが日々変わり、 Reddit や X オタクたちが常に最初に対応しています。より良いプロンプトが必要で、トレーニングデータはコミュニティがすでに把握しているものより常に数ヶ月遅れていました。

しかし、それはもっと大きなものに発展しました。今では営業電話の前に、ビジネスの過去30日間の真実を知るために使っています。会議の前に誰かの最近のツイートやポッドキャストの書き起こしを読む前に。 Disney World 旅行の前に、どのライドが閉鎖されているか、コミュニティが Genie+について何を言っているかを知るために。何かを作る前に、人々が実際にどんな問題に直面しているのかを知るために。

CEOと会う場合、過去30日間のツイートや議事録 YouTube 全部読んだ?読んだよ。

## 情報源は人々によって評価されました

| 出典 | 人々の言うこと |
|--------|--------------------------|
| **Reddit** | フィルターなしの意見。実際にアップボート数がつくトップコメント、無料、 API キーなし。 Google が埋めている本当の意見。 |
| **X / Twitter** | 熱い意見、専門家のスレッド、そして激しい反応。最初に知り、最初に議論する。 |
| **YouTube** | 45分間の深掘り。重要な引用可能な5文の全文を検索しました。 |
| **TikTok** | クリエイターは360万人にリーチし、 Googleでは決して得られないような視点を持っています。 |
| **Instagram Reels** | スポークンワードの書き起こしによるインフルエンサーの視点。ビジュアルカルチャーのシグナル。 |
| **Hacker News** | 開発者の合意。825ポイント、899件のコメント。技術者たちが実際に議論している場面。 |
| **Polymarket** | 意見じゃない。確率だ。本物の資金に裏付けられている。アルバム売上に96%の信頼。買収に4%の信頼。 |
| **GitHub** | 人向けに: PR Velocity、スターによるトップリポジトリ、リリースノート。トピック:問題や議論。 |
| **Digg** | DiggのAI 1000リーダーボード(Xで約1000のハイシグナルAIアカウント)からキュレーションされたストーリークラスターで、帰属可能なインライン引用付き(X認証不要)。`digg-pp-cli`がPATHオン時は自動有効化。 |
| **arXiv** | 話題の裏にある論文。ウィンドウに新しいリサーチが表示され、無料、APIキーなし。PATH`arxiv-pp-cli`中は自動有効化(初回セットアップでインストール)。 |
| **Techmeme** | テックニュース編集レイヤーで、30日以内の日付ウィンドウが表示されます。無料で API キーなし。 `techmeme-pp-cli` が PATH 上にあると自動で有効化されます(初回セットアップでインストールされます)。 |
| **LinkedIn** | プロフェッショナルシグナル。投稿や記事、記事は高シグナルとして重み付けされています。 |
| **StockTwits** | トレーダーのセンチメント。トピックがティッカーや暗号通貨の場合、自動で活性化されます。 |
| **Threads** | Twitter後のテキスト層。クリエイターやブランドからの会話。 |
| **Pinterest** | ビジュアルディスカバリー。製品やアイデアをピン、保存、コメント。 |
| **Xiaohongshu (RED)** | 中国のライフスタイル、製品、クリエイターのシグナル。ログイン済みのx-mcpブラウザプラグインや`xiaohongshu-mcp`サービスがローカルで動作している場合、`--search xhs`に明示的にリクエストされます。 |
| **Bluesky** | 分散型ソーシャルレイヤー。ATプロトコルはTwitter移行後の投稿です。 |
| **Perplexity** | グラウンデッドソナー合成、生のサーチ API ロウ、そしてディープリサーチ。 |
| **Web** | 編集報道やブログの比較。多くの兆候の一つであり、唯一のものではありません。 |

コミュニティの貢献者たちが次々と追加しています。 Truth Social やその他のニッチなソースもエンジンに含まれており、今後も追加予定です。

1,500アップボートの Reddit スレッドは、誰も読まないブログ記事よりも強いシグナルです。360万回の閲覧数を持つ TikTok は、プレスリリースよりも文化的に重要なことをよく伝えます。66,000ドルのボリュームに裏付けられた Polymarket 確率は、評論家の推測よりも議論しにくいです。

総合は、実際の人々が実際に関わったものに基づいてランク付けされます。社会的関連性であり、 SEO 関連性ではありません。

## 実際に人々が使う用途

**会議前に。** `/last30days Peter Steinberger` - OpenAIの Codex チームに加わり、Anthropicのサードパーティエージェント禁止に反対し、23 PRs が85%の合併率で GitHubに統合され、デバイス間エージェント制御のための統合 LobsterOS を構築しました。r/ClaudeCode:「 OpenClaw リリース以来、 API以外の手段で処理するといずれBANされるというのは広く知られていました」(227アップボート)。これは LinkedInではありません。

**採用シグナルを読み取るため。** `/last30days Listen Labs --hiring-signals` - 現在の求人やキャリアページは、企業のセキュリティ、カスタマーサクセス、インフラ、製品拡大など、焦点の変化の証拠として引用されます。レポートは採用が示唆しているように見えるものを述べており、ロードマップが何を提供するかを示しています。

**ピーク前にトピックを見つけるために。** `/last30days what's exploding in AI agents?`に尋ねるとスキルは発見モードに切り替わります:エンジンはカテゴリーリストReddit、表出し/ベストストーリー、DiggのAI 1000フィード、認証時にXHacker Newsします。エージェントがノミネーション(名前、ジャンクフィルタリング、コンテンツの価値度)を審査し、ポッドキャストやX記事の角度を書きます。その後、5〜10件のVelocityランクのトピックが得られます。すべての結果にはクロスソースの数値、モメンタムラベル、そしてすぐに実行可能な`/last30days "<topic>"`フォローアップが含まれています。

**何かが落ちるとき。** `/last30days Kanye West` - イギリスは彼のビザをブロックし、ワイヤレスフェスティバルは中止、スポンサーは逃げた。しかしBULLYはビルボードで#2をデビューさせた。ファンタノは「イェイ・サバティカル」から戻ってレビューを続けた(65万3千回)。SoFi Homecomingはローリン・ヒルとトラヴィス・スコットを招き、44曲を披露した。 Polymarket:「カニエはまたツイートするのか?」86%はイエス。23件の Reddit スレッド、17本の YouTube 動画、8万6千件のアップボート。

**ツールを比較するため。** `/last30days OpenClaw vs Hermes vs Paperclip` - 「これらは競合ではなく層だ。」 OpenClaw は執行者(351, GitHub 0星、ライブ)、ヘルメスは自己改善の脳(31,0星)、ペーパークリップは組織図(49,000星)。星の数はライブで取得されたもので、古びたブログ投稿ではなく GitHub APIから抽出される。建築、メモリ、セキュリティを備えた並列テーブル、ベストフォース。 @IMJustinBrookeによると:「OpenClaw =ヒトカゲ、ヘルメス=リザードン。」

**世界を理解するために。** `/last30days Iran vs USA` - 戦争38日目。トランプ大統領がイランにホルムズ海峡を再開させる火曜日の期限を掲げた。米軍機2機が撃墜された。石油は1バレルあたり126ドル。IEAはこれを「世界石油市場史上最大の供給混乱」と呼んだ。 Polymarket:12月31日までに停戦が74%で達成。27件の X 投稿、10本の YouTube 動画、20の予測市場。

**旅行の前に。** `/last30days Universal Epic Universe` - 拡張工事がすでに進行中。「プロジェクト680」許可申請済み。インフラで花火大会が確認されたが、予告なし。待ち時間:鉱山カートの狂気、平均148分。まだ年間パスはなく、地元の人々は不満を抱いている。スターダストレーサーズは4月5日まで改装中。

**何かを素早く学ぶために。** `/last30days Nano Banana Pro prompting` - JSON構造化プロンプトがタグスープに取って代わっています。 @pictsbyaiのネスト形式は「コンセプトの漏れ」を防ぎます。編集優先のワークフローは再生よりも優れています。そしてコミュニティが「うまくいく」と言った方法で、まさに本番プロンプトを書いてくれます。

## 新しいこと

5月のv3.3発表以降、v3.11.1(2026年7月)時点で、175件の統合 PRs 、そのうち122件は52人のコミュニティ貢献者から15のリリースにわたり統合されました。これが実現したものです。

### ファーストクラス OpenAI Codex

/last30days現在はネイティブの Codex プラグインで、ガイド付きセットアップが可能で、ポートではなく一流の市民です。レンダラー対応の引用により、Codex出力はURLスープではなくブリーフのように読み取れます(#694)、同じエンジンはClaude Code、Cursor、Copilot、Gemini CLI、Claude Desktop、OpenClaw、50+ Agent Skillsホストで動作します。プラグインマニフェストCodex[@rfoust](https://github.com/rfoust)(#686)、認証修正は[@tmchow](https://github.com/tmchow)(#698)によってCodexされています。

### arXiv、 Techmeme、 Digg - 無料、 API キーなし

arXiv 新聞を宣伝の後ろに引き込み、 Techmeme は編集技術ニュース層をもたらします。無料、ゼロキー、初回セットアップが CLIをインストールして自動的に有効化します(#709)。 DiggのAI 1000ストーリークラスターも同様の方法で X 認証なしに届きます。セットアップが無料 Digg CLI をインストールしてくれます(#590)。 Trustpilot 消費者ブランドリサーチのオプトインを出荷します。

### 無料 Reddit は実際のスコアとトップコメントを増やしました

Redditの公開.json API は死んだ。フリーパスはより強く復活した。キーレスRSS+シュレディットスクレイピング(#457)、arctic-shiftによる本当のアップボート数を持つ専用サブレディット発見(#696)、そしてバイラルなオフトピック投稿がブリーフを乗っ取らない関連性フロア(#488、ありがとう [@rzachsmith](https://github.com/rzachsmith)))。キーは API なし。リアルスコア。トップコメントも含まれている。

### すべてのブリーフに最高のコメントが

コメントは現在、ソース全体でデフォルトレイヤーとなっています。Instagramのコメントはランクベースの多様性で、5つのホットテイクが1つの投稿からすべて出てこないようにしています(#751)、 YouTube コメントと ScrapeCreators のトランスクリプトバックアップ(#637)、そしてコミュニティの最も面白いセリフがスコアリングに残るようにクラウド投票によるコメントは Best Takes に重み付けされています(#592、#608)。

### 1人のドクター指令

健康診断を依頼すれば、医師はあらゆる情報源を調べ、どのキーが欠けているか、どの CLI が PATHか、どのクッキーが期限切れか(#753)と正確な修正を処方します。なぜ X が薄くなったのか、もう推測する必要はありません。

### X 捜索、再建

Xパイプラインは一から刷新されました。FROMレーンとABOUTレーンが導入され、本人の投稿とそれに関する会話が両方にランク付けされる(#610)、パーソンウェアなサブクエリの曖昧さ回避(#611)、インタラクション・シグナルランキングによるファーストパーティ著作権のグラウンディング(#613)、そして自動バックエンドフェイルオーバーを備えた単一のXソース(#622)。さらに、認証を実際にプローブする正直な`--diagnose`(#609)。

### さらに多くの情報源が加わりました

LinkedInScrapeCreatorsを通じて、高信号の記事([@ravstr](https://github.com/ravstr)、#702)を掲載しています。StockTwitsティッカーや暗号通貨のトピックに対して自動でアクティベートされます([@wtiwana](https://github.com/wtiwana)、#658)。PerplexityダイレクトAPIモードと非同期のディープリサーチ([@sk-holmes](https://github.com/sk-holmes)、#629)を拡大しました。

### コミュニティによって鍛えられる

セキュリティの波はほぼ完全にコミュニティの作業によるものでした。 HTML レンダラーのストアドXSS修正([@iliaal](https://github.com/iliaal)、 [@aaronjmars](https://github.com/aaronjmars))、ロックダウンされたクッキー一時ファイル、OpenSSFスコアカードとビルドの出所証明([@shaanmajid](https://github.com/shaanmajid)、 [@hammadxcm](https://github.com/hammadxcm)、 [@aniruddh909](https://github.com/aniruddh909))を用いたサプライチェーン強化CI、SemgrepおよびOSV-Scannerスキャン、 PR 依存関係レビューゲート([@23241a6749](https://github.com/23241a6749))、60%で導入され現在84%に引き上げられたテストカバレッジの最低限([@gourab5139014](https://github.com/gourab5139014))、そしてすべての重要な発見を除去したHermesのセキュリティスキャン(#768)。

### さらに遠くへ

ヘブライ語および非ラテン語([@dudyme](https://github.com/dudyme))。CJK中国語ソースの認識トークン化([@An-idd](https://github.com/An-idd))。Windows互換性の波。Chromiumファミリー全体(Brave、Edge、Vivaldi、Opera、Arc([@andrey-esipov](https://github.com/andrey-esipov))にわたるクッキー抽出、さらに認証情報ソースmacOS Keychainとpass(1)Linux。`--as-of`過去の遡及([@chiyi-creator](https://github.com/chiyi-creator))。uv([@buntysomroy](https://github.com/buntysomroy))を経由Python3.12で自動プロビジョニング。`--hiring-signals`企業の求人ページを読むためのもの。実行間のウォッチリストの差。

### まだv3のまま箱の中です

v3の基盤はすべてまだ存在しています:単一の API コールが発信する前に適切なハンドルネーム、サブレディット、ハッシュタグを解析する事前研究の脳( [@j-sperling](https://github.com/j-sperling)が構築);ユーモアやバイラル性の Best Takes スコアリングと関連性を並行して評価するもの;ソース間のクラスター統合;シングルパス比較(「CLI vs MCP」を3分で行う、12分ではなく;自動発見 `--competitors` 比較; GitHub パーソンモード(`--github-user=steipete`); ELI5 モード("実行後に"eli5オン");そして共有可能で自己完結型の HTML ブリーフ(`--emit=html`)。設定ノブは [CONFIGURATION.md](CONFIGURATION.md)に存在します。

## 設置

| 表面 | 設置 | アップデート |
|---------|---------|---------|
| **Claude Code**(推奨) | `/plugin marketplace add mvanhorn/last30days-skill` | マーケットプレイス経由で自動更新、または `claude plugin update last30days@last30days-skill` |
| **Grok**(xAIビルド CLI) | `grok plugin marketplace add mvanhorn/last30days-skill``grok plugin install last30days` | `grok plugin update last30days` |
| **Codex、 Cursor、 Copilot、 Gemini CLI、または50+のホストのいずれか [Agent Skills](https://agentskills.io)** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai**(ウェブ) | [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) ・アップロード claude.ai > カスタマイズ > スキル > + > スキル作成 > スキルをアップロード | 再ダウンロードと再アップロード |
| **Claude Desktop** | [Download the `.mcpb` for your platform](https://github.com/mvanhorn/last30days-skill/releases/latest) して設定>拡張機能にドラッグします | 新しいバンドルを再ダウンロードしてドラッグしてください |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (推奨)

```
/plugin marketplace add mvanhorn/last30days-skill
```

おすすめです。なぜなら Claude Code マーケットプレイスがアップデートを代行してくれるからです。プラグインキャッシュはバージョン管理されており、新しいリリースが公開されると自動的にリフレッシュされます。 `claude plugin update last30days@last30days-skill` を実行してチェックを強制してください。

Claude Codeでエージェントスキルインストールパスを使いたい場合は、こちらもサポートしています:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

ネイティブプラグインと `npx skills` インストールは共存可能です。 Claude Code インストール方法間での重複除去はしないことに注意してください。マーケットプレイスプラグインと `npx skills` コピーの両方が有効 `/last30days` 、2つのエントリが表示されます。1台のマシンごとに1つのインストール方法を使いましょう。

### Grok (xAIビルド CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`)インストールはネイティブプラグインとして30日間有効です。直接インストールはリポジトリを追跡します:

```bash
grok plugin install mvanhorn/last30days-skill
```

または、このリポジトリをマーケットプレイスのソースとして追加し、プラグイン名でインストールする方法もあります:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

インストール確認をスキップするために `--trust` を追加してください。 `grok plugin update last30days`でアップデートします。 Grok 互換性のために Claude Code マニフェストも読みます。ネイティブ `.grok-plugin/` ペアがファーストクラスレーン(公式 [xAI marketplace](https://github.com/xai-org/plugin-marketplace) リストが示している通りです)。 `npx skills add` 有効なクロスホストのフォールバックとして機能しています。

### Codex、 Cursor、 Copilot、 Gemini CLI、その他の Agent Skills ホスト

オープン [Agent Skills](https://agentskills.io) CLI 経由でインストール — `codex`、 `cursor`、 `github-copilot`、 `gemini-cli`、 `claude-code`、 `windsurf`、 `cline`、 `continue`、 `roo`、 `aider-desk`、 `opencode`、 `goose`など50+ハーネスに対応しています( [vercel-labs/skills repo](https://github.com/vercel-labs/skills)に全リスト)。

```bash
npx skills add mvanhorn/last30days-skill -g
```

`-g`(グローバル)フラグはユーザーディレクトリにインストールされるため、スキルはすべてのプロジェクトで利用可能です。`-g`がなければ、`npx skills`プロジェクトローカルに`./.skills/`にインストールします(リポジトリとコミットしています)。世界を調査するツールとしては、グローバルが最適です。

Codex デスクトップやその他のフォルダモードのホストは、通常のフォルダでもGitリポジトリでも動作します。最初の調査を行う前に、ホストエージェントにロードされたスキルディレクトリからバンドルされた `scripts/last30days.py --preflight` を実行するよう依頼してください。ソースチェックアウトでは、同等のコマンドは `python3 skills/last30days/scripts/last30days.py --preflight`です。これは設定ソース、ブラウザクッキー計画、計画された書き込み、オプションコマンド、無視されたプロジェクト設定をクッキーの読み取り、書き込み、リサーチを行わずに表示されます。

デフォルトでは、 `npx skills` 検出したハーネスに対してこの仕組みがインストールされます。特定のハーネス(または複数)をターゲットにするには:

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

後日更新:

```bash
npx skills update last30days -g
```

または、 `npx skills`を通じてグローバルにインストールしたすべてのものを更新してください:

```bash
npx skills update -g
```

リストして `npx skills list -g` と `npx skills remove last30days -g`で削除してください。

### claude.ai(ウェブ)

1. 最新リリースからの[Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill)
2. [claude.ai > Customize > Skills](https://claude.ai/customize/skills)に行って
3. スキルパネルの `+` ボタンをクリックし> `Create skill` > `Upload a skill` をクリックしてファイルを閲覧・ドロップしてください

まずは「Capabilities」で「コード実行とファイル作成」を有効にしてください。スキルはそれなしでは動作しません。

### Claude Desktop

Claude Desktop`.mcpb`バンドル(ワンクリックのModel Context Protocolパッケージ)を通じて`/last30days`をMCPサーバーとしてインストールします。

1. [latest release](https://github.com/mvanhorn/last30days-skill/releases/latest)にアクセスし、あなたのプラットフォームの`.mcpb`をダウンロードしてください:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS インテル: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Claude Desktopを開き、拡張機能>設定に行き、ファイルをドラッグします。
3. 促されたら、有効にしたいソースのキーを API 貼り付けます。すべてのフィールドは任意で、すべてスキップするとエンジンはウェブ専用モードに劣化します。キーはOSのキーチェーンに保存されます。
4. Claude Desktopを再起動してください。Claudeに「ピーター・スタインバーガーを調べて」など、どのトピックでも「リサーチ」と頼むと、`research`ツールを呼び出します。

**ホスト要件:** PATHPython 3.12+。バンドルはエンジンソースを出荷しますが、ローカルのPythonインタプリタを使用します。Windows[python.org](https://www.python.org/downloads/)からインストールしてください;macOSおよびほとんどのLinuxディストリビューションは互換性のあるバージョンを出荷します。

**キーはコードスキルと同期しません。** Claude Desktop と Claude Code は設計上別々の認証情報ストアを維持しています。コードスキルですでに `~/.config/last30days/.env` を設定している場合、同じキーをここで一度再入力します。

Windows サポートはプラットフォームごとのマニフェストエントリポイントが解決されるまで延期されます。追跡はフォローアップの問題で行われます。

### OpenClaw

```bash
clawhub install last30days-official
```

X/Twitter のアクションワークフロー、例えば`/last30days`研究以外の作業、例えば投稿
ツイートや返信、フォロワーのエクスポート、メディアの管理、モニター、プレゼント企画
ドローを使い、 [TweetClaw](https://github.com/Xquik-dev/tweetclaw) を仲間として使う
OpenClaw プラグインです。 TweetClaw はXquik-devによって管理されており、
オプションの伴侶パスであり、最後の30日間の依存や承認ではありません。

### マニュアル(開発者)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

シンムリンクは編集中にインストールを作業ツリーと同期させ、再コピーは不要です。 `claude.ai`はソースから `.skill` ファイルをビルドします。 `bash skills/last30days/scripts/build-skill.sh` `dist/last30days.skill`を生成します。

Reddit (コメント付き)、 Hacker News、 Polymarket、 GitHub が即座に動作します。設定はゼロです。一度 `/last30days` 実行すると、セットアップウィザードが30秒でさらに多くのソースをアンロックし、無料の arXiv や Techmeme CLIsも含まれます。

## 自分の鍵を持ってきてください

これらのプラットフォーム同士には関係性がありません。 X は Reddit がどう思っているか分かりません。 YouTube はそれを見 TikTok。しかし、自分の API キーやブラウザトークンを持っていけば、突然それらすべてに一度にアクセスできるようになります。

| 出典 | 必要なもの | 費用 |
|---------|---------------|------|
| Reddit (コメント付き)+ HN + Polymarket + GitHub + StockTwits | 何も | 無料 |
| arXiv + Techmeme | 初開セットアップで自動インストールされる無料CLI | 無料 |
| X / Twitter | どのブラウザでも x.com にログインするか、`XQUIK_API_KEY`/`XAI_API_KEY`設定してください。 | ブラウザクッキーは無料です。キーはプロバイダーごとに異なります |
| YouTube | `brew install yt-dlp` | 無料 |
| Bluesky | アプリパスワードは bsky.app から | 無料 |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube コメント | ScrapeCreators キー | 1万件の無料通話、そしてPAYG |
| Xiaohongshu (RED) | ログインしたx-mcpブラウザプラグインや`xiaohongshu-mcp`サービスを実行し、`.env`で`--search xhs`の`INCLUDE_SOURCES=xiaohongshu`でオプトインしてください;最後に30日の自動プローブ`http://localhost:18060`、その後`http://host.docker.internal:18060`、またはカスタムURLとして`XIAOHONGSHU_API_BASE`を使う | last30daysの API キーはありません。ローカルのブラウザセッションサービスによります |
| DripStack(プレミアム金融ニュースレター) | オプトイン:1回のランに`--search dripstack`、または`INCLUDE_SOURCES=dripstack``.env` | 鍵なし;無料の公開検索 API |
| Perplexity ソナー / 捜索 API / 深層調査 | Perplexity キー、またはOpenRouterキーをSonarのフォールバックとして使います | 使い分ずつ支払ってください |
| Web 検索 | Brave Searchキー | 月間2,000件の無料クエリ |

### macOS Keychain (任意)

macOSでは、`.env`ファイルではなくシステムKeychainにキーを保存できます。スキルは自動的にキーを最も優先度の低いソースとして取得します。`.env`ファイルやプロセス環境は衝突時に依然として優勢です。

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

アイテムは現在のユーザーのためにサービス名 `last30days-<KEY>` に保存されます。ダーウィン以外のプラットフォームではローダーはノーオペレーターであるため、 Linux/Windows ユーザーの挙動変更はありません。

すでに異なるKeychainサービス名でキーを持っていますか?秘密をコピーする代わりに、[CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items)で説明された非秘密の`LAST30DAYS_KEYCHAIN_ALIASES`マッピングを設定しましょう。

ソースごとの鍵マトリックス、推論提供者の優先順位、ウェブ検索のバックエンド優先度については、詳細 [CONFIGURATION.md](CONFIGURATION.md) を参照してください。

## 構成

初日に知っておくべきことが2つあります:

**研究ファイルが保存される場所。** `LAST30DAYS_MEMORY_DIR`デフォルトは`~/Documents/Last30Days/`(Windows: `C:\Users\<you>\Documents\Last30Days\`)。そのenv varをシェル内の任意のパスに設定し、または1回の実行ごとに`--save-dir <path>`設定してオーバーライドしてください。正確なパスでレンダリング結果が必要な場合は、`--emit`が選択したフォーマットで`--output <file>`を使います。`--save-suffix=<name>`を使って同じトピックの複数のバリエーションを別々に管理してください(例:クライアントごと)。各`--save-dir`実行は`<slug>-raw[-suffix].md`を生成します。研究実行前に計画された書き込みを確認するために`python3 skills/last30days/scripts/last30days.py --preflight`実行してください。

**エージェントおよびワークフロー向けの構造化出力。** 安定したバージョン管理されたエージェントプロファイルを受け取るための機械可読JSONを`/last30days`に求めてください。スクリプトや開発で直接エンジンを使う場合は、`python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`実行してください;バージョンのない内部`Report`ダンプが必要な時のみ`--json-profile=raw`追加してください。[JSON export field reference and versioning policy](docs/reference/json-export.md)を参照してください。

**トピックレスディスカバリー。** 既知のトピックを調べる代わりにランク付けされたディスカバリーブリーフを取得 `/last30days what's trending in AI agents?` を求めてください。エージェントホストでは、3コマンドのホストジャッジドプロトコルを実行します(モデルがトピック名、ジャンクをフィルタリングし、価値をスコアリングし、コンテンツの角度を書きます)。スクリプトやクロンで直接エンジンで使う場合は、 `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` を実行します(ワンショット:決定的なトピック名、アングルなし);バージョン付きディスカバリー契約のために、 `--emit=json` を追加します。ディスカバリーはポジショントピックと相互排他的であり、 `--drill`。

**実行をまたぐ傾向監視。** デフォルトモードでは1回の実行ごとに新しいマークダウンスナップショットが生成されます。時間をかけて発見を蓄積するには、SQLiteデータベースに永続化する `--store` を追加し、スケジュールされた実行には [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) (新しい発見にはオプションでSlackやwebhookの配信も可能)、日次・週次ダイジェストには日次 [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) を使います。完全なカデンスパターンは [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings)にあります。

**購読可能な研究図書館です。** `/last30days`に図書館フィードを作成してもらうか、スクリプト作成や開発に直接使う`python3 skills/last30days/scripts/last30days.py library feed`をください。保存されたブリーフを`index.html`、ローカルのAtom `feed.xml`、読みやすいブリーフページに変換します。HTMLインデックスやブリーフページをホストしたい場合にのみ追加`--publish`;公開は明示的にオプトインし、デフォルトで公開されます。Atomフィードを購読可能にするには、生成された出力ディレクトリをGitHub Pagesのような静的ホストでホストしてください。

**調べたものをすべて検索してください。** `/last30days search my library for MCP servers` または質問 `/last30days have I researched MCP servers before?`。直接エンジン使用の場合は `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`を実行します。検索はオフラインかつ決定論的です。図書館フィードで使われる同じ保存ブリーフを段階的にインデックス化し、各ランごとに一致する店舗の目撃情報を統合し、トピックや日付で結果をグループ化します。新規の検索では、先行研究が現在のトピックと重複する場合、コンパクトな**あなたの図書館から**セクションが表示されます。その受動的なコンテキストを無効に `LAST30DAYS_LIBRARY_CONTEXT=off` 設定してください。

クライアントごとのラッパースクリプト、カスタムカテゴリピアサブレディット、進行中のカスタマイズのための実験的ベータチャンネルも [CONFIGURATION.md](CONFIGURATION.md)でドキュメント化されています。

## 紹介:コミュニティリサーチフィード

定期的なAIアップデート、市場観察、またはlast30daysへの非常に狭い執着を公開した?公共図書館のURL、または静的ホストで `feed.xml` ホスティングした後のAtomのURLを共有 [the community showcase thread](https://github.com/mvanhorn/last30days-skill/issues/532)。コミュニティフィードは所有者が投稿する際にここにリンクされます。スレッドがその間、収集の拠点となっています。

## 仕組み

1. **トピックをタイプします。** 人物、会社、製品、技術、「X vs Y」何でもいい。
2. **エージェントが重要な人物を決定します。** Xハンドル(創業者を含む)、リポジトリ、サブレディット、TikTokハッシュタグ、YouTubeチャンネルGitHubを見つけます。「Kanye West」についてはr/hiphopheads、@kanyewest、そしてYouTubeの「bully review」を知っています。「OpenClaw」ではopenclaw/openclawをGitHubで解決し、ライブのスター数を取得します。
3. **すべてのソースを並行検索。** 複数クエリの拡張。結果はエンゲージメント、関連性、新鮮度で評価されます。
4. **他の誰にもない深み。**リアクション動画の全 YouTube トランスクリプト。アップボート数を含むトップ Reddit コメント。 TikTok キャプション。 Polymarket 確率。タイトルやリンクだけじゃない。
5. **同じ話、統合済み。**ワイヤレスフェスティバルは Reddit日に発表され、 Xで議論、 TikTok のチケット価格は3つの別々の項目ではなく1つのクラスターです。
6. **まとめて一つのブリーフにまとめた。** 具体的なデータに根ざしている。出典ごとに引用。人々が実際に関わるもので順位付け。「これが私の発見だ」ではなく「これが重要なことだ」ということだ。
7. **そうすればあなたの専門家になります。** 一度の実行で、あなたの Claude セッションはコミュニティが知っているすべてを知っています。フォローアップの質問をしてください。プロンプトを書かせ、メールを下書きし、旅行計画を立て、アーキテクトシステムを計画し、すべて今の現実に基づいています。

## 人々が言っていること

> 「過去30日間の44Reddit、X、YouTube、HNのあらゆるトピックを調べるClaude Codeスキルを見つけました。そして、あなたのためにプロンプトを書きます。私は毎回のコンテンツを書く前に、RedditやXを手動で調査しています。タブごとに。スレッドごとに。そこが90分かかる部分です。これでその部分はなくなります。」-@itsjasonai

> 「この一つのスキルが、私の研究ワークフロー全体を置き換えました。トピックを与えると、実際に人々が話していることを Reddit、 X、ウェブからスクレイピングします。古いブログ記事ではありません。過去30日間の本当の会話です。」-@itswilsoncharles

> 「今日 GitHub でトレンド入りしている10のリポジトリのうち5つは Claude ツールです。#1:Mvanhorn/last30days-スキル」 -@yieldhunter95

## オープンソース

MITライセンス。追跡なし。分析なし。研究はあなたのマシンに残る。2,700+テスト。

Python 3.12+、yt-dlp、Node.js(X検索用にベンダーBirdクライアント)、およびScrapeCreators API[@j-sperling](https://github.com/j-sperling)によるv3エンジンアーキテクチャで構築されています。

PRを開くには[CONTRIBUTING.md](CONTRIBUTING.md)、コミュニティ貢献者の全リストは「[CONTRIBUTORS.md](CONTRIBUTORS.md)」、バージョン履歴は「[CHANGELOG.md](CHANGELOG.md)」を参照してください。

## スターの歴史

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
