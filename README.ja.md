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

**編集者ではなく、アップボート・いいね・実際に動いたお金でランク付けする、AIエージェント主導の検索エンジンです。**

このREADMEは現行のv3パイプラインについて説明しています。実行時のスキル仕様は [skills/last30days/SKILL.md](skills/last30days/SKILL.md) にあり、コマンドとセットアップの挙動についてはそちらが最新かつ正式なものです。

**Claude Code(推奨 — マーケットプレイス経由で自動更新):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex、Cursor、Copilot、Gemini CLI、その他50以上の [Agent Skills](https://agentskills.io) ホスト:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` を付けるとユーザー単位でグローバルにインストールされ、すべてのプロジェクトで使えます。プロジェクト単位に限定したい場合はこのフラグを外してください。)

その他のインストール方法(claude.aiのウェブ版、OpenClaw、手動)は下の [インストール](#インストール) セクションにあります。

設定は不要です。Reddit、HN、Polymarket、GitHub はすぐに使えます。一度実行すれば、セットアップウィザードが30秒で X、YouTube、TikTok、arXiv、Techmeme などを有効にします。

---

Reddit のアップボート。X のいいね。YouTube の文字起こし。TikTok のエンゲージメント。実際のお金とインサイダー情報に裏打ちされた Polymarket のオッズ。つまり、毎日何百万人もの人が自分の注意と財布で投票しているということです。/last30days はそのすべてを並行して検索し、実際に人々が反応したかどうかでスコアを付け、AIエージェントが判定役となって1本のブリーフにまとめます。

Google は編集者を束ねます。/last30days は人を検索します。

この検索は他のどこでも手に入りません。単独のAIがすべてにアクセスできないからです。Google の検索は Reddit のコメントにも X の投稿にも届きません。ChatGPT は Reddit と提携していますが、X も TikTok も検索できません。Gemini には YouTube がありますが Reddit がありません。Claude はそのどれもネイティブには持っていません。どのプラットフォームも、独自のAPI・独自のトークン・独自の認証を備えた閉じた庭です。しかし自分のキーとブラウザセッションを持ち込めば、AIエージェントが一度にすべてを検索し、互いに突き合わせてスコアを付け、本当に重要なことを教えてくれるようになります。

そこが突破口です。優れた検索エンジンが1つ増えるという話ではありません。断絶していた十数のプラットフォームを、エージェントが橋渡しするのです。

```
/last30days Peter Steinberger
```

明日、打ち合わせがあるとします。その人を Google で調べると、出てくるのは2023年の LinkedIn です。/last30days なら、その人が今月実際にやっていることが分かります。Codex に取り組むため OpenAI に参加し、サードパーティ製エージェントを禁じた Anthropic の方針と争い、23本のPRをマージ率85%で送り、デバイスをまたいでエージェントを操作する「LobsterOS」を作っていて、さらに r/ClaudeCode では彼が英雄なのか「鼻につく」のかという議論が569アップボートを集めている。それらは X の投稿、Reddit のスレッド、YouTube の文字起こし、GitHub のコミットに散らばっていて、どれも Google には出てきませんでした。

## なぜ作ったのか

AIの動きに追いつくために作りました。何もかもが日々変わり、Reddit と X の濃い人たちがいつも真っ先に把握しています。もっと良いプロンプトが必要でしたが、学習データはコミュニティがすでに突き止めたことより常に数か月遅れていました。

ただ、そこからもっと大きなものになりました。今では商談の前に走らせて、その会社について直近30日間の実情を押さえます。打ち合わせの前には、相手の最近のツイートやポッドキャストの文字起こしを読むために。ディズニー・ワールドに行く前には、どのアトラクションが休止中で、Genie+ についてコミュニティが何と言っているかを知るために。何かを作り始める前には、人々が実際にどんな問題にぶつかっているかを知るために。

CEOと会うとして、直近30日間のツイートと YouTube の文字起こしを全部読んできましたか。私は読んでいます。

## 人々がスコアを付けた情報源

| 情報源 | 人々が教えてくれること |
|--------|--------------------------|
| **Reddit** | フィルターのかかっていない本音。実際のアップボート数付きのトップコメントが、無料・APIキーなしで手に入ります。Google が埋もれさせてしまう本当の意見です。 |
| **X / Twitter** | 勢いのある一言、専門家のスレッド、速報への最初の反応。誰よりも早く知り、誰よりも早く議論が始まります。 |
| **YouTube** | 45分の掘り下げ。文字起こし全文を検索し、引用に値する5つの文だけを取り出します。 |
| **TikTok** | Google では絶対に見つからない切り口で360万人に届いているクリエイター。 |
| **Instagram Reels** | 話した内容の文字起こし付きで届く、インフルエンサーの視点。ビジュアル文化のシグナルです。 |
| **Hacker News** | 開発者の総意。825ポイント、899コメント。技術寄りの人たちが本気で議論している場所です。 |
| **Polymarket** | 意見ではなく、オッズ。実際のお金が裏付けています。アルバムの売上に96%、買収に4%といった具合です。 |
| **GitHub** | 人物について: PRの勢い、スター数の多いリポジトリ、リリースノート。トピックについて: Issue と Discussion。 |
| **Digg** | Digg の AI 1000 リーダーボード(X 上でシグナルの強いAI関連アカウント約1000件)から集めたストーリークラスター。出典をたどれるインライン引用付きで、X の認証は不要です。`digg-pp-cli` が PATH にあると自動的に有効になります。 |
| **arXiv** | 話題の裏側にある論文。対象期間に出た新しい研究が、無料・APIキーなしで手に入ります。`arxiv-pp-cli` が PATH にあると自動的に有効になります(初回セットアップでインストールされます)。 |
| **Techmeme** | テックニュースの編集レイヤーを、対象の30日間に絞って取得します。無料・APIキーなし。`techmeme-pp-cli` が PATH にあると自動的に有効になります(初回セットアップでインストールされます)。 |
| **LinkedIn** | ビジネス面のシグナル。投稿と記事を拾い、記事は強いシグナルとして重み付けします。 |
| **StockTwits** | トレーダーの温度感。調べる対象が銘柄コードや暗号資産のときに自動で有効になります。 |
| **Threads** | Twitter 以後のテキストの層。クリエイターやブランドの会話です。 |
| **Pinterest** | ビジュアル起点の発見。プロダクトやアイデアに対するピン・保存・コメント。 |
| **Xiaohongshu(RED)** | 中国のライフスタイル・プロダクト・クリエイターのシグナル。ログイン済みの x-mcp ブラウザプラグイン、または `xiaohongshu-mcp` サービスがローカルで動いているときに、`--search xhs` で明示的に指定して使います。 |
| **Bluesky** | 分散型のソーシャル層。Twitter 以後の移住で生まれた AT Protocol の投稿です。 |
| **Perplexity** | 根拠付きの Sonar による統合、Search API の生の結果、そして Deep Research。 |
| **Web** | 編集記事や、ブログの比較記事。数あるシグナルの1つであって、唯一のものではありません。 |

コミュニティが今も情報源を増やし続けています。Truth Social をはじめとするニッチな情報源もすでにエンジンに入っていて、さらに追加予定です。

1,500アップボートの Reddit スレッドは、誰にも読まれなかったブログ記事よりも強いシグナルです。360万回再生の TikTok は、プレスリリースよりも「今、文化的に何が効いているか」を語ります。6.6万ドルの出来高に裏打ちされた Polymarket のオッズは、評論家の当て推量よりも反論しにくいものです。

この統合処理は、実在の人々が実際に反応したかどうかで順位を付けます。SEO上の関連性ではなく、社会的な関連性です。

## みんなが実際に使っている場面

**打ち合わせの前に。** `/last30days Peter Steinberger` — OpenAI の Codex チームに参加、サードパーティ製エージェントを禁じた Anthropic の方針と対立、GitHub で23本のPRをマージ率85%でマージ、デバイスをまたいでエージェントを操作する LobsterOS を開発中。r/ClaudeCode では「OpenClaw が出てからずっと、API 以外の経路で動かせばいずれBANされると広く知られていた」(227アップボート)。これは LinkedIn には載っていません。

**採用シグナルを読むために。** `/last30days Listen Labs --hiring-signals` — 現在の求人ページやキャリアページが、注力領域の変化を示す引用可能な根拠になります。エンタープライズ向けセキュリティ、カスタマーサクセス、インフラ、プロダクト拡張といった採用の動きです。レポートが述べるのは「採用が何を示唆しているように見えるか」であって、「ロードマップが何を出すか」ではありません。

**ピークを迎える前の話題を見つけるために。** `/last30days what's exploding in AI agents?` と尋ねると、スキルはディスカバリーモードに切り替わります。エンジンが Reddit のカテゴリー一覧、Hacker News のフロントページとベストストーリー、Digg の AI 1000 フィード、そして認証済みであれば X を横断してさらいます。次にエージェントが候補を審査し(名前の妥当性、ノイズの除去、記事になるか)、ポッドキャストや X 記事の切り口を書きます。最後に、勢いの強さで並べた5〜10件のトピックが返ってきます。各結果には情報源をまたいだ数値、モメンタムのラベル、そしてそのまま実行できる `/last30days "<topic>"` が付いてきます。

**何かが出たとき。** `/last30days Kanye West` — イギリスがビザを却下、Wireless Festival は中止、スポンサーは離脱。それでも BULLY は Billboard 初登場2位。Fantano は「Yay sabbatical」から復帰してレビューを公開(65.3万回再生)。SoFi Homecoming では Lauryn Hill と Travis Scott を迎えて44曲を披露。Polymarket では「Kanye はまたツイートするか?」が「はい」86%。Reddit のスレッド23件、YouTube の動画17本、アップボート8.6万件。

**ツールを比べるために。** `/last30days OpenClaw vs Hermes vs Paperclip` — 「これらは競合ではなくレイヤーだ」。OpenClaw は実行を担うレイヤー(GitHub スター35.1万、稼働中)、Hermes は自己改善する頭脳(スター3.1万)、Paperclip は組織図(スター4.9万)。スター数は古いブログ記事からではなく GitHub API からその場で取得しています。アーキテクチャ、メモリ、セキュリティ、向いている用途を並べた比較表付き。@IMJustinBrooke いわく「OpenClaw = ヒトカゲ、Hermes = リザードン」。

**世界の動きを理解するために。** `/last30days Iran vs USA` — 開戦から38日目。トランプ大統領はイランに対し、ホルムズ海峡の再開について火曜日を期限とする最後通告。米軍機2機が撃墜。原油は1バレル126ドル。IEA はこれを「世界の石油市場の歴史上最大の供給途絶」と呼びました。Polymarket では12月31日までの停戦が74%。X の投稿27件、YouTube の動画10本、予測市場20件。

**旅行の前に。** `/last30days Universal Epic Universe` — 拡張エリアはすでに着工済み。「Project 680」の建設許可が申請されています。花火ショーはインフラの痕跡から確認できるものの、まだ発表はありません。待ち時間は Mine-Cart Madness が平均148分。年間パスはまだ出ておらず、地元の人たちは不満を漏らしています。Stardust Racers は4月5日まで改修で運休。

**手早く学ぶために。** `/last30days Nano Banana Pro prompting` — JSON で構造化したプロンプトが、タグの寄せ集めに取って代わりつつあります。@pictsbyai の入れ子形式は「コンセプトの混線」を防ぎます。作り直すより、編集を前提にしたワークフローのほうが結果が出ます。そのうえで、コミュニティが「これは効く」と言った内容をそのまま使って、実運用向けのプロンプトを書いてくれます。

## 最近の変更

5月の v3.3 発表以降、v3.11.1(2026年7月)時点までで、15回のリリースにわたり175本のPRがマージされました。うち122本はコミュニティの52人によるものです。以下がその内容です。

### OpenAI Codex での一級対応

/last30days は、ガイド付きセットアップを備えた Codex のネイティブプラグインになりました。移植版ではなく、一級の対応です。レンダラーを踏まえた引用処理によって、Codex での出力はURLの羅列ではなくブリーフとして読めるようになり(#694)、同じエンジンが Claude Code、Cursor、Copilot、Gemini CLI、Claude Desktop、OpenClaw、そして50以上の Agent Skills ホストで動きます。Codex のプラグインマニフェストは [@rfoust](https://github.com/rfoust)(#686)、Codex の認証まわりの修正は [@tmchow](https://github.com/tmchow)(#698)によるものです。

### arXiv、Techmeme、Digg — 無料、APIキー不要

arXiv は話題の裏側にある論文を、Techmeme はテックニュースの編集レイヤーを持ち込みます。いずれも無料でキーは一切不要、しかも初回セットアップが各CLIをインストールするので自動的に有効になります(#709)。Digg の AI 1000 ストーリークラスターも同じように、X の認証なしで届きます。セットアップが無料の Digg CLI を入れてくれます(#590)。Trustpilot は消費者向けブランドの調査用に、任意で有効にできます。

### 無料の Reddit が、実数のスコアとトップコメント付きで復活

Reddit の公開 .json API は終了しましたが、無料の経路はより強くなって戻ってきました。キー不要の RSS と shreddit のスクレイピング(#457)、arctic-shift 経由で実際のアップボート数まで取れるサブレディット特定(#696)、そして話題から外れたバズ投稿にブリーフを乗っ取られないようにする関連性の下限(#488、[@rzachsmith](https://github.com/rzachsmith) に感謝)。APIキーは不要。スコアは実数。トップコメントも込みです。

### どのブリーフにも最高のコメントを

コメントは今や、どの情報源でも既定で有効なレイヤーです。Instagram のコメントは順位に基づいて分散させ、尖った意見5件が同じ投稿ばかりから出ないようにしています(#751)。YouTube のコメントに加えて、yt-dlp が失敗したときのために ScrapeCreators による文字起こしのバックアップも用意しました(#637)。さらに、コミュニティの投票で支持されたコメントを Best Takes のスコアに反映し、いちばん面白い一言が選別を生き延びるようにしています(#592、#608)。

### doctor コマンド1つで

ヘルスチェックを頼めば、doctor がすべての情報源を試したうえで、必要な対処をそのまま提示します。どのキーが足りないのか、どのCLIが PATH に入っていないのか、どのクッキーが期限切れなのか(#753)。X の結果が薄かった理由を当てずっぽうで探す必要はもうありません。

### X 検索の作り直し

X のパイプラインを一から作り直しました。FROM レーンと ABOUT レーンを設けて、本人の投稿と本人についての会話の両方が順位付けされるようにし(#610)、対象人物に応じてサブクエリの曖昧さを解消し(#611)、本人による投稿かどうかを裏付けたうえでインタラクションのシグナルで順位を付け(#613)、バックエンドを自動で切り替える単一の X ソースにまとめました(#622)。さらに、認証を実際に確かめる正直な `--diagnose` も入っています(#609)。

### 情報源が増えました

ScrapeCreators 経由の LinkedIn。記事は強いシグナルとして扱います([@ravstr](https://github.com/ravstr)、#702)。StockTwits は銘柄コードや暗号資産の話題で自動的に有効になります([@wtiwana](https://github.com/wtiwana)、#658)。Perplexity は直接APIモードと非同期の Deep Research に対応しました([@sk-holmes](https://github.com/sk-holmes)、#629)。

### コミュニティによる堅牢化

セキュリティ面の改善は、ほぼすべてコミュニティの手によるものです。HTML レンダラーの格納型XSSの修正([@iliaal](https://github.com/iliaal)、[@aaronjmars](https://github.com/aaronjmars))、クッキーの一時ファイルの権限強化、OpenSSF Scorecard とビルド来歴の証明を組み込んだサプライチェーン耐性のあるCI([@shaanmajid](https://github.com/shaanmajid)、[@hammadxcm](https://github.com/hammadxcm)、[@aniruddh909](https://github.com/aniruddh909))、Semgrep と OSV-Scanner によるスキャンおよびPRごとの依存関係レビューゲート([@23241a6749](https://github.com/23241a6749))、60%で導入し現在は84%まで引き上げたテストカバレッジの下限([@gourab5139014](https://github.com/gourab5139014))、そして CRITICAL の指摘がゼロになった Hermes のセキュリティスキャン(#768)。

### 届く範囲が広がりました

ヘブライ語をはじめとする非ラテン文字の言語に対応([@dudyme](https://github.com/dudyme))。中国語の情報源向けに CJK を考慮したトークナイズ([@An-idd](https://github.com/An-idd))。Windows 対応の改善もまとめて入りました。Chromium 系ブラウザ全体(Brave、Edge、Vivaldi、Opera、Arc)からのクッキー抽出([@andrey-esipov](https://github.com/andrey-esipov))に加え、macOS のキーチェーンと Linux の pass(1) も認証情報の取得元として使えます。`--as-of` による過去時点の振り返り([@chiyi-creator](https://github.com/chiyi-creator))。uv 経由での Python 3.12 の自動セットアップ([@buntysomroy](https://github.com/buntysomroy))。企業の求人ページを読む `--hiring-signals`。実行と実行のあいだのウォッチリスト差分。

### v3 から引き続き入っているもの

v3 の土台はすべて健在です。APIコールを1件も投げる前に、適切なアカウント・サブレディット・ハッシュタグを特定する事前リサーチの頭脳([@j-sperling](https://github.com/j-sperling) が構築)。関連性だけでなくユーモアやバイラル性も見る Best Takes のスコアリング。情報源をまたいだクラスターの統合。1回のパスで済む比較(「CLI vs MCP」が12分ではなく3分)。自動で候補を見つける `--competitors` 比較。GitHub の人物モード(`--github-user=steipete`)。ELI5 モード(実行後に「eli5 on」)。そして共有できる自己完結型の HTML ブリーフ(`--emit=html`)。設定項目は [CONFIGURATION.md](CONFIGURATION.md) にまとまっています。

## インストール

| 環境 | インストール | 更新 |
|---------|---------|---------|
| **Claude Code**(推奨) | `/plugin marketplace add mvanhorn/last30days-skill` | マーケットプレイス経由で自動、または `claude plugin update last30days@last30days-skill` |
| **Grok**(xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` のあとに `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex、Cursor、Copilot、Gemini CLI、その他50以上の [Agent Skills](https://agentskills.io) ホスト** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai**(ウェブ) | [`last30days.skill` をダウンロード](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill)し、claude.ai > Customize > Skills > + > Create skill > Upload a skill からアップロード | ダウンロードし直してアップロードし直す |
| **Claude Desktop** | [お使いのプラットフォーム向けの `.mcpb` をダウンロード](https://github.com/mvanhorn/last30days-skill/releases/latest)し、Settings > Extensions にドラッグ | ダウンロードし直して新しいバンドルをドラッグ |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code(推奨)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Claude Code のマーケットプレイスが更新を代わりにやってくれるため、これが推奨です。プラグインのキャッシュはバージョン管理されていて、新しいリリースが公開されると自動で更新されます。`claude plugin update last30days@last30days-skill` を実行すれば、その場で確認を強制できます。

Claude Code で Agent Skills 経由のインストールを使いたい場合も、それはそれで対応しています。

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

ネイティブプラグインと `npx skills` でのインストールは共存できます。ただし Claude Code はインストール方法をまたいだ重複排除を行いません。マーケットプレイス版のプラグインと `npx skills` のコピーを両方とも有効にしていると、`/last30days` が2件表示されます。1台につきインストール方法は1つにしてください。

### Grok(xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces)(`grok`)は last30days をネイティブプラグインとしてインストールします。直接インストールする場合はリポジトリを追跡します。

```bash
grok plugin install mvanhorn/last30days-skill
```

あるいは、このリポジトリをマーケットプレイスのソースとして追加してから、プラグイン名でインストールすることもできます。

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

インストール時の確認を省きたい場合は `--trust` を付けてください。更新は `grok plugin update last30days` です。Grok は互換性のために Claude Code のマニフェストも読みますが、第一の経路はネイティブの `.grok-plugin/` のペアで、[xAI のマーケットプレイス](https://github.com/xai-org/plugin-marketplace)への公式掲載もこちらを指しています。`npx skills add` は、どのホストでも使える代替手段として引き続き有効です。

### Codex、Cursor、Copilot、Gemini CLI、その他の Agent Skills ホスト

オープンな [Agent Skills](https://agentskills.io) の CLI からインストールします。`codex`、`cursor`、`github-copilot`、`gemini-cli`、`claude-code`、`windsurf`、`cline`、`continue`、`roo`、`aider-desk`、`opencode`、`goose` など50以上のホストに対応しています(全一覧は [vercel-labs/skills リポジトリ](https://github.com/vercel-labs/skills)にあります)。

```bash
npx skills add mvanhorn/last30days-skill -g
```

`-g`(グローバル)フラグを付けるとユーザーディレクトリにインストールされ、スキルをすべてのプロジェクトで使えます。`-g` を付けない場合、`npx skills` はプロジェクト内の `./.skills/` にインストールし、リポジトリと一緒にコミットされます。世界中を調べるためのツールなので、通常はグローバルが向いています。

Codex のデスクトップ版など、フォルダ単位で動くホストは、Git リポジトリでも普通のフォルダでも動作します。最初の調査を始める前に、読み込み済みのスキルディレクトリから同梱の `scripts/last30days.py --preflight` を実行するようホストのエージェントに頼んでください。ソースをチェックアウトしている場合、同等のコマンドは `python3 skills/last30days/scripts/last30days.py --preflight` です。設定の取得元、ブラウザのクッキーをどう扱う予定か、どのファイルを書き込む予定か、任意で使えるコマンド、無視されるプロジェクト設定を表示します。クッキーの読み取りもファイルの書き込みも調査の実行もしません。

既定では、`npx skills` が検出したホスト向けにインストールされます。特定のホスト(または複数)を指定するには次のようにします。

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

あとから更新するには次のようにします。

```bash
npx skills update last30days -g
```

`npx skills` でグローバルに入れたものをまとめて更新することもできます。

```bash
npx skills update -g
```

一覧表示と削除は `npx skills list -g` と `npx skills remove last30days -g` で行えます。

### claude.ai(ウェブ)

1. 最新リリースから [`last30days.skill` をダウンロード](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill)します
2. [claude.ai > Customize > Skills](https://claude.ai/customize/skills) を開きます
3. Skills パネルの `+` ボタンをクリックし、`Create skill` > `Upload a skill` と進んで、ファイルを選択するかドロップします

先に Capabilities で「Code execution and file creation」を有効にしてください。これがないとスキルは動きません。

### Claude Desktop

Claude Desktop では、`.mcpb` バンドル(ワンクリック版の Model Context Protocol パッケージ)を使って `/last30days` を MCP サーバーとしてインストールします。

1. [最新リリース](https://github.com/mvanhorn/last30days-skill/releases/latest)を開き、お使いのプラットフォーム向けの `.mcpb` をダウンロードします:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Claude Desktop を開き、Settings > Extensions に移動して、ファイルをドラッグします。
3. 求められたら、有効にしたい情報源のAPIキーを貼り付けます。どの項目も任意です。すべて省略した場合、エンジンはウェブのみのモードに切り替わります。キーはOSのキーチェーンに保存されます。
4. Claude Desktop を再起動します。Claude に「Peter Steinberger について調べて」などと頼めば、`research` ツールが呼び出されます。

**ホスト側の要件:** PATH の通った Python 3.12以上。バンドルにはエンジンのソースが含まれますが、実行にはローカルの Python インタプリタを使います。Windows では [python.org](https://www.python.org/downloads/) からインストールしてください。macOS とたいていの Linux ディストリビューションには、対応するバージョンが最初から入っています。

**キーは Claude Code のスキルとは共有されません。** Claude Desktop と Claude Code は、設計上それぞれ別に認証情報を保管しています。Claude Code のスキル用にすでに `~/.config/last30days/.env` を設定していても、ここで同じキーをもう一度だけ入力する必要があります。

Windows のサポートは、マニフェストのプラットフォーム別エントリーポイントが整理されるまで見送りとなっており、専用のIssueで追跡しています。

### OpenClaw

```bash
clawhub install last30days-official
```

`/last30days` の調査以外で X/Twitter を操作したい場合 — ツイートや返信の投稿、
フォロワーのエクスポート、メディアの扱い、モニタリング、プレゼント企画の抽選など —
には、OpenClaw の補助プラグインとして [TweetClaw](https://github.com/Xquik-dev/tweetclaw)
を使ってください。TweetClaw は Xquik-dev が管理しており、ここでは任意の補助的な
選択肢として挙げているだけです。last30days の依存でも推奨でもありません。

### 手動インストール(開発者向け)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

シンボリックリンクにしておけば、編集するたびに作業ツリーとインストール先が同期するので、コピーし直す必要はありません。`claude.ai` 用には、ソースから `.skill` ファイルをビルドしてください。`bash skills/last30days/scripts/build-skill.sh` で `dist/last30days.skill` が生成されます。

Reddit(コメント込み)、Hacker News、Polymarket、GitHub はすぐに使えます。設定は不要です。`/last30days` を一度実行すれば、セットアップウィザードが30秒でさらに多くの情報源を有効にします。無料の arXiv と Techmeme の CLI も含まれます。

## 自分のキーを持ち込む

これらのプラットフォーム同士には何のつながりもありません。X は Reddit が何を考えているかを知りませんし、YouTube に TikTok は見えていません。しかし自分のAPIキーとブラウザのトークンを持ち込めば、それらすべてに一度にアクセスできるようになります。

| 情報源 | 必要なもの | 費用 |
|---------|---------------|------|
| Reddit(コメント込み)+ HN + Polymarket + GitHub + StockTwits | 不要 | 無料 |
| arXiv + Techmeme | 無料のCLI。初回セットアップが自動でインストールします | 無料 |
| X / Twitter | 任意のブラウザで x.com にログインするか、`XQUIK_API_KEY` / `XAI_API_KEY` を設定 | ブラウザのクッキーは無料。キーの料金は提供元によります |
| YouTube | `brew install yt-dlp` | 無料 |
| Bluesky | bsky.app のアプリパスワード | 無料 |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube のコメント | ScrapeCreators のキー | 1万リクエストまで無料、以降は従量課金 |
| Xiaohongshu(RED) | ログイン済みの x-mcp ブラウザプラグインか `xiaohongshu-mcp` サービスを動かしたうえで、実行ごとに `--search xhs` を付けるか `.env` に `INCLUDE_SOURCES=xiaohongshu` を設定して有効化します。last30days は `http://localhost:18060`、次に `http://host.docker.internal:18060` の順に自動で接続を試し、独自のURLを使う場合は `XIAOHONGSHU_API_BASE` を指定します | last30days 側のAPIキーは不要。ローカルのブラウザセッションのサービス次第です |
| DripStack(有料の金融ニュースレター) | 任意で有効化: 実行ごとに `--search dripstack`、または `.env` に `INCLUDE_SOURCES=dripstack` | キー不要。無料の公開検索APIを使います |
| Perplexity Sonar / Search API / Deep Research | Perplexity のキー、または Sonar の代替として OpenRouter のキー | 従量課金 |
| ウェブ検索 | Brave Search のキー | 月2,000クエリまで無料 |

### macOS のキーチェーン(任意)

macOS では、キーを `.env` ファイルではなくシステムのキーチェーンに保存できます。スキルは最も優先度の低い取得元として自動的に読み込むため、衝突した場合は `.env` ファイルとプロセスの環境変数が優先されます。

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

項目は現在のユーザー向けに、サービス名 `last30days-<KEY>` で保存されます。Darwin 以外のプラットフォームではローダーは何もしないため、Linux や Windows のユーザーにとって挙動は変わりません。

すでに別のサービス名でキーチェーンにキーを保存している場合は、秘密情報をコピーする代わりに、[CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) で説明している秘密情報ではないマッピング `LAST30DAYS_KEYCHAIN_ALIASES` を設定してください。

情報源ごとのキーの一覧、推論プロバイダーの優先順位、ウェブ検索バックエンドの優先順位については [CONFIGURATION.md](CONFIGURATION.md) を参照してください。

## 設定

初日に知っておくとよいことが2つあります。

**調査ファイルの保存先。** `LAST30DAYS_MEMORY_DIR` の既定値は `~/Documents/Last30Days/` です(Windows では `C:\Users\<you>\Documents\Last30Days\`)。変更したい場合は、シェルでこの環境変数に任意のパスを設定するか、実行ごとに `--save-dir <path>` を指定します。レンダリング結果を特定のパスに出力したいときは `--output <file>` を使い、形式は `--emit` で選びます。同じトピックの複数のバリエーションを分けて残したいときは `--save-suffix=<name>` を使ってください(クライアントごとに分ける場合など)。`--save-dir` を付けた実行では `<slug>-raw[-suffix].md` が生成されます。調査を走らせる前に書き込み予定を確認するには `python3 skills/last30days/scripts/last30days.py --preflight` を実行してください。

**エージェントやワークフロー向けの構造化出力。** `/last30days` に機械可読なJSONを求めると、安定したバージョン付きのエージェント向けプロファイルが返ります。スクリプトや開発でエンジンを直接使う場合は `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json` を実行してください。バージョン管理されていない内部の `Report` のダンプが必要なときだけ `--json-profile=raw` を追加します。[JSONエクスポートのフィールド一覧とバージョニング方針](docs/reference/json-export.md)も参照してください。

**トピックを決めないディスカバリー。** すでに知っているトピックを調べる代わりに、順位付きのディスカバリーブリーフがほしいときは `/last30days what's trending in AI agents?` と尋ねてください。エージェントを備えたホストでは、ホストが判定する3コマンドのプロトコルが走ります(モデルがトピックを挙げ、ノイズを除き、取り上げる価値を採点し、コンテンツの切り口を書きます)。スクリプトや cron でエンジンを直接使う場合は `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` を実行します(単発実行。トピック名は決定論的で、切り口は付きません)。バージョン付きのディスカバリー契約がほしい場合は `--emit=json` を追加してください。ディスカバリーは、位置引数のトピックや `--drill` とは併用できません。

**実行をまたいだトレンド監視。** 既定のモードでは、実行のたびに新しい Markdown のスナップショットが作られます。時間をかけて結果を蓄積したい場合は `--store` を付けて SQLite データベースに保存し、定期実行には [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py)(新しい結果が出たときの Slack や Webhook への通知も任意で設定できます)、日次・週次のまとめには [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) を使ってください。運用サイクルの全体像は [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings) にあります。

**購読できる調査ライブラリ。** `/last30days` にライブラリのフィードを作らせるか、スクリプトや開発用には `python3 skills/last30days/scripts/last30days.py library feed` を直接使ってください。保存済みのブリーフが `index.html`、ローカルの Atom 形式の `feed.xml`、読みやすいブリーフのページに変換されます。HTML のインデックスとブリーフのページをホスティングしたいときだけ `--publish` を付けてください。公開は明示的に選ぶ形で、既定では誰でも見られる状態になります。Atom フィードを実際に購読できるようにするには、生成された出力ディレクトリを GitHub Pages のような静的ホスティングに置いてください。

**これまで調べたものをすべて検索する。** `/last30days search my library for MCP servers` や `/last30days have I researched MCP servers before?` と尋ねてください。エンジンを直接使う場合は `python3 skills/last30days/scripts/last30days.py library search "MCP servers"` を実行します。この検索はオフラインかつ決定論的です。ライブラリのフィードが使うのと同じ保存済みブリーフを少しずつインデックス化し、実行ごとにストアへ記録された該当分をまとめ、トピックと日付で結果をグループ化します。新しく実行したときも、過去の調査が今回のトピックと重なっていれば、**From your library**(あなたのライブラリから)というコンパクトなセクションが表示されます。この受動的な文脈表示をやめたい場合は `LAST30DAYS_LIBRARY_CONTEXT=off` を設定してください。

クライアントごとのラッパースクリプト、カテゴリー用のサブレディットのカスタマイズ、作業中のカスタマイズを試す実験的なベータチャンネルについても [CONFIGURATION.md](CONFIGURATION.md) に記載しています。

## ショーケース: コミュニティの調査フィード

last30days で、定期的なAIのまとめ、市場ウォッチ、あるいは見事にニッチな偏愛を公開しましたか。公開ライブラリのURL(または `feed.xml` を静的ホスティングに置いたあとの Atom のURL)を[コミュニティのショーケーススレッド](https://github.com/mvanhorn/last30days-skill/issues/532)で共有してください。コミュニティのフィードは、作者から届き次第ここにリンクしていきます。それまでのあいだは、このスレッドが集約先です。

## 仕組み

1. **トピックを入力します。** 人物、企業、プロダクト、技術、「X vs Y」。何でもかまいません。
2. **エージェントが「誰が重要か」を特定します。** X のアカウント(創業者を含む)、GitHub のリポジトリ、サブレディット、TikTok のハッシュタグ、YouTube のチャンネルを見つけます。「Kanye West」なら r/hiphopheads、@kanyewest、YouTube の「bully review」だと分かります。「OpenClaw」なら GitHub 上の openclaw/openclaw を特定し、スター数をその場で取得します。
3. **すべての情報源を並行して検索します。** 複数クエリへの展開。結果はエンゲージメント、関連性、新しさでスコア付けされます。
4. **他にはない深さ。** リアクション動画の YouTube 全文文字起こし。アップボート数付きの Reddit のトップコメント。TikTok のキャプション。Polymarket のオッズ。タイトルとリンクだけではありません。
5. **同じ話題はまとめます。** Reddit で告知され、X で語られ、TikTok にチケット価格が出た Wireless Festival は、3件の別々の項目ではなく1つのクラスターになります。
6. **1本のブリーフに統合します。** 具体的なデータに基づき、情報源を明示し、実際に人々が反応したかどうかで順位を付けます。「見つけたものはこれです」ではなく「重要なのはこれです」を返します。
7. **そのあとは、あなたの専門家になります。** 一度実行すれば、あなたの Claude のセッションはコミュニティが知っていることをすべて把握しています。続けて質問してください。プロンプトを書かせる、メールを下書きさせる、旅程を立てさせる、システム構成を設計させる。どれも「今、実際に起きていること」に基づきます。

## 使っている人の声

> 「Reddit、X、YouTube、HN を横断して直近30日のあらゆるトピックを調べてくれる Claude Code のスキルを見つけた。しかもプロンプトまで書いてくれる。書く記事ごとに、これまでは Reddit と X を手作業で調べていた。タブごと、スレッドごとに。そこが90分かかっていた部分だ。それがなくなる。」 — @itsjasonai

> 「このスキル1つで、私の調査ワークフローがまるごと置き換わった。トピックを渡すと、Reddit、X、ウェブから人々が本当に話していることを拾ってくる。古いブログ記事ではなく、直近30日の生の会話だ。」 — @itswilsoncharles

> 「今日 GitHub でトレンド入りしているリポジトリ10件のうち5件が Claude 関連のツール。1位は mvanhorn/last30days-skill」 — @yieldhunter95

## オープンソース

MIT ライセンス。トラッキングなし。アナリティクスなし。調査結果はあなたのマシンに残ります。テストは2,700件以上。

Python 3.12以上、yt-dlp、Node.js(X 検索用に同梱した Bird クライアント)、ScrapeCreators API で構築しています。v3 のエンジンアーキテクチャは [@j-sperling](https://github.com/j-sperling) によるものです。

PRの出し方は [CONTRIBUTING.md](CONTRIBUTING.md)、コミュニティの貢献者の一覧は [CONTRIBUTORS.md](CONTRIBUTORS.md)、バージョン履歴は [CHANGELOG.md](CHANGELOG.md) を参照してください。

## スター数の推移

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
