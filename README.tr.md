# /last30days

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [العربية](README.ar.md) | [فارسی](README.fa.md) | Türkçe

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
  <br/>
  <a href="https://github.com/mvanhorn/last30days-skill/actions/workflows/validate.yml">
    <img src="https://github.com/mvanhorn/last30days-skill/actions/workflows/validate.yml/badge.svg" alt="Validate status" />
  </a>
</p>

**Editörlerin değil; artı oyların, beğenilerin ve gerçek paranın belirlediği, yapay zekâ ajanının yönettiği bir arama motoru.**

Bu README güncel v4 işlem hattını anlatır. En güncel komut ve kurulum davranışı için esas kaynak olan çalışma zamanı beceri tanımı [skills/last30days/SKILL.md](skills/last30days/SKILL.md) dosyasındadır.

**Claude Code (önerilen — pazar yeri üzerinden otomatik güncellenir):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI veya 50'den fazla [Agent Skills](https://agentskills.io) ortamından biri:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g`, kullanıcı hesabınıza genel olarak kurar ve tüm projelerde kullanılmasını sağlar. Projeyle sınırlamak için kaldırın.)

Diğer kurulum seçenekleri (claude.ai web, OpenClaw, elle kurulum) aşağıdaki [Kurulum](#kurulum) bölümündedir.

Yapılandırma gerekmez. Reddit, HN, Polymarket ve GitHub hemen çalışır; konu bir şirketse patent başvuruları ve kariyer sayfaları da taranır. Bir kez çalıştırın; kurulum sihirbazı X, YouTube, TikTok, arXiv, Techmeme ve diğer kaynakları 30 saniyede açar.

---

Reddit artı oyları. X beğenileri. YouTube dökümleri. TikTok etkileşimleri. Gerçek para ve içeriden bilgiyle desteklenen Polymarket olasılıkları. Her gün milyonlarca insan ilgisiyle ve cüzdanıyla oy veriyor. /last30days hepsinde paralel arama yapar, insanların gerçekten etkileşim kurduğu içeriklere göre puanlar ve değerlendiren bir yapay zekâ ajanı bunları tek bir araştırma özetinde birleştirir.

Google editörleri bir araya getirir. /last30days insanlarda arama yapar.

Bu aramayı başka yerde bulamazsınız; tek bir yapay zekâ tüm kaynaklara erişemez. Google araması Reddit yorumlarını veya X gönderilerini taramaz. ChatGPT'nin Reddit ile anlaşması vardır ama X veya TikTok'ta arama yapamaz. Gemini'de YouTube vardır, Reddit yoktur. Claude'da bunların hiçbiri yerleşik değildir. Her platformun kendi API'si, tokenları ve kimlik doğrulaması vardır. Kendi anahtarlarınızı ve tarayıcı oturumlarınızı getirince bir ajan hepsini aynı anda arayabilir, karşılaştırmalı puanlayabilir ve gerçekten önemli olanı söyleyebilir.

Sağladığı olanak budur: bir ajanın birbirinden kopuk bir düzine platform arasında köprü kurması.

```
/last30days Peter Steinberger
```

Yarın bir toplantınız var. Kişiyi Google'da arayıp 2023'ten kalma LinkedIn profilini buluyorsunuz. /last30days bu ay gerçekte neler yaptığını getirir: Codex için OpenAI'a katılmış, Anthropic'in üçüncü taraf ajan yasağına karşı çıkmış, %85 birleştirme oranıyla 23 PR çıkarmış, cihazlar arası ajan kontrolü için “LobsterOS” geliştiriyor. r/ClaudeCode'da kahraman mı yoksa “çekilmez” biri mi olduğu tartışması 569 artı oy almış. Bilgiler X gönderilerine, Reddit başlıklarına, YouTube dökümlerine ve GitHub commit'lerine dağılmış. Hiçbiri Google'da yoktu.

## Neden geliştirildi?

Yapay zekâdaki gelişmeleri takip etmek için yaptım. Her şey her gün değişiyor; Reddit ve X meraklıları gelişmeleri hep ilk öğrenenler. Daha iyi istemlere ihtiyacım vardı ve eğitim verileri, topluluğun çoktan çözdüğü şeylerin aylar gerisindeydi.

Ama daha büyük bir şeye dönüştü. Artık satış görüşmesinden önce bir işletmenin son 30 gününü öğrenmek için çalıştırıyorum. Toplantıdan önce kişinin son gönderilerini ve podcast dökümlerini okumak için. Disney World gezisinden önce hangi oyuncakların kapalı olduğunu ve Genie+ hakkında topluluğun ne dediğini öğrenmek için. Bir şey geliştirmeden önce insanların gerçekte hangi sorunlara takıldığını anlamak için.

Bir CEO ile görüşecekseniz son 30 gündeki tüm gönderilerini ve YouTube dökümlerini okudunuz mu? Ben okudum.

## İnsanların puanladığı kaynaklar

| Kaynak | İnsanlar size ne söylüyor? |
|--------|--------------------------|
| **Reddit** | Filtresiz görüşler. Gerçek artı oy sayılarıyla öne çıkan yorumlar; ücretsiz, API anahtarı gerektirmez. Google'ın gerilere ittiği gerçek düşünceler. Onayınızla kendi açık Reddit hesabınız üzerinden daha fazla başlık ve güncel puan için arama da yapabilir. |
| **X / Twitter** | İddialı görüşler, uzman dizileri, anlık tepkiler. İlk öğrenenler ve ilk tartışanlar. |
| **YouTube** | 45 dakikalık derin incelemede önemli olan, alıntılanabilir 5 cümleyi bulmak için tam dökümler taranır. |
| **TikTok** | Google'da bulamayacağınız bir görüşle 3,6 milyon kişiye ulaşan içerik üreticisi. |
| **Instagram Reels** | Konuşma dökümleriyle içerik üreticilerinin bakış açısı. Görsel kültürün sinyali. |
| **Hacker News** | Geliştiricilerin ortak görüşü. 825 puan, 899 yorum. Teknik insanların gerçekten tartıştığı yer. |
| **Polymarket** | Gerçek parayla desteklenen olasılıklar. Albüm satışlarında %96, bir satın almada %4. Rapor, piyasa olasılıklarını ve değişimlerini ilgili haberin altında tek satırda gösterir. |
| **GitHub** | Kişiler için PR temposu, yıldız sayısına göre öne çıkan depolar ve sürüm notları. Konular için sorunlar ve tartışmalar. |
| **Digg** | Tüm Digg konularından (yapay zekâ, teknoloji, dünya ve iş, spor, bilim, oyun, eğlence) Digg Score ile sıralanan haber kümeleri ve kaynağı belirtilen X alıntıları; X girişi gerekmez. `digg-pp-cli` PATH üzerindeyse otomatik açılır. Eski sürümler de çalışır; yalnızca yapay zekâ ve teknoloji konularında, puansız. |
| **arXiv** | Heyecanın arkasındaki makaleler. Seçilen zaman aralığındaki yeni araştırmalar; ücretsiz, anahtarsız. `arxiv-pp-cli` PATH üzerindeyse otomatik açılır (ilk kurulumda yüklenir). |
| **Techmeme** | Son 30 güne göre filtrelenmiş teknoloji haberciliği. Ücretsiz, anahtarsız. `techmeme-pp-cli` PATH üzerindeyse otomatik açılır (ilk kurulumda yüklenir). |
| **Yahoo Finance** | Piyasanın şirket hakkındaki görüşü. Son 30 günlük hisse hareketi; ABD'de işlem gören şirketler için S&P 500 karşılaştırması. Ücretsiz, anahtarsız. Konu halka açık bir şirket veya yöneticisiyse ve `yahoo-finance-pp-cli` PATH üzerindeyse gösterilir (ilk kurulumda yüklenir). |
| **LinkedIn** | Profesyonel sinyaller. Gönderiler ve makaleler; makalelere daha yüksek ağırlık verilir. |
| **Meta Ads** | Markanın söylemek için para ödediği şeyler. Bu ay yayımlanan etkin reklamlar; metinler, promosyon kodları ve konuşma dökümleri. |
| **StockTwits** | Yatırımcı duyarlılığı. Konu hisse kodu veya kriptoysa otomatik açılır. |
| **Threads** | Twitter sonrası metin katmanı. İçerik üreticilerinin ve markaların konuşmaları. |
| **Pinterest** | Görsel keşif. Ürün ve fikirlere ilişkin pinler, kaydetmeler ve yorumlar. |
| **Xiaohongshu (RED)** | Çin'den yaşam tarzı, ürün ve içerik üreticisi sinyalleri. Yerelde giriş yapılmış x-mcp tarayıcı eklentisi veya `xiaohongshu-mcp` hizmeti çalışırken `--search xhs` ile açıkça istenir. |
| **Bluesky** | Merkeziyetsiz sosyal katman. Twitter sonrası göçten gelen AT Protocol gönderileri. |
| **Perplexity** | Kontrollü Agent API sentezi, yedek OpenRouter Sonar, ham Search API satırları ve açıkça istenen Deep Research. |
| **DEV Community (dev.to)** | Genel, anahtarsız API üzerinden geliştirici yazıları ve öne çıkan yorumlar. `--devto-tags`, `--search devto` veya `INCLUDE_SOURCES=devto` ile etkinleştirin. |
| **Truth Social** | `TRUTHSOCIAL_TOKEN` ile aranan gönderiler; kimlik doğrulaması varsa kullanılabilir. |
| **Telegram** | Belirtilen kanalların herkese açık gönderileri. İsteğe bağlıdır; `SCRAPECREATORS_API_KEY` ve kanal listesi gerekir. Anahtar sözcükle keşif yoktur. |
| **Trustpilot** | Şirket TrustScore'u, yorum sayısı ve müşteri geri bildirimlerinin yapay zekâ özeti. İsteğe bağlıdır; PATH üzerinde `trustpilot-pp-cli` ve kullanılabilir oturum gerekir. |
| **Amazon** | Ürün puanları, fiyatlar ve müşteri yorumları. İsteğe bağlıdır; giriş yapılmış `brightdata` CLI gerektirir ve Bright Data kredilerinizi kullanır. |
| **DripStack** | Genel, anahtarsız API üzerinden finans bültenleri ve analist yazıları. `--search dripstack` veya `INCLUDE_SOURCES=dripstack` ile etkinleştirin. |
| **İş ilanları / kariyer sayfaları** | Şirketin kendi kariyer sayfasından işe alım bilgileri (Workday, Apple, Amazon, Google, Microsoft, Netflix, Oracle vb.). Şirket araştırmasında otomatik okunur; duyurulmamış gelişmelere işaret eden roller, kariyer sayfası gizli sinyallerine dönüşür. `--hiring-signals`, `--search jobs` veya araştırma planıyla da seçilir. |
| **Patentler** | Şirketin ürünü çıkmadan önce ne geliştirdiğine dair yeni ABD patent başvuruları. Ücretsiz, anahtarsız. Konu şirketse gösterilir. Yalnızca şirket için alışılmadık veya henüz piyasaya sürülmemiş ya da duyurulmamış bir şey anlatan başvurular patent gizli sinyali sayılır. |
| **Yerel belge koleksiyonu** | Yerel Markdown ve metin dosyalarınız; `pdftotext` PATH üzerindeyse PDF'ler de. `--corpus` veya `LAST30DAYS_CORPUS_DIRS` ile etkinleştirin. |
| **Web** | Editoryal haberler ve blog karşılaştırmaları. Tek kaynak değil, birçok sinyalden biri. |

`/last30days` becerisinden gereken kaynakları dahil etmesini isteyin. Kullanılabilirlik kimlik bilgilerine, kurulu araçlara, konuya ve açık tercihlere bağlıdır. Yapılandırma için [kaynak gereksinimlerine](CONFIGURATION.md#source-requirements) bakın.

1.500 artı oyu olan bir Reddit başlığı kimsenin okumadığı blog yazısından güçlü sinyaldir. 3,6 milyon izlenmiş TikTok, kültürel ilgiyi basın bülteninden iyi anlatır. 66 bin dolar işlem hacmiyle desteklenen Polymarket olasılıklarına, yorumcu tahmininden daha zor karşı çıkılır.

Sentez, insanların gerçekten etkileşim kurduğu içeriklere göre sıralar. SEO değil, toplumsal ilgi belirleyicidir.

## Gerçekte ne için kullanılıyor?

**Toplantıdan önce.** `/last30days Peter Steinberger` — OpenAI'ın Codex ekibine katılmış, Anthropic'in üçüncü taraf ajan yasağına karşı çıkıyor, GitHub'da %85 birleştirme oranıyla 23 PR birleştirmiş, cihazlar arası ajan kontrolü için LobsterOS geliştiriyor. r/ClaudeCode: “OpenClaw çıktığından beri API dışında bir yolla çalıştırırsanız eninde sonunda yasaklanacağınız biliniyordu” (227 artı oy). Bunlar LinkedIn'de yok.

**Şirketin ne geliştirdiğini duyurulmadan öğrenmek için.** `/last30days Apple` — kariyer sayfası sinyali, isteklerin ortak API'leri ve finans sistemlerinde işlem tetikleyip tetiklemediğini test eden yeni bir direktör rolünü işaretledi; satın almaları tamamlayan bir asistanın kalite kontrolünü andırıyor. Patent sinyali, odanızın kayıtlı fotoğrafındaki cihaza dokunarak cihaz durumunu değiştirme başvurusunu buldu. Polymarket, Apple'ın yıl sonunda dünyanın en büyük şirketi olma olasılığını %91 gösterdi. `/last30days Alphabet`, “YouTube Ads Agentic Buyflows” ürün yöneticisi rolünü buldu; hisse 30 günde %17,6, S&P 500 %3,6 düşmüştü; en kötü gün 29 Ekim'di (-%12,6). Yalnızca işe alım raporu için `--hiring-signals` ekleyin.

**Konu zirveye ulaşmadan bulmak için.** `/last30days what's exploding in AI agents?` diye sorun. Keşif modunda motor Reddit kategori listelerini, Hacker News ön/iyi haberlerini, Digg'i ve giriş yapılmışsa X'i tarar. Ajan adayları adlandırır, ilgisizleri eler, içerik değerini değerlendirir ve podcast / X yazısı açıları önerir. Hızına göre sıralanmış 5–10 konu alırsınız. Her sonuç kaynaklar arası sayılar, ivme etiketi ve hazır `/last30days "<topic>"` takip komutu içerir.

**Yeni bir gelişme olduğunda.** `/last30days Kanye West` — Birleşik Krallık vizesini engelledi, Wireless Festival iptal edildi, sponsorlar çekildi. Ama BULLY Billboard'a 2. sıradan girdi. Fantano “Yay molasından” dönüp değerlendirdi (653 bin izlenme). SoFi Homecoming'de Lauryn Hill ve Travis Scott ile 44 şarkı seslendirildi. Polymarket: “Kanye tekrar tweet atacak mı?” %86 Evet. 23 Reddit başlığı, 17 YouTube videosu, 86 bin artı oy.

**Araçları karşılaştırmak için.** `/last30days OpenClaw vs Hermes vs Paperclip` — “Rakip değil, katmanlar.” OpenClaw yürütücü (güncel 351 bin GitHub yıldızı), Hermes kendini geliştiren beyin (31 bin), Paperclip organizasyon şeması (49 bin). Yıldız sayıları eski bloglardan değil, canlı GitHub API'sinden alınır. Mimari, bellek, güvenlik ve en uygun kullanım için karşılaştırma tablosu sunulur. @IMJustinBrooke: “OpenClaw = Charmander, Hermes = Charizard.”

**Dünyayı anlamak için.** `/last30days Iran vs USA` — savaşın 38. günü. Trump'ın İran'a Hürmüz Boğazı'nı açması için verdiği salı son tarihi. Düşürülen iki ABD savaş uçağı. Petrol 126 dolar/varil. IEA bunu “küresel petrol piyasası tarihinin en büyük arz kesintisi” diye niteledi. Polymarket: 31 Aralık'a kadar ateşkes %74. 27 X gönderisi, 10 YouTube videosu, 20 tahmin piyasası.

**Geziden önce.** `/last30days Universal Epic Universe` — genişleme inşaatı başlamış. “Project 680” izni başvurulmuş. Altyapı havai fişek gösterisine işaret ediyor ama henüz duyurulmamış. Mine-Cart Madness ortalama bekleme süresi 148 dakika. Yıllık geçiş kartı yok; bölge sakinleri tepkili. Stardust Racers, 5 Nisan'a kadar bakımda.

**Hızla öğrenmek için.** `/last30days Nano Banana Pro prompting` — etiket yığınlarının yerini JSON yapılandırılmış istemler alıyor. @pictsbyai'nin iç içe biçimi “kavram karışmasını” önlüyor. Önce düzenlemeye dayalı akış, yeniden üretimden iyi sonuç veriyor. Ardından topluluğun işe yaradığını söylediği yöntemlerle size kullanıma hazır istem yazıyor.

## Yenilikler

v4, şirketin ne yaptığını duyurmadan önce okur ve raporlardan alıntı yapmayı kolaylaştırır:

- Patent gizli sinyalleri: Şirket araştırmasında o ayın ABD patent başvurularını anahtarsız okur; yalnızca alışılmadık veya duyurulmamış bir şeyi anlatanları işaretler.
- Kariyer sayfası gizli sinyalleri: Şirketin kendi sayfasını okur; henüz haber olmayan ekipler dahil, gelecek gelişmeleri açığa çıkaran rolleri işaretler.
- Alıntı satırları: En iyi Reddit, X, YouTube, TikTok ve Instagram yorumları, gerekirse çevrilerek ilgili haberin altında ayrı satırlarda görünür. Videoda söylenen sözler de alıntılanabilir.
- Polymarket olasılık satırı: İlgili haberin altında olasılık ve değişimi tek satırda gösterir.
- Yahoo Finance satırı: Halka açık şirketin son 30 günlük hisse hareketini S&P 500 ve en büyük tek günlük değişimle gösterir.
- Kimi kastettiğinizi anlar: “Apple” ifadesini şirket, web sitesi ve hisse koduyla otomatik eşleştirir.
- Sizin adınıza Reddit: Onayınızla kendi açık Reddit hesabınız üzerinden arar.
- Digg artık yalnızca yapay zekâ ve teknolojiyi değil, tüm konuları kapsar.

Güncel yayımlanmış sürüm için [son sürüme](https://github.com/mvanhorn/last30days-skill/releases/latest), sürüm bazındaki değişiklikler için [CHANGELOG.md](CHANGELOG.md) dosyasına bakın. Varsayılan daldaki yayımlanmamış değişiklikler [changelog.d/](changelog.d/) içindedir.

Bu README güncel kaynak ağacını anlatır; kurulu sürümünüz farklı olabilir.

## Kurulum

| Ortam | Kurulum | Güncellemeler |
|---------|---------|---------|
| **Claude Code** (önerilen) | `/plugin marketplace add mvanhorn/last30days-skill` | Pazar yerinden otomatik veya `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill`, ardından `grok plugin install last30days` | `grok plugin update last30days` |
| **Grok Bot** | [Grok Bot pazar yerinden ekleyin](https://x.ai/bot/marketplace/bots/last30days) | Pazar yeri yönetir |
| **Codex, Cursor, Copilot, Gemini CLI veya 50'den fazla [Agent Skills](https://agentskills.io) ortamından biri** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [`last30days.skill` indirin](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) ve claude.ai > Customize > Skills > + > Create skill > Upload a skill yoluyla yükleyin | Tekrar indirip yükleyin |
| **Claude Desktop** | [Platformunuzun `.mcpb` dosyasını indirin](https://github.com/mvanhorn/last30days-skill/releases/latest) ve Settings > Extensions alanına sürükleyin | Yeni paketi indirip sürükleyin |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (önerilen)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Claude Code pazar yeri güncellemeleri yönettiği için önerilir: eklenti önbelleği sürümlüdür ve yeni sürüm çıktığında otomatik yenilenir. Kontrolü zorlamak için `claude plugin update last30days@last30days-skill` çalıştırın.

Claude Code'da agent-skills kurulum yolunu tercih ederseniz bu da desteklenir:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

### Hızlı deneme bağlantısı

[Last30Days araştırma becerisini Telegram veya WhatsApp'ta deneyin](https://app.clawmama.run/skills/2ne05f/hermes?utm_source=github&utm_medium=issue&utm_campaign=skill_outreach_mvanhorn_last30days_skill)

Yerel eklenti ve `npx skills` kurulumu birlikte bulunabilir. Ancak Claude Code kurulum yöntemleri arasında yinelenenleri elemez: ikisi de etkinse `/last30days` iki kez görünür. Bilgisayar başına tek yöntem kullanın.

### Grok (xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`), last30days'i yerel eklenti olarak kurar. Doğrudan kurulum depoyu takip eder:

```bash
grok plugin install mvanhorn/last30days-skill
```

Depoyu pazar yeri kaynağı olarak ekleyip eklenti adıyla da kurabilirsiniz:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Kurulum onayını atlamak için `--trust` ekleyin. `grok plugin update last30days` ile güncelleyin. Grok uyumluluk için Claude Code bildirim dosyalarını da okur; asıl yol yerel `.grok-plugin/` çiftidir ve resmî [xAI pazar yeri](https://github.com/xai-org/plugin-marketplace) kaydı bunu kullanır. `npx skills add` ortamlar arası yedek yoldur. Pazar yerinden kurulan [Grok Bot](https://x.ai/bot/marketplace/bots/last30days) üzerinde X, önce botun yerleşik araçları, ardından X bağlayıcısıyla hazır çalışır; resmî X API'si (`X_BEARER_TOKEN`) yedektir.

### Codex, Cursor, Copilot, Gemini CLI ve diğer Agent Skills ortamları

Açık [Agent Skills](https://agentskills.io) CLI ile kurun. `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose` ve 50'den fazla ortamı destekler. Tam liste [vercel-labs/skills deposundadır](https://github.com/vercel-labs/skills).

```bash
npx skills add mvanhorn/last30days-skill -g
```

`-g` (genel) seçeneği kullanıcı dizininize kurar; beceri tüm projelerde kullanılabilir. Seçenek olmadan `npx skills`, depoyla commit edilen `./.skills/` altına projeye özel kurar. Genel amaçlı araştırma için genel kurulum uygundur.

Codex masaüstü ve klasör modlu diğer ortamlar Git depolarının yanında normal klasörlerde de çalışır. Araştırma başlamadan neyin okunup yazılacağını görmek için yüklenen beceri dizininde `scripts/last30days.py --preflight` çalıştırın. Kaynak depodaki karşılığı `python3 skills/last30days/scripts/last30days.py --preflight` komutudur. Çerez okumadan, dosya yazmadan veya araştırmadan; yapılandırma kaynağını, çerez planını, planlanan yazmaları, isteğe bağlı komutları ve yok sayılan proje ayarlarını gösterir. İlk kurulum için zorunlu değildir.

Varsayılan olarak `npx skills` hangi ortamı algılarsa ona kurar. Belirli bir veya birden fazla ortam için:

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Daha sonra güncellemek için:

```bash
npx skills update last30days -g
```

`npx skills` ile genel kurduğunuz her şeyi güncellemek için:

```bash
npx skills update -g
```

Listelemek ve kaldırmak için `npx skills list -g` ve `npx skills remove last30days -g` kullanın.

### claude.ai (web)

1. Son sürümden [`last30days.skill` indirin](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill)
2. [claude.ai > Customize > Skills](https://claude.ai/customize/skills) sayfasına gidin
3. Skills panelinde `+` > `Create skill` > `Upload a skill` seçeneğini açıp dosyayı seçin veya sürükleyin

Önce Capabilities altında “Code execution and file creation” seçeneğini açın; beceriler onsuz çalışmaz.

### Claude Desktop

Claude Desktop, `/last30days` becerisini tek tıklamayla kurulabilen Model Context Protocol paketi `.mcpb` üzerinden MCP sunucusu olarak kurar.

1. [Son sürüme](https://github.com/mvanhorn/last30days-skill/releases/latest) gidip platformunuzun `.mcpb` dosyasını indirin:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Claude Desktop'ta Settings > Extensions bölümünü açıp dosyayı sürükleyin.
3. İstendiğinde açmak istediğiniz kaynakların API anahtarlarını yapıştırın. Tüm alanlar isteğe bağlıdır; hepsini atlarsanız motor yalnızca web moduna döner. Anahtarlar işletim sisteminin anahtar zincirinde saklanır.
4. Claude Desktop'ı yeniden başlatın. Claude'dan “Peter Steinberger'i araştır” veya başka bir konu isteyin; `research` aracını çağırır.

**Ortam gereksinimi:** PATH üzerinde Python 3.12+. Paket motor kaynaklarını içerir ama yerel Python yorumlayıcınızı kullanır. Windows'ta [python.org](https://www.python.org/downloads/) üzerinden kurun; macOS ve çoğu Linux dağıtımı uyumlu sürüm içerir.

**Anahtarlar Code becerisiyle eşitlenmez.** Claude Desktop ve Claude Code tasarım gereği ayrı kimlik bilgisi depoları kullanır. Code için `~/.config/last30days/.env` yapılandırdıysanız aynı anahtarları burada bir kez daha girmeniz gerekir.

Windows desteği, platforma özel bildirim giriş noktaları düzenlenene kadar ertelenmiştir; takip işi ayrı bir sorunda yürütülür.

### OpenClaw

```bash
clawhub install last30days-official
```

Araştırma dışındaki X/Twitter işlemleri için, örneğin gönderi ve yanıt yazma,
takipçi dışa aktarma, medya yönetimi, izleyiciler ve çekilişlerde,
OpenClaw'a yardımcı eklenti olarak [TweetClaw](https://github.com/Xquik-dev/tweetclaw) kullanabilirsiniz.
TweetClaw, Xquik-dev tarafından sürdürülür. Burada yalnızca isteğe bağlı
bir yardımcı olarak listelenir; last30days bağımlılığı veya tavsiyesi değildir.

### Elle kurulum (geliştirici)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

Sembolik bağlantı, düzenleme sırasında kurulumu çalışma ağacıyla eş tutar; tekrar kopyalamak gerekmez. `claude.ai` için `.skill` dosyasını kaynaktan derleyin: `bash skills/last30days/scripts/build-skill.sh`, `dist/last30days.skill` üretir.

Reddit (yorumlarla), Hacker News, Polymarket ve GitHub hemen çalışır; şirket konularında patent başvuruları ve kariyer sayfaları da. Ayar gerekmez. `/last30days` komutunu bir kez çalıştırın; sihirbaz ücretsiz arXiv ve Techmeme CLI'leri dahil diğer kaynakları 30 saniyede açar.

## Kendi anahtarlarınızı kullanın

Bu platformlar birbirine bağlı değil. X, Reddit'in ne düşündüğünü bilmez. YouTube, TikTok'u görmez. Kendi API anahtarlarınızı ve tarayıcı tokenlarınızı getirerek hepsine aynı anda erişebilirsiniz.

| Kaynaklar | Gerekenler | Maliyet |
|---------|---------------|------|
| Reddit (yorumlarla) + HN + Polymarket + GitHub + StockTwits + şirket patentleri ve kariyer sayfaları | Hiçbir şey | Ücretsiz |
| arXiv + Techmeme + Yahoo Finance | İlk kurulumda otomatik yüklenen ücretsiz CLI'ler | Ücretsiz |
| X / Twitter | Resmî X API için `X_BEARER_TOKEN` ayarlayın (X geliştirici projenizde tam arşiv erişimi yoksa yaklaşık son haftanın gönderileri; Grok Bot'ta yerleşik araçlardan sonra yedek, diğer ortamlarda `LAST30DAYS_X_BACKEND=xapi` ile isteğe bağlı). Alternatif olarak tarayıcıda x.com'a giriş yapın veya `XQUIK_API_KEY` / `XAI_API_KEY` ayarlayın | X API kredileri geliştirici projenizden gelir; tarayıcı çerezleri ücretsizdir, diğer anahtarların ücretleri sağlayıcıya bağlıdır |
| YouTube | `brew install yt-dlp` | Ücretsiz |
| Bluesky | bsky.app uygulama parolası | Ücretsiz |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube yorumları | ScrapeCreators anahtarı | İlk 10.000 çağrı ücretsiz, sonra kullandıkça öde |
| Xiaohongshu (RED) | Giriş yapılmış x-mcp tarayıcı eklentisi veya `xiaohongshu-mcp` çalıştırıp her çalışmada `--search xhs` ya da `.env` içinde `INCLUDE_SOURCES=xiaohongshu` ile açın. last30days önce `http://localhost:18060`, sonra `http://host.docker.internal:18060` adresini dener; özel URL için `XIAOHONGSHU_API_BASE` kullanın | last30days API anahtarı gerekmez; yerel tarayıcı oturumu hizmetinize bağlıdır |
| DripStack (ücretli finans bültenleri) | Her çalışmada `--search dripstack` veya `.env` içinde `INCLUDE_SOURCES=dripstack` ile açın | Anahtarsız, ücretsiz genel arama API'si |
| Perplexity Agent API / Search API / Deep Research | Perplexity anahtarı veya Sonar yedeği olarak OpenRouter anahtarı | Kullandıkça öde; doğrudan anahtar Agent API ve arka planda Deep Research sağlar |
| Web araması | Brave Search anahtarı | Ayda 2.000 ücretsiz sorgu |

### macOS Anahtar Zinciri (isteğe bağlı)

macOS'ta anahtarları `.env` yerine sistem Anahtar Zinciri'nde tutabilirsiniz. Beceri bunları en düşük öncelikli kaynak olarak otomatik okur; çakışmada `.env` ve süreç ortamı önceliklidir.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Öğeler geçerli kullanıcı için `last30days-<KEY>` hizmet adıyla saklanır. Darwin dışı platformlarda yükleyici işlem yapmaz; Linux/Windows davranışı değişmez.

Anahtarlarınız farklı Anahtar Zinciri hizmet adları altında mı? Gizli değerleri kopyalamak yerine [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) içinde anlatılan, gizli olmayan `LAST30DAYS_KEYCHAIN_ALIASES` eşlemesini ayarlayın.

Kaynak başına anahtar tablosu, akıl yürütme sağlayıcı önceliği ve web araması arka uç önceliği için [CONFIGURATION.md](CONFIGURATION.md) dosyasına bakın.

## Yapılandırma

İlk gün bilmek isteyeceğiniz noktalar:

**Araştırma dosyalarının kayıt yeri.** `LAST30DAYS_MEMORY_DIR` varsayılanı `~/Documents/Last30Days/` yoludur (Windows: `C:\Users\<you>\Documents\Last30Days\`). Kabukta bu değişkeni istediğiniz yola ayarlayın veya çalıştırma başına `--save-dir <path>` verin. `--emit` ile seçilen biçimde tam dosya yolu gerekiyorsa `--output <file>` kullanın. Aynı konunun farklı sürümlerini (ör. müşteri bazında) ayırmak için `--save-suffix=<name>` kullanın. Her `--save-dir` çalışması `<slug>-raw[-suffix].md` üretir. Yazma planını araştırmasız incelemek için `python3 skills/last30days/scripts/last30days.py --preflight` çalıştırabilirsiniz.

**Ajanlar ve iş akışları için yapılandırılmış çıktı.** Kararlı, sürümlenmiş ajan profili almak için `/last30days` becerisinden makinece okunabilir JSON isteyin. Betik veya geliştirmede doğrudan motor kullanımı için `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json` çalıştırın. Sürümlenmemiş dahili `Report` dökümü gerektiğinde `--json-profile=raw` ekleyin. [JSON alanları ve sürümleme politikasını](docs/reference/json-export.md) inceleyin.

**Konu belirtmeden keşif.** Bilinen bir konuyu araştırmak yerine sıralı keşif özeti için `/last30days what's trending in AI agents?` diye sorun. Ajan ortamında üç komutlu değerlendirme protokolü çalışır: model konuları adlandırır, ilgisizleri eler, değeri puanlar ve içerik açıları yazar. Betik veya cron için `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` çalıştırın (tek seferlik, belirlenimci konu adları; içerik açısı yok). Sürümlenmiş keşif sözleşmesi için `--emit=json` ekleyin. Keşif, konumla verilen konu ve `--drill` ile birlikte kullanılamaz.

**Çalışmalar arasında trend izleme.** Varsayılan mod her çalışmada yeni Markdown özeti üretir. Bulguları zamanla biriktirmek için `--store` ile SQLite'a kaydedin. Zamanlanmış çalışmalar ve yeni bulgularda isteğe bağlı Slack/webhook teslimi için [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py), günlük/haftalık özetler için [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) kullanın. Tam döngü [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings) içindedir.

**Abone olunabilen araştırma kütüphanesi.** `/last30days` becerisinden kütüphane akışınızı oluşturmasını isteyin. Betik ve geliştirmede doğrudan `python3 skills/last30days/scripts/last30days.py library feed` kullanın. Kayıtlı özetleri `index.html`, yerel Atom `feed.xml` ve okunabilir özet sayfalarına çevirir. Yalnızca HTML dizinini ve sayfaları barındırmak istiyorsanız `--publish` ekleyin; yayımlama açık tercihe bağlıdır ve varsayılan olarak herkese açıktır. Atom akışına abone olunabilmesi için çıktı dizinini GitHub Pages gibi statik barındırmada sunun.

**Araştırdığınız her şeyi arayın.** `/last30days search my library for MCP servers` veya `/last30days have I researched MCP servers before?` diye sorun. Doğrudan motor için `python3 skills/last30days/scripts/last30days.py library search "MCP servers"` kullanın. Arama çevrimdışı ve belirlenimcidir; kütüphane akışındaki kayıtlı özetleri artımlı dizine ekler, eşleşen çalışma kayıtlarını birleştirir ve konu/tarihe göre gruplar. Önceki araştırmalar konuya uyuyorsa yeni çalışmada kısa **Kütüphanenizden** bölümü görünür; kapatmak için `LAST30DAYS_LIBRARY_CONTEXT=off` ayarlayın.

İstemciye özel sarmalayıcı betikler, özel kategori alt toplulukları ve devam eden özelleştirmeler için deneysel beta kanalı da [CONFIGURATION.md](CONFIGURATION.md) içinde açıklanır.

## Vitrin: topluluğun araştırma akışları

last30days ile düzenli yapay zekâ güncellemesi, piyasa takibi veya çok özel bir ilgi alanı mı yayımlıyorsunuz? Herkese açık kütüphane URL'sini ya da `feed.xml` dosyasını statik sunucuda barındırdıktan sonra Atom URL'sini [topluluk vitrin başlığında](https://github.com/mvanhorn/last30days-skill/issues/532) paylaşın. Sahipleri gönderdikçe topluluk akışları buraya eklenir; şimdilik toplama noktası bu başlıktır.

## Nasıl çalışır?

1. **Bir konu yazarsınız.** Kişi, şirket, ürün, teknoloji, “X ve Y karşılaştırması”; herhangi bir şey.
2. **Ajan önemli kişi ve kaynakları belirler.** Kurucular dahil X hesapları, GitHub depoları, alt topluluklar, TikTok etiketleri, YouTube kanalları. “Kanye West” için r/hiphopheads, @kanyewest ve YouTube'da “bully review” aramasını bilir. “OpenClaw” için GitHub'da openclaw/openclaw deposunu bulup güncel yıldız sayısını alır.
3. **Tüm kaynaklarda paralel arar.** Sorguları çeşitlendirir. Sonuçları etkileşim, ilgi ve güncellikle puanlar.
4. **Derinlemesine içerik getirir.** Tepki videolarının tam YouTube dökümleri, artı oy sayılarıyla Reddit yorumları, TikTok açıklamaları, Polymarket olasılıkları; yalnızca başlık ve bağlantılar değil.
5. **Aynı haber birleştirilir.** Reddit'te duyurulan, X'te konuşulan ve bilet fiyatları TikTok'ta görülen Wireless Festival üç ayrı öğe değil, tek kümedir.
6. **Tek özette sentezlenir.** Belirli verilere dayanır, kaynak gösterir, insanların gerçek etkileşimine göre sıralanır. Bulunanları değil, önemli olanları gösterir.
7. **Ardından uzmanınız olur.** Bir çalışmadan sonra Claude oturumunuz topluluğun bilgilerine sahiptir. Takip soruları sorun; güncel gerçeklere dayanan istemler, e-posta taslakları, gezi planları veya sistem tasarımları hazırlatın.

## Kullanıcılar ne diyor?

> “Son 30 gündeki Reddit, X, YouTube ve HN içeriklerinde her konuyu araştıran bir Claude Code becerisi buldum. Sonra istemleri de yazıyor. Yazdığım her içerikten önce Reddit ve X'i sekme sekme, başlık başlık elle araştırıyordum. 90 dakika süren bölüm buydu. Bunu ortadan kaldırıyor.” -@itsjasonai

> “Tek bir beceri tüm araştırma akışımın yerini aldı. Konuyu veriyorsunuz; Reddit, X ve web'de insanların gerçekten konuştuğu şeyleri tarıyor. Eski blog yazılarını değil, son 30 günün gerçek konuşmalarını.” -@itswilsoncharles

> “Bugün GitHub'da öne çıkan 10 deponun 5'i Claude aracı. 1 numara: mvanhorn/last30days-skill” -@yieldhunter95

## Açık kaynak

MIT lisansı. İzleme yok. Analitik yok. Araştırmanız bilgisayarınızda kalır (aşağıdaki Veri işleme bölümüne bakın).

Test komutları için [CONTRIBUTING.md](CONTRIBUTING.md#tests) dosyasına bakın.

Python 3.12+, yt-dlp, Node.js (X araması için depoya dahil Bird istemcisi) ve ScrapeCreators API ile geliştirildi. v3 motor mimarisi: [@j-sperling](https://github.com/j-sperling).

PR açmak için [CONTRIBUTING.md](CONTRIBUTING.md), tüm topluluk katkıcıları için [CONTRIBUTORS.md](CONTRIBUTORS.md), sürüm geçmişi için [CHANGELOG.md](CHANGELOG.md) dosyalarına bakın.

## Veri işleme

Beceri varsayılan olarak tamamen bilgisayarınızda çalışır; çağırdığınız arama API'lerine (Reddit, ScrapeCreators vb.) gidenler dışında veri gönderilmez.

**Yeniden sıralama.** Bir akıl yürütme sağlayıcısı yapılandırdığınızda (`OPENAI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY` veya `GOOGLE_GENAI_API_KEY`), motor araştırma konusunu ve alınan içerik parçalarını ilgi puanlaması için sağlayıcıya gönderir. Bu isteğe bağlıdır: anahtar ayarlamazsanız yerel puanlamaya döner ve dış yeniden sıralama çağrısı yapmaz. Sağlayıcı önceliği için [CONFIGURATION.md](CONFIGURATION.md) dosyasına bakın.

**Yerel depo.** `LAST30DAYS_STORE=1` olduğunda bulgular etkin kayıt dizinindeki `research.db` SQLite veritabanında tutulur (`--save-dir`, `LAST30DAYS_MEMORY_DIR` değerinden önceliklidir). Etkin kayıt dizini yoksa `~/.local/share/last30days/research.db` kullanılır. Depoyu etkinleştirmek veritabanını uzak hizmete yüklemez. Saklama ve silme ayrıntıları, silmeden önce veritabanını kullananları durdurma dahil, [CONFIGURATION.md](CONFIGURATION.md#--store-flag) içindedir.

## Yıldız geçmişi

<a href="https://star-history.dera.page/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
