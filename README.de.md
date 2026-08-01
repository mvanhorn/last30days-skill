# /last30days

[English](README.md) | [Français](README.fr.md) | Deutsch | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

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

**Eine von einem KI-Agenten gesteuerte Suchmaschine, die nach Upvotes, Likes und echtem Geld gewichtet – nicht nach Redaktionen.**

Dieses README beschreibt die aktuelle v3-Pipeline. Die Laufzeitspezifikation der Skill liegt in [skills/last30days/SKILL.md](skills/last30days/SKILL.md) und ist maßgeblich für das aktuelle Verhalten von Befehlen und Setup.

**Claude Code (empfohlen – automatische Updates über den Marketplace):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI oder einer von 50+ [Agent Skills](https://agentskills.io)-Hosts:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` installiert global für deinen Benutzer, also in allen Projekten verfügbar. Lass das Flag weg, wenn du die Installation auf ein Projekt beschränken willst.)

Weitere Installationswege (claude.ai im Browser, OpenClaw, manuell) findest du unten im Abschnitt [Installation](#installation).

Null Konfiguration. Reddit, HN, Polymarket und GitHub funktionieren sofort. Führe die Skill einmal aus, und der Setup-Assistent schaltet X, YouTube, TikTok, arXiv, Techmeme und mehr in 30 Sekunden frei.

---

Upvotes von Reddit. Likes von X. YouTube-Transkripte. TikTok-Engagement. Polymarket-Quoten, gedeckt durch echtes Geld und Insiderwissen. Das sind Millionen Menschen, die jeden Tag mit ihrer Aufmerksamkeit und ihrem Geldbeutel abstimmen. /last30days durchsucht all das parallel, gewichtet nach dem, womit echte Menschen tatsächlich interagieren, und ein KI-Agent fasst es als Juror zu einem einzigen Briefing zusammen.

Google aggregiert Redaktionen. /last30days durchsucht Menschen.

Diese Suche bekommst du nirgendwo sonst, weil keine einzelne KI Zugriff auf alles hat. Google erfasst weder Reddit-Kommentare noch X-Beiträge. ChatGPT hat einen Deal mit Reddit, kann aber weder X noch TikTok durchsuchen. Gemini hat YouTube, aber kein Reddit. Claude hat nichts davon nativ. Jede Plattform ist ein abgeschotteter Garten mit eigener API, eigenen Tokens, eigener Authentifizierung. Aber du kannst deine eigenen Schlüssel und Browser-Sessions mitbringen – und plötzlich durchsucht ein KI-Agent alle gleichzeitig, wägt sie gegeneinander ab und sagt dir, was wirklich zählt.

Das ist der eigentliche Durchbruch. Keine bessere Suchmaschine, sondern ein Dutzend getrennter Plattformen, die ein Agent miteinander verbindet.

```
/last30days Peter Steinberger
```

Du hast morgen ein Meeting. Du googelst die Person. Du bekommst ihr LinkedIn-Profil von 2023. /last30days zeigt dir, was sie diesen Monat wirklich macht: bei OpenAI eingestiegen, um an Codex zu arbeiten, kämpft gegen Anthropics Verbot von Drittanbieter-Agenten, hat 23 PRs mit 85 % Merge-Rate geliefert, baut „LobsterOS“ für geräteübergreifende Agentensteuerung – und ein Thread in r/ClaudeCode kam auf 569 Upvotes bei der Frage, ob sie ein Held oder „unerträglich“ ist. Verteilt über X-Beiträge, Reddit-Threads, YouTube-Transkripte und GitHub-Commits. Nichts davon stand bei Google.

## Warum es das gibt

Ich habe es gebaut, um bei KI Schritt zu halten. Alles ändert sich täglich, und die Nerds auf Reddit und X wissen es immer zuerst. Ich brauchte bessere Prompts, und die Trainingsdaten lagen immer Monate hinter dem, was die Community längst herausgefunden hatte.

Daraus wurde etwas Größeres. Heute lasse ich es vor einem Sales-Call laufen, um die Wahrheit der letzten 30 Tage über ein Unternehmen zu kennen. Vor einem Meeting, um die aktuellen Tweets und Podcast-Transkripte meines Gegenübers zu lesen. Vor einer Reise nach Disney World, um zu wissen, welche Attraktionen geschlossen sind und was die Community über Genie+ sagt. Bevor ich irgendetwas baue, um zu wissen, an welchen Problemen die Leute wirklich hängen.

Wenn du dich mit einem CEO triffst: Hast du alle Tweets und YouTube-Transkripte der letzten 30 Tage gelesen? Ich schon.

## Quellen, gewichtet von den Menschen

| Quelle | Was dir die Menschen sagen |
|--------|--------------------------|
| **Reddit** | Die ungefilterte Meinung. Top-Kommentare mit echten Upvote-Zahlen, kostenlos, ohne API-Schlüssel. Die echten Meinungen, die Google vergräbt. |
| **X / Twitter** | Die spontane Einschätzung, der Experten-Thread, die erste Reaktion auf eine Eilmeldung. Zuerst informiert, zuerst am Streiten. |
| **YouTube** | Die 45-minütige Tiefenanalyse. Vollständige Transkripte, durchsucht nach den 5 zitierfähigen Sätzen, auf die es ankommt. |
| **TikTok** | Der Creator, der 3,6 Millionen Menschen mit einer Sichtweise erreicht, die du bei Google nie findest. |
| **Instagram Reels** | Die Perspektive der Influencer, inklusive Transkript des Gesprochenen. Das Signal der visuellen Kultur. |
| **Hacker News** | Der Konsens der Entwickler. 825 Punkte, 899 Kommentare. Wo technische Leute wirklich streiten. |
| **Polymarket** | Keine Meinungen. Quoten. Gedeckt durch echtes Geld. 96 % Wahrscheinlichkeit bei Albumverkäufen. 4 % bei einer Übernahme. |
| **GitHub** | Für Personen: PR-Tempo, Top-Repos nach Sternen, Release Notes. Für Themen: Issues und Discussions. |
| **Digg** | Kuratierte Story-Cluster aus Diggs AI-1000-Leaderboard (rund 1000 KI-Accounts mit hohem Signal auf X), mit zuordenbaren Inline-Zitaten und ganz ohne X-Authentifizierung. Wird automatisch aktiv, sobald `digg-pp-cli` im PATH liegt. |
| **arXiv** | Die Fachartikel hinter dem Hype. Neue Forschung im Zeitfenster, kostenlos, ohne API-Schlüssel. Wird automatisch aktiv, sobald `arxiv-pp-cli` im PATH liegt (das Erst-Setup installiert es). |
| **Techmeme** | Die redaktionelle Ebene der Tech-News, begrenzt auf dein 30-Tage-Fenster. Kostenlos, ohne API-Schlüssel. Wird automatisch aktiv, sobald `techmeme-pp-cli` im PATH liegt (das Erst-Setup installiert es). |
| **LinkedIn** | Das berufliche Signal. Beiträge und Artikel, wobei Artikel als starkes Signal gewichtet werden. |
| **StockTwits** | Die Stimmung der Trader. Aktiviert sich automatisch, wenn dein Thema ein Ticker oder eine Kryptowährung ist. |
| **Threads** | Die Textebene nach Twitter. Gespräche von Creators und Marken. |
| **Pinterest** | Visuelle Entdeckung. Pins, gespeicherte Beiträge und Kommentare zu Produkten und Ideen. |
| **Xiaohongshu (RED)** | Chinesische Signale zu Lifestyle, Produkten und Creators. Wird ausdrücklich mit `--search xhs` angefordert, wenn lokal ein eingeloggtes x-mcp-Browser-Plugin oder ein `xiaohongshu-mcp`-Dienst läuft. |
| **Bluesky** | Die dezentrale soziale Ebene. AT-Protocol-Beiträge aus der Abwanderung nach Twitter. |
| **Perplexity** | Belegte Sonar-Synthese, Rohtreffer der Search API und Deep Research. |
| **Web** | Die redaktionelle Berichterstattung, die Blog-Vergleiche. Ein Signal von vielen, nicht das einzige. |

Die Community steuert laufend weitere bei. Truth Social und andere Nischenquellen stecken bereits in der Engine, weitere folgen.

Ein Reddit-Thread mit 1.500 Upvotes ist ein stärkeres Signal als ein Blogbeitrag, den niemand gelesen hat. Ein TikTok mit 3,6 Millionen Aufrufen sagt mehr darüber aus, was kulturell relevant ist, als jede Pressemitteilung. Polymarket-Quoten mit 66.000 $ Handelsvolumen dahinter lassen sich schwerer wegdiskutieren als die Vermutung eines Kommentators.

Die Synthese sortiert nach dem, womit echte Menschen tatsächlich interagiert haben. Soziale Relevanz, nicht SEO-Relevanz.

## Wofür die Leute es wirklich nutzen

**Vor einem Meeting.** `/last30days Peter Steinberger` – beim Codex-Team von OpenAI eingestiegen, kämpft gegen Anthropics Verbot von Drittanbieter-Agenten, 23 PRs mit 85 % Merge-Rate auf GitHub gemergt, baut LobsterOS für geräteübergreifende Agentensteuerung. r/ClaudeCode: „Seit OpenClaw erschienen ist, war allgemein bekannt: Wer es über etwas anderes als die API laufen lässt, fliegt irgendwann raus“ (227 Upvotes). Das steht so nicht auf LinkedIn.

**Um Hiring-Signale zu lesen.** `/last30days Listen Labs --hiring-signals` – aktuelle Stellenanzeigen und Karriereseiten werden zu zitierten Belegen für Schwerpunktverschiebungen: Einstellungen in Enterprise Security, Customer Success, Infrastruktur oder Produktausbau. Der Bericht sagt, was das Hiring zu signalisieren scheint, nicht was die Roadmap liefern wird.

**Um ein Thema vor seinem Höhepunkt zu finden.** Frag `/last30days what's exploding in AI agents?`, und die Skill wechselt in den Discovery-Modus: Die Engine durchkämmt Reddit-Kategorielisten, die Front- und Best-Stories von Hacker News, Diggs AI-1000-Feed und X, sofern du authentifiziert bist. Dein Agent bewertet die Vorschläge (Namen, Müllfilterung, inhaltliche Relevanz) und schreibt Podcast- und X-Artikel-Ansätze. Am Ende bekommst du 5 bis 10 nach Velocity sortierte Themen. Jedes Ergebnis enthält quellenübergreifende Zahlen, ein Momentum-Label und einen startklaren Folgebefehl `/last30days "<topic>"`.

**Wenn etwas erscheint.** `/last30days Kanye West` – Großbritannien hat sein Visum blockiert, das Wireless Festival wurde abgesagt, die Sponsoren sind abgesprungen. Aber BULLY stieg auf Platz 2 der Billboard-Charts ein. Fantano kam aus seinem „Yay sabbatical“ zurück, um es zu rezensieren (653.000 Aufrufe). Beim SoFi Homecoming holte er Lauryn Hill und Travis Scott für 44 Songs auf die Bühne. Polymarket: „Wird Kanye wieder twittern?“ 86 % Ja. 23 Reddit-Threads, 17 YouTube-Videos, 86.000 Upvotes.

**Um Tools zu vergleichen.** `/last30days OpenClaw vs Hermes vs Paperclip` – „Das sind keine Konkurrenten, das sind Schichten.“ OpenClaw ist die ausführende Ebene (351.000 GitHub-Sterne, produktiv), Hermes ist das sich selbst verbessernde Gehirn (31.000 Sterne), Paperclip ist das Organigramm (49.000 Sterne). Die Sternzahlen kommen live aus der GitHub-API, nicht aus veralteten Blogbeiträgen. Vergleichstabelle mit Architektur, Speicher, Sicherheit und idealem Einsatzzweck. Laut @IMJustinBrooke: „OpenClaw = Glumanda, Hermes = Glurak.“

**Um die Welt zu verstehen.** `/last30days Iran vs USA` – Tag 38 des Krieges. Trumps Ultimatum bis Dienstag, damit der Iran die Straße von Hormus wieder öffnet. Zwei US-Kampfjets abgeschossen. Öl bei 126 $ pro Barrel. Die IEA nannte es „die größte Versorgungsstörung in der Geschichte des globalen Ölmarkts“. Polymarket: Waffenstillstand bis zum 31. Dezember bei 74 %. 27 X-Beiträge, 10 YouTube-Videos, 20 Prognosemärkte.

**Vor einer Reise.** `/last30days Universal Epic Universe` – die Erweiterung ist bereits im Bau. Baugenehmigung „Project 680“ eingereicht. Eine Feuerwerksshow ist über die Infrastruktur belegt, aber noch nicht angekündigt. Wartezeiten: Mine-Cart Madness im Schnitt 148 Minuten. Noch keine Jahreskarte, und die Einheimischen sind genervt. Stardust Racers steht bis zum 5. April wegen Renovierung still.

**Um schnell etwas zu lernen.** `/last30days Nano Banana Pro prompting` – JSON-strukturierte Prompts lösen den Tag-Wildwuchs ab. Das verschachtelte Format von @pictsbyai verhindert „Concept Bleeding“. Bearbeiten schlägt neu generieren. Und danach schreibt dir die Skill einen produktionsreifen Prompt, der genau das umsetzt, was die Community als funktionierend beschrieben hat.

## Was neu ist

Seit der Ankündigung von v3.3 im Mai und mit Stand v3.11.1 (Juli 2026): 175 gemergte PRs – 122 davon von 52 Beitragenden aus der Community – verteilt auf 15 Releases. Das ist gelandet.

### Erstklassig auf OpenAI Codex

/last30days ist jetzt ein natives Codex-Plugin mit geführtem Setup – keine Portierung, sondern ein vollwertiger Bürger. Renderer-bewusste Zitate sorgen dafür, dass die Codex-Ausgabe sich wie ein Briefing liest und nicht wie eine URL-Suppe (#694), und dieselbe Engine läuft auf Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClaw und 50+ Agent-Skills-Hosts. Codex-Plugin-Manifest von [@rfoust](https://github.com/rfoust) (#686), Codex-Auth-Fix von [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmeme und Digg – kostenlos, ohne API-Schlüssel

arXiv liefert die Fachartikel hinter dem Hype, Techmeme die redaktionelle Tech-News-Ebene – kostenlos, ohne einen einzigen Schlüssel, und das Erst-Setup installiert ihre CLIs, sodass sie sich von selbst aktivieren (#709). Diggs AI-1000-Story-Cluster kommen genauso ohne X-Authentifizierung an: Das Setup installiert dir die kostenlose Digg-CLI (#590). Trustpilot ist optional zuschaltbar für Recherchen zu Consumer-Marken.

### Reddit gratis, mit echten Scores und Top-Kommentaren

Reddits öffentliche .json-API ist gestorben; der kostenlose Weg kam stärker zurück. Schlüsselloses RSS plus Shreddit-Scraping (#457), gezielte Subreddit-Suche mit echten Upvote-Zahlen über arctic-shift (#696) und eine Relevanzschwelle, damit ein viraler Off-Topic-Beitrag dein Briefing nicht kapert (#488, danke [@rzachsmith](https://github.com/rzachsmith)). Kein API-Schlüssel. Echte Scores. Top-Kommentare inklusive.

### Die besten Kommentare in jedem Briefing

Kommentare sind jetzt eine quellenübergreifend standardmäßig aktive Ebene: Instagram-Kommentare mit rangbasierter Vielfalt, damit fünf zugespitzte Meinungen nicht alle aus einem einzigen Beitrag stammen (#751), YouTube-Kommentare plus ein Transkript-Backup über ScrapeCreators, falls yt-dlp scheitert (#637), und von der Community hochgevotete Kommentare, die in die Best-Takes-Wertung einfließen, damit die witzigsten Zeilen die Bewertung überleben (#592, #608).

### Ein einziger doctor-Befehl

Bitte um einen Health-Check: doctor prüft jede Quelle und verschreibt dann die genauen Korrekturen – welcher Schlüssel fehlt, welche CLI nicht im PATH liegt, welches Cookie abgelaufen ist (#753). Kein Rätselraten mehr, warum X so wenig geliefert hat.

### Die X-Suche, neu gebaut

Die X-Pipeline wurde von Grund auf überarbeitet: FROM- und ABOUT-Lanes, damit sowohl die eigenen Beiträge einer Person als auch das Gespräch über sie einsortiert werden (#610), personenbezogene Auflösung mehrdeutiger Unterabfragen (#611), Verifizierung der Urheberschaft aus erster Hand samt Ranking nach Interaktionssignalen (#613) und eine einzige X-Quelle mit automatischem Backend-Failover (#622). Dazu ein ehrliches `--diagnose`, das die Authentifizierung wirklich prüft (#609).

### Weitere Quellen sind dazugekommen

LinkedIn über ScrapeCreators, mit Artikeln als starkem Signal ([@ravstr](https://github.com/ravstr), #702). StockTwits aktiviert sich automatisch bei Ticker- und Krypto-Themen ([@wtiwana](https://github.com/wtiwana), #658). Perplexity hat direkte API-Modi und asynchrone Deep Research dazubekommen ([@sk-holmes](https://github.com/sk-holmes), #629).

### Von der Community gehärtet

Die Sicherheitswelle war fast vollständig Community-Arbeit: Fixes für Stored XSS im HTML-Renderer ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), abgesicherte temporäre Cookie-Dateien, eine gegen Supply-Chain-Angriffe gehärtete CI mit OpenSSF Scorecard und Build-Provenance-Attestierung ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), Semgrep- und OSV-Scanner-Scans plus ein Dependency-Review-Gate für jeden PR ([@23241a6749](https://github.com/23241a6749)), eine Mindestgrenze für die Testabdeckung, eingeführt bei 60 % und inzwischen auf 84 % angehoben ([@gourab5139014](https://github.com/gourab5139014)), und ein Hermes-Sicherheitsscan, der inzwischen keinen einzigen CRITICAL-Befund mehr enthält (#768).

### Reicht weiter

Hebräisch und andere nichtlateinische Sprachen ([@dudyme](https://github.com/dudyme)). CJK-taugliche Tokenisierung für chinesische Quellen ([@An-idd](https://github.com/An-idd)). Eine Welle an Windows-Kompatibilität. Cookie-Extraktion für die gesamte Chromium-Familie – Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) – plus macOS Keychain und pass(1) unter Linux als Quellen für Zugangsdaten. Historischer Rückblick mit `--as-of` ([@chiyi-creator](https://github.com/chiyi-creator)). Automatisch bereitgestelltes Python 3.12 über uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` zum Auslesen der Stellenseiten eines Unternehmens. Watchlist-Deltas zwischen zwei Durchläufen.

### Weiterhin ab Werk dabei seit v3

Die Grundlagen aus v3 sind alle noch da: das Pre-Research-Hirn, das die richtigen Handles, Subreddits und Hashtags ermittelt, bevor ein einziger API-Aufruf rausgeht (gebaut von [@j-sperling](https://github.com/j-sperling)); die Best-Takes-Wertung, die Humor und Viralität neben Relevanz berücksichtigt; quellenübergreifendes Cluster-Merging; Vergleiche in einem Durchgang („CLI vs MCP“ in 3 Minuten statt 12); automatisch gefundene `--competitors`-Vergleiche; der GitHub-Personenmodus (`--github-user=steipete`); der ELI5-Modus („eli5 on“ nach jedem Durchlauf); und teilbare, in sich geschlossene HTML-Briefings (`--emit=html`). Die Konfigurationsschalter stehen in [CONFIGURATION.md](CONFIGURATION.md).

## Installation

| Umgebung | Installation | Updates |
|---------|---------|---------|
| **Claude Code** (empfohlen) | `/plugin marketplace add mvanhorn/last30days-skill` | Automatisch über den Marketplace, oder `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill`, dann `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI oder einer von 50+ [Agent Skills](https://agentskills.io)-Hosts** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (Browser) | [`last30days.skill` herunterladen](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) und über claude.ai > Customize > Skills > + > Create skill > Upload a skill hochladen | Neu herunterladen und erneut hochladen |
| **Claude Desktop** | [Die `.mcpb` für deine Plattform herunterladen](https://github.com/mvanhorn/last30days-skill/releases/latest) und in Settings > Extensions ziehen | Neu herunterladen und das neue Bundle hineinziehen |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (empfohlen)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Empfohlen, weil der Claude-Code-Marketplace die Updates für dich übernimmt: Der Plugin-Cache ist versioniert und aktualisiert sich automatisch, sobald ein neues Release erscheint. Mit `claude plugin update last30days@last30days-skill` erzwingst du eine Prüfung.

Wenn du lieber den Agent-Skills-Installationsweg unter Claude Code nutzt, wird auch der unterstützt:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

Das native Plugin und die `npx skills`-Installation können nebeneinander existieren. Beachte aber: Claude Code dedupliziert nicht über Installationsmethoden hinweg. Wenn sowohl das Marketplace-Plugin als auch die `npx skills`-Kopie aktiv sind, taucht `/last30days` doppelt auf. Nutze pro Rechner eine Installationsmethode.

### Grok (xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) installiert last30days als natives Plugin. Die direkte Installation folgt dem Repository:

```bash
grok plugin install mvanhorn/last30days-skill
```

Oder füge dieses Repository als Marketplace-Quelle hinzu und installiere anschließend über den Plugin-Namen:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Mit `--trust` überspringst du die Installationsbestätigung. Aktualisieren kannst du mit `grok plugin update last30days`. Grok liest aus Kompatibilitätsgründen auch die Claude-Code-Manifeste; das native `.grok-plugin/`-Paar ist der bevorzugte Weg – und genau darauf verweist ein offizieller Eintrag im [xAI-Marketplace](https://github.com/xai-org/plugin-marketplace). `npx skills add` bleibt ein gültiger Fallback über alle Hosts hinweg.

### Codex, Cursor, Copilot, Gemini CLI und weitere Agent-Skills-Hosts

Installiere über die offene [Agent Skills](https://agentskills.io)-CLI – sie unterstützt 50+ Hosts, darunter `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose` und weitere (vollständige Liste im [Repository vercel-labs/skills](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

Das Flag `-g` (global) installiert in dein Benutzerverzeichnis, sodass die Skill in allen Projekten verfügbar ist. Ohne `-g` installiert `npx skills` projektlokal nach `./.skills/` (und wird mit dem Repository eingecheckt). Für ein Werkzeug, mit dem du die ganze Welt recherchierst, willst du die globale Installation.

Codex Desktop und andere Hosts, die auf Ordnerebene arbeiten, funktionieren sowohl in gewöhnlichen Ordnern als auch in Git-Repositories. Bitte den Host-Agenten vor der ersten Recherche, das mitgelieferte `scripts/last30days.py --preflight` aus dem geladenen Skill-Verzeichnis auszuführen; in einem Checkout des Quellcodes lautet der entsprechende Befehl `python3 skills/last30days/scripts/last30days.py --preflight`. Er zeigt dir, woher die Konfiguration stammt, welche Browser-Cookies gelesen würden, welche Dateien geschrieben würden, welche optionalen Befehle es gibt und welche Projektkonfiguration ignoriert wird – ohne Cookies zu lesen, Dateien zu schreiben oder eine Recherche zu starten.

Standardmäßig wird für den Host installiert, den `npx skills` erkennt. Um gezielt einen (oder mehrere) anzusprechen:

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Später aktualisieren mit:

```bash
npx skills update last30days -g
```

Oder aktualisiere alles, was du global über `npx skills` installiert hast:

```bash
npx skills update -g
```

Auflisten und entfernen kannst du mit `npx skills list -g` und `npx skills remove last30days -g`.

### claude.ai (Browser)

1. [`last30days.skill` herunterladen](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) – aus dem neuesten Release
2. Geh zu [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Klicke im Skills-Panel auf `+`, dann auf `Create skill` > `Upload a skill`, und wähle die Datei aus oder zieh sie hinein

Aktiviere vorher unter Capabilities die Option „Code execution and file creation“ – ohne sie laufen Skills nicht.

### Claude Desktop

Claude Desktop installiert `/last30days` als MCP-Server über ein `.mcpb`-Bundle (ein Model-Context-Protocol-Paket zum Ein-Klick-Installieren).

1. Öffne das [neueste Release](https://github.com/mvanhorn/last30days-skill/releases/latest) und lade die `.mcpb` für deine Plattform herunter:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Öffne Claude Desktop, geh zu Settings > Extensions und zieh die Datei hinein.
3. Füge auf Nachfrage die API-Schlüssel für die Quellen ein, die du aktivieren willst. Jedes Feld ist optional – überspringst du alle, fällt die Engine auf den reinen Web-Modus zurück. Die Schlüssel landen im Schlüsselbund deines Betriebssystems.
4. Starte Claude Desktop neu. Bitte Claude, „zu Peter Steinberger zu recherchieren“ oder zu einem beliebigen anderen Thema, und es ruft das Tool `research` auf.

**Voraussetzung auf dem Host:** Python 3.12+ im PATH. Das Bundle bringt den Quellcode der Engine mit, nutzt aber deinen lokalen Python-Interpreter. Unter Windows installierst du ihn von [python.org](https://www.python.org/downloads/); macOS und die meisten Linux-Distributionen bringen bereits eine kompatible Version mit.

**Die Schlüssel werden nicht mit der Claude-Code-Skill geteilt.** Claude Desktop und Claude Code halten bewusst getrennte Speicher für Zugangsdaten. Wenn du `~/.config/last30days/.env` bereits für die Claude-Code-Skill eingerichtet hast, gibst du dieselben Schlüssel hier einmalig erneut ein.

Windows-Unterstützung ist zurückgestellt, bis die plattformspezifischen Einstiegspunkte im Manifest geklärt sind; verfolgt wird das in einem eigenen Issue.

### OpenClaw

```bash
clawhub install last30days-official
```

Für X/Twitter-Aktionen außerhalb der `/last30days`-Recherche – Tweets oder
Antworten posten, Follower exportieren, Medien verwalten, Accounts beobachten
und Verlosungen auswerten – nutzt du [TweetClaw](https://github.com/Xquik-dev/tweetclaw)
als ergänzendes OpenClaw-Plugin. TweetClaw wird von Xquik-dev gepflegt und ist
hier nur als optionale Ergänzung aufgeführt, nicht als Abhängigkeit oder
Empfehlung von last30days.

### Manuell (für Entwickler)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

Der Symlink hält die Installation beim Bearbeiten mit deinem Arbeitsverzeichnis synchron – erneutes Kopieren entfällt. Für `claude.ai` baust du die `.skill`-Datei aus dem Quellcode: `bash skills/last30days/scripts/build-skill.sh` erzeugt `dist/last30days.skill`.

Reddit (mit Kommentaren), Hacker News, Polymarket und GitHub funktionieren sofort. Null Konfiguration. Führe `/last30days` einmal aus, und der Setup-Assistent schaltet in 30 Sekunden weitere Quellen frei, darunter die kostenlosen CLIs für arXiv und Techmeme.

## Bring deine eigenen Schlüssel mit

Diese Plattformen haben nichts miteinander zu tun. X weiß nicht, was Reddit denkt. YouTube sieht TikTok nicht. Aber du kannst deine eigenen API-Schlüssel und Browser-Tokens mitbringen – und hast auf einen Schlag Zugriff auf alle gleichzeitig.

| Quellen | Was du brauchst | Kosten |
|---------|---------------|------|
| Reddit (mit Kommentaren) + HN + Polymarket + GitHub + StockTwits | Nichts | Kostenlos |
| arXiv + Techmeme | Kostenlose CLIs, die das Erst-Setup automatisch installiert | Kostenlos |
| X / Twitter | In einem beliebigen Browser bei x.com anmelden, oder `XQUIK_API_KEY` / `XAI_API_KEY` setzen | Browser-Cookies sind kostenlos; Schlüssel hängen vom Anbieter ab |
| YouTube | `brew install yt-dlp` | Kostenlos |
| Bluesky | App-Passwort von bsky.app | Kostenlos |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube-Kommentare | Ein ScrapeCreators-Schlüssel | 10.000 kostenlose Aufrufe, danach nutzungsabhängig |
| Xiaohongshu (RED) | Ein eingeloggtes x-mcp-Browser-Plugin oder einen `xiaohongshu-mcp`-Dienst laufen lassen und die Quelle mit `--search xhs` pro Durchlauf oder `INCLUDE_SOURCES=xiaohongshu` in `.env` zuschalten; last30days probiert automatisch `http://localhost:18060` und danach `http://host.docker.internal:18060`, oder du setzt `XIAOHONGSHU_API_BASE` für eine eigene URL | Kein last30days-API-Schlüssel nötig; hängt von deinem lokalen Browser-Session-Dienst ab |
| DripStack (Premium-Finanznewsletter) | Zuschaltbar: `--search dripstack` pro Durchlauf, oder `INCLUDE_SOURCES=dripstack` in `.env` | Kein Schlüssel; kostenlose öffentliche Such-API |
| Perplexity Sonar / Search API / Deep Research | Ein Perplexity-Schlüssel, oder ein OpenRouter-Schlüssel als Sonar-Fallback | Nutzungsabhängig |
| Websuche | Ein Brave-Search-Schlüssel | 2.000 kostenlose Anfragen pro Monat |

### macOS Keychain (optional)

Unter macOS kannst du Schlüssel im System-Schlüsselbund statt in einer `.env`-Datei ablegen. Die Skill liest sie automatisch aus, allerdings mit der niedrigsten Priorität – bei einer Kollision gewinnen weiterhin `.env`-Dateien und die Prozessumgebung.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Die Einträge werden für den aktuellen Benutzer unter dem Dienstnamen `last30days-<KEY>` gespeichert. Auf Nicht-Darwin-Plattformen tut der Loader nichts, für Linux- und Windows-Nutzer ändert sich also am Verhalten nichts.

Du hast bereits Schlüssel unter anderen Keychain-Dienstnamen? Dann setz das nicht geheime Mapping `LAST30DAYS_KEYCHAIN_ALIASES`, das in [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) beschrieben ist, statt Geheimnisse zu kopieren.

Die vollständige Schlüsselmatrix pro Quelle, die Priorität der Reasoning-Anbieter und die Priorität der Websuche-Backends stehen in [CONFIGURATION.md](CONFIGURATION.md).

## Konfiguration

Zwei Dinge, die du vermutlich schon am ersten Tag wissen willst:

**Wo die Rechercheergebnisse landen.** `LAST30DAYS_MEMORY_DIR` zeigt standardmäßig auf `~/Documents/Last30Days/` (unter Windows: `C:\Users\<you>\Documents\Last30Days\`). Überschreib das, indem du die Umgebungsvariable in deiner Shell auf einen beliebigen Pfad setzt, oder mit `--save-dir <path>` pro Durchlauf. Nutze `--output <file>`, wenn du das gerenderte Ergebnis an einem exakten Pfad brauchst – im Format, das `--emit` vorgibt. Mit `--save-suffix=<name>` hältst du mehrere Varianten desselben Themas auseinander (etwa pro Kunde). Jeder Durchlauf mit `--save-dir` erzeugt `<slug>-raw[-suffix].md`. Mit `python3 skills/last30days/scripts/last30days.py --preflight` siehst du vor einer Recherche, welche Dateien geschrieben würden.

**Strukturierte Ausgabe für Agenten und Workflows.** Bitte `/last30days` um maschinenlesbares JSON, dann bekommst du das stabile, versionierte Agentenprofil. Für den direkten Einsatz der Engine in Skripten oder in der Entwicklung führst du `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json` aus; `--json-profile=raw` brauchst du nur, wenn du den unversionierten internen `Report`-Dump willst. Siehe die [Feldreferenz des JSON-Exports samt Versionierungsrichtlinie](docs/reference/json-export.md).

**Discovery ohne festes Thema.** Frag `/last30days what's trending in AI agents?`, um ein sortiertes Discovery-Briefing zu bekommen, statt ein Thema zu recherchieren, das du ohnehin kennst. Auf einem Agenten-Host läuft dafür das dreistufige, vom Host bewertete Protokoll (das Modell benennt Themen, filtert Müll heraus, bewertet ihre Relevanz und schreibt die inhaltlichen Ansätze). Für den direkten Einsatz der Engine in Skripten oder per Cron führst du `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` aus (einmaliger Lauf: deterministische Themennamen, keine Ansätze); mit `--emit=json` bekommst du den versionierten Discovery-Vertrag. Discovery schließt ein positionsbasiertes Thema und `--drill` gegenseitig aus.

**Trendbeobachtung über mehrere Durchläufe.** Der Standardmodus erzeugt pro Durchlauf einen frischen Markdown-Snapshot. Um Erkenntnisse über die Zeit zu sammeln, hängst du `--store` an, damit sie in einer SQLite-Datenbank landen, und nutzt dann [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) für geplante Durchläufe (auf Wunsch mit Zustellung per Slack oder Webhook bei neuen Funden) sowie [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) für tägliche oder wöchentliche Zusammenfassungen. Das vollständige Taktmuster steht in [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Eine abonnierbare Recherche-Bibliothek.** Bitte `/last30days`, deinen Bibliotheks-Feed zu bauen, oder nutze für Skripting und Entwicklung direkt `python3 skills/last30days/scripts/last30days.py library feed`. Das verwandelt gespeicherte Briefings in eine `index.html`, ein lokales Atom-`feed.xml` und lesbare Briefing-Seiten. Hänge `--publish` nur an, wenn der HTML-Index und die Briefing-Seiten gehostet werden sollen; das Veröffentlichen ist eine bewusste Entscheidung und standardmäßig öffentlich. Damit der Atom-Feed wirklich abonnierbar wird, hoste das erzeugte Ausgabeverzeichnis bei einem statischen Anbieter wie GitHub Pages.

**Durchsuche alles, was du schon recherchiert hast.** Frag `/last30days search my library for MCP servers` oder `/last30days have I researched MCP servers before?`. Für den direkten Einsatz der Engine führst du `python3 skills/last30days/scripts/last30days.py library search "MCP servers"` aus. Die Suche läuft offline und deterministisch: Sie indexiert nach und nach dieselben gespeicherten Briefings, die auch der Bibliotheks-Feed nutzt, führt passende Treffer aus dem Store je Durchlauf zusammen und gruppiert die Ergebnisse nach Thema und Datum. Neue Durchläufe blenden außerdem einen kompakten Abschnitt **From your library** („aus deiner Bibliothek“) ein, wenn frühere Recherchen das aktuelle Thema überschneiden; mit `LAST30DAYS_LIBRARY_CONTEXT=off` schaltest du diesen passiven Kontext ab.

Wrapper-Skripte pro Kunde, eigene Kategorie-Subreddits und der experimentelle Beta-Kanal für Anpassungen in Arbeit sind ebenfalls in [CONFIGURATION.md](CONFIGURATION.md) dokumentiert.

## Showcase: Recherche-Feeds aus der Community

Du hast mit last30days ein wiederkehrendes KI-Update, eine Marktbeobachtung oder eine herrlich spezielle Obsession veröffentlicht? Teil die URL deiner öffentlichen Bibliothek – oder die Atom-URL, sobald `feed.xml` bei einem statischen Anbieter liegt – im [Showcase-Thread der Community](https://github.com/mvanhorn/last30days-skill/issues/532). Community-Feeds werden hier verlinkt, sobald ihre Besitzer sie einreichen; bis dahin ist der Thread die Sammelstelle.

## So funktioniert es

1. **Du tippst ein Thema ein.** Person, Unternehmen, Produkt, Technologie, „X vs Y“. Alles ist möglich.
2. **Der Agent klärt, wer zählt.** Er findet X-Handles (auch die von Gründerinnen und Gründern), GitHub-Repos, Subreddits, TikTok-Hashtags und YouTube-Kanäle. Bei „Kanye West“ weiß er, dass r/hiphopheads, @kanyewest und „bully review“ auf YouTube dazugehören. Bei „OpenClaw“ löst er openclaw/openclaw auf GitHub auf und holt die aktuellen Sternzahlen.
3. **Alle Quellen werden parallel durchsucht.** Erweiterung über mehrere Suchanfragen. Ergebnisse gewichtet nach Engagement, Relevanz und Aktualität.
4. **Die Tiefe, die sonst niemand hat.** Vollständige YouTube-Transkripte aus Reaktionsvideos. Die besten Reddit-Kommentare samt Upvote-Zahlen. TikTok-Captions. Polymarket-Quoten. Nicht nur Titel und Links.
5. **Dieselbe Geschichte, zusammengeführt.** Das Wireless Festival auf Reddit angekündigt, auf X diskutiert, Ticketpreise auf TikTok – das ergibt einen Cluster, nicht drei getrennte Einträge.
6. **Zu einem Briefing verdichtet.** Auf konkreten Daten fußend. Nach Quelle belegt. Sortiert nach dem, womit Menschen wirklich interagieren. Nicht „hier ist, was ich gefunden habe“, sondern „hier ist, was zählt“.
7. **Danach wird es dein Experte.** Nach einem einzigen Durchlauf weiß deine Claude-Sitzung alles, was die Community weiß. Stell Rückfragen. Lass sie Prompts schreiben, E-Mails entwerfen, Reisen planen, Systeme entwerfen – immer verankert in dem, was gerade wirklich stimmt.

## Was die Leute sagen

> „Ich habe eine Claude-Code-Skill gefunden, die zu jedem Thema die letzten 30 Tage auf Reddit, X, YouTube und HN recherchiert. Und dann schreibt sie dir die Prompts. Vor jedem Text, den ich schreibe, habe ich das bisher von Hand auf Reddit und X gemacht. Tab für Tab. Thread für Thread. Genau das ist der Teil, der 90 Minuten frisst. Der fällt jetzt weg.“ – @itsjasonai

> „Diese eine Skill hat meinen kompletten Recherche-Workflow ersetzt. Du gibst ihr ein Thema, sie holt sich von Reddit, X und dem Web, worüber die Leute wirklich reden. Keine alten Blogbeiträge. Echte Gespräche aus den letzten 30 Tagen.“ – @itswilsoncharles

> „5 der 10 Trending-Repos heute auf GitHub sind Claude-Tools. Nummer 1: mvanhorn/last30days-skill“ – @yieldhunter95

## Open Source

MIT-Lizenz. Kein Tracking. Keine Analytics. Deine Recherche bleibt auf deinem Rechner. Über 2.700 Tests.

Gebaut mit Python 3.12+, yt-dlp, Node.js (mitgelieferter Bird-Client für die X-Suche) und der ScrapeCreators-API. Architektur der v3-Engine von [@j-sperling](https://github.com/j-sperling).

Wie du einen PR aufmachst, steht in [CONTRIBUTING.md](CONTRIBUTING.md), die vollständige Liste der Community-Beitragenden in [CONTRIBUTORS.md](CONTRIBUTORS.md) und die Versionshistorie in [CHANGELOG.md](CHANGELOG.md).

## Sternverlauf

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
