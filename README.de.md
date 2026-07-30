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

**Eine von KI-Agenten geführte Suchmaschine, die durch Upvotes, Likes und echtes Geld bewertet wird – nicht durch Redakteure.**

Diese README-Datei verfolgt die aktuelle v3-Pipeline. Die Laufzeit-Skill-Spezifikation befindet sich in [skills/last30days/SKILL.md](skills/last30days/SKILL.md), was die Quelle der Wahrheit für das neueste Befehls- und Setup-Verhalten ist.

**Claude Code (empfohlen – automatische Updates über den Marktplatz):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI oder einer von über 50 [Agent Skills](https://agentskills.io)-Hosts:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` wird global für Ihren Benutzer installiert und ist in allen Projekten verfügbar. Legen Sie es auf den Bereich pro Projekt ab.)

Weitere Installationsoptionen (claude.ai web, OpenClaw, manuell) finden Sie unten im Abschnitt [Installation](#installieren).

Keine Konfiguration. Reddit, HN, Polymarket und GitHub funktionieren sofort. Führen Sie es einmal aus und der Einrichtungsassistent schaltet X, YouTube, TikTok, arXiv, Techmeme und mehr in 30 Sekunden frei.

---

Reddit-Upvotes. X mag. YouTube-Transkripte. TikTok-Engagement. Polymarket-Quoten, gestützt durch echtes Geld und Insiderinformationen. Das sind Millionen von Menschen, die jeden Tag mit ihrer Aufmerksamkeit und ihrem Geldbeutel abstimmen. /last30days durchsucht alles parallel, bewertet es danach, womit sich echte Menschen tatsächlich beschäftigen, und ein KI-Agent-Juror fasst es in einem Briefing zusammen.

Google fasst Redakteure zusammen. /last30days durchsucht Personen.

Sie können diese Suche nirgendwo anders erhalten, da keine einzelne KI Zugriff auf alles hat. Die Google-Suche berührt keine Reddit-Kommentare oder X-Beiträge. ChatGPT hat einen Deal mit Reddit, kann aber weder X noch TikTok durchsuchen. Gemini hat YouTube, aber nicht Reddit. Claude hat von Haus aus keine davon. Jede Plattform ist ein ummauerter Garten mit eigener API, eigenen Token und eigener Authentifizierung. Aber Sie können Ihre eigenen Schlüssel und Browsersitzungen mitbringen, und plötzlich kann ein KI-Agent alle auf einmal durchsuchen, sie miteinander vergleichen und Ihnen sagen, worauf es wirklich ankommt.

Das ist die Freischaltung. Keine bessere Suchmaschine. Ein Dutzend nicht verbundener Plattformen, überbrückt von einem Agenten.

```
/last30days Peter Steinberger
```

Du hast morgen ein Treffen. Sie googeln sie. Sie erhalten ihr LinkedIn ab 2023. /last30days zeigt Ihnen, was sie diesen Monat tatsächlich tun: Sie sind OpenAI beigetreten, um an Codex zu arbeiten, kämpfen gegen das Verbot von Drittanbieter-Agenten durch Anthropic, versenden 23 PRs mit einer Zusammenführungsrate von 85 %, entwickeln „LobsterOS“ für die geräteübergreifende Agentenkontrolle und r/ClaudeCode erreichte 569 positive Stimmen bei der Debatte darüber, ob er ein Held oder „unerträglich“ ist. Verstreut über X-Posts, Reddit-Threads, YouTube-Transkripte und GitHub-Commits. Nichts davon war bei Google.

## Warum das existiert

Ich habe es gebaut, um in der KI mithalten zu können. Alles ändert sich jeden Tag und die Reddit- und X-Nerds sind immer die Ersten, die den Überblick behalten. Ich brauchte bessere Eingabeaufforderungen und die Trainingsdaten lagen immer Monate hinter dem zurück, was die Community bereits herausgefunden hatte.

Aber es wurde etwas Größeres. Jetzt führe ich es vor einem Verkaufsgespräch durch, um die Wahrheit über ein Unternehmen in den letzten 30 Tagen zu erfahren. Vor einem Meeting die neuesten Tweets und Podcast-Transkripte einer anderen Person lesen. Informieren Sie sich vor einer Disney World-Reise darüber, welche Fahrgeschäfte geschlossen sind und was die Community über Genie+ sagt. Bevor ich etwas baue, muss ich wissen, auf welche Probleme die Leute tatsächlich stoßen.

Wenn Sie sich mit einem CEO treffen, haben Sie dann alle Tweets und YouTube-Transkripte der letzten 30 Tage gelesen? Ich habe.

## Quellen, bewertet von den Leuten

| Quelle | Was die Leute dir sagen |
|--------|--------------------------|
| **Reddit** | Der ungefilterte Take. Top-Kommentare mit echten Upvote-Zählungen, kostenlos, kein API-Schlüssel. Die wahren Meinungen, die Google vergräbt. |
| **X / Twitter** | Der heiße Take, der Expertenthread, die bahnbrechende Reaktion. Zuerst wissen, zuerst argumentieren. |
| **YouTube** | Der 45-minütige Tieftauchgang. Vollständige Transkripte wurden nach den fünf zitierfähigen Sätzen durchsucht, auf die es ankommt. |
| **TikTok** | Der Ersteller erreicht 3,6 Millionen Menschen mit einer Einstellung, die Sie bei Google nie finden werden. |
| **Instagram-Reels** | Die Influencer-Perspektive mit Spoken-Word-Transkripten. Das visuelle Kultursignal. |
| **Hacker-News** | Der Entwicklerkonsens. 825 Punkte, 899 Kommentare. Wo technische Leute tatsächlich streiten. |
| **Polymarkt** | Keine Meinungen. Chancen. Unterstützt durch echtes Geld. 96 % Vertrauen in die Albumverkäufe. 4 % bei einer Akquisition. |
| **GitHub** | Für Leute: PR-Geschwindigkeit, Top-Repos nach Stars, Versionshinweise. Für Themen: Probleme und Diskussionen. |
| **Digg** | Kuratierte Story-Cluster aus Diggs AI 1000-Bestenliste (~1000 AI-Konten mit hohem Signalwert auf X) mit zuordenbaren Inline-Zitaten (keine X-Authentifizierung erforderlich). Automatisch aktiviert, wenn sich `digg-pp-cli` auf PATH befindet. |
| **arXiv** | Die Papiere hinter dem Hype. Neue Recherche im Fenster, kostenlos, kein API-Schlüssel. Automatisch aktiviert, wenn sich `arxiv-pp-cli` auf PATH befindet (das Setup beim ersten Start installiert es). |
| **Techmeme** | Die redaktionelle Ebene der Tech-News, mit Datumsfenster für Ihre 30 Tage. Kostenlos, kein API-Schlüssel. Automatisch aktiviert, wenn sich `techmeme-pp-cli` auf PATH befindet (das Setup beim ersten Start installiert es). |
| **LinkedIn** | Das professionelle Signal. Beiträge und Artikel, wobei die Artikel als hohes Signal gewichtet werden. |
| **StockTwits** | Händlerstimmung. Wird automatisch aktiviert, wenn es sich bei Ihrem Thema um einen Ticker oder eine Kryptowährung handelt. |
| **Threads** | Die Post-Twitter-Textebene. Gespräche von YouTubern und Marken. |
| **Pinterest** | Visuelle Entdeckung. Pins, Speicherungen und Kommentare zu Produkten und Ideen. |
| **Xiaohongshu (ROT)** | Chinesische Lifestyle-, Produkt- und Schöpfersignale. Wird explizit mit `--search xhs` angefordert, wenn ein angemeldetes x-mcp-Browser-Plugin oder ein `xiaohongshu-mcp`-Dienst lokal ausgeführt wird. |
| **Bluesky** | Die dezentrale soziale Schicht. AT-Protokollbeiträge aus der Post-Twitter-Migration. |
| **Perplexität** | Grounded Sonar-Synthese, rohe Such-API-Zeilen und Deep Research. |
| **Web** | Die redaktionelle Berichterstattung, die Blog-Vergleiche. Ein Signal von vielen, nicht das einzige. |

Community-Mitwirkende fügen immer mehr hinzu. Truth Social und andere Nischenquellen sind in Arbeit, weitere sind in Vorbereitung.

Ein Reddit-Thread mit 1.500 Upvotes ist ein stärkeres Signal als ein Blog-Beitrag, den niemand liest. Ein TikTok mit 3,6 Millionen Aufrufen verrät Ihnen mehr darüber, was kulturell relevant ist, als eine Pressemitteilung. Mit Polymarket-Quoten, die durch ein Volumen von 66.000 US-Dollar untermauert werden, lässt sich schwerer argumentieren als mit der Vermutung eines Experten.

Die Synthese orientiert sich daran, womit sich echte Menschen tatsächlich beschäftigt haben. Soziale Relevanz, nicht SEO-Relevanz.

## Wofür die Leute es tatsächlich verwenden

**Vor einem Meeting.** `/last30days Peter Steinberger` – trat dem Codex-Team von OpenAI bei und kämpfte gegen das Verbot von Drittanbieter-Agenten durch Anthropic. 23 PRs wurden mit einer Zusammenführungsrate von 85 % auf GitHub zusammengeführt und LobsterOS für die geräteübergreifende Agentensteuerung entwickelt. r/ClaudeCode: „Seit der Veröffentlichung von OpenClaw war allgemein bekannt, dass man irgendwann gesperrt wird, wenn man es über etwas anderes als die API ausführt“ (227 positive Stimmen). Das ist nicht auf LinkedIn.

**Zum Lesen von Einstellungssignalen.** `/last30days Listen Labs --hiring-signals` – Aktuelle Stellen- und Karriereseiten werden zu zitierten Beweisen für Schwerpunktverlagerungen: Einstellungen in den Bereichen Unternehmenssicherheit, Kundenerfolg, Infrastruktur oder Produkterweiterung. Der Bericht sagt, was die Einstellung zu signalisieren scheint, nicht was die Roadmap bewirken wird.

**Um das Thema zu finden, bevor es seinen Höhepunkt erreicht.** Fragen Sie `/last30days what's exploding in AI agents?` und der Skill wechselt in den Entdeckungsmodus: Die Engine durchsucht Reddit-Kategorielisten, Hacker News-Front/Best-Storys, Diggs AI 1000-Feed und X, wenn sie authentifiziert ist; Ihr Agent beurteilt die Nominierungen (Namen, Junk-Filterung, Inhaltswürdigkeit) und schreibt Podcast-/X-Artikel-Betrachtungen; dann erhalten Sie 5–10 Themen mit Geschwindigkeitsranking. Zu jedem Ergebnis gehören quellenübergreifende Zahlen, ein Momentum-Label und ein sofort einsatzbereites `/last30days "<topic>"`-Follow-up.

**Wenn etwas ausfällt.** `/last30days Kanye West` – Großbritannien hat sein Visum blockiert, das Wireless Festival abgesagt, Sponsoren sind geflohen. Aber BULLY debütierte auf Platz 2 der Billboard-Charts. Fantano kam von seinem „Yay Sabbatical“ zurück, um es zu rezensieren (653.000 Aufrufe). SoFi Homecoming brachte Lauryn Hill und Travis Scott für 44 Songs heraus. Polymarket: „Wird Kanye erneut twittern?“ 86 % Ja. 23 Reddit-Threads, 17 YouTube-Videos, 86.000 Upvotes.

**Zum Vergleichen von Werkzeugen.** `/last30days OpenClaw vs Hermes vs Paperclip` – „Das sind keine Konkurrenten, es sind Schichten.“ OpenClaw ist der Vollstrecker (351.000 GitHub-Sterne, live), Hermes ist das sich selbst verbessernde Gehirn (31.000 Sterne), Paperclip ist das Organigramm (49.000 Sterne). Die Anzahl der Sterne wird live von der GitHub-API abgerufen, nicht von veralteten Blogbeiträgen. Side-by-Side-Tisch mit Architektur, Speicher, Sicherheit, Best-for. Per @IMJustinBrooke: „OpenClaw = Charmander, Hermes = Charizard.“

**Um die Welt zu verstehen.** `/last30days Iran vs USA` – Tag 38 des Krieges. Trumps Frist für die Wiedereröffnung der Straße von Hormus durch den Iran am Dienstag. Zwei US-Kampfflugzeuge abgeschossen. Öl bei 126 $/Barrel. Die IEA nannte es „die größte Versorgungsstörung in der Geschichte des globalen Ölmarktes“. Polymarket: Waffenstillstand bis 31.12. bei 74 %. 27 X-Beiträge, 10 YouTube-Videos, 20 Prognosemärkte.

**Vor einer Reise.** `/last30days Universal Epic Universe` – Erweiterung bereits im Bau. Genehmigung für „Projekt 680“ eingereicht. Feuerwerksshow von der Infrastruktur bestätigt, aber unangekündigt. Wartezeiten: Mine-Cart Madness durchschnittlich 148 Minuten. Noch gibt es keine Jahreskarte und die Einheimischen sind frustriert. Stardust Racers wegen Renovierungsarbeiten bis zum 5. April außer Betrieb.

**Um schnell etwas zu lernen.** `/last30days Nano Banana Pro prompting` – JSON-strukturierte Eingabeaufforderungen ersetzen die Tag-Suppe. Das verschachtelte Format von @pictsbyai verhindert „Concept Bleeding“. Der Edit-First-Workflow übertrifft die Regeneration. Dann schreibt es Ihnen eine Produktionsaufforderung, die genau das verwendet, was die Community als funktionierend bezeichnet hat.

## Was ist neu

Seit der Ankündigung von v3.3 im Mai, ab v3.11.1 (Juli 2026): 175 zusammengeführte PRs – 122 davon von 52 Community-Mitwirkenden – in 15 Veröffentlichungen. Das ist gelandet.

### Erstklassig im OpenAI Codex

/last30days ist jetzt ein natives Codex-Plugin mit geführter Einrichtung – kein Port, ein Bürger erster Klasse. Renderer-fähige Zitate bedeuten, dass sich die Codex-Ausgabe wie eine kurze Zusammenfassung und nicht wie eine URL-Suppe liest (#694) und die gleiche Engine auf Claude Code-, Cursor-, Copilot-, Gemini CLI-, Claude Desktop-, OpenClaw- und über 50 Agent Skills-Hosts läuft. Codex-Plugin-Manifest von [@rfoust](https://github.com/rfoust) (#686), Codex-Authentifizierungskorrektur von [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmeme und Digg – kostenlos, keine API-Schlüssel

arXiv bringt die Papiere hinter dem Hype und Techmeme bringt die redaktionelle Tech-News-Ebene – kostenlos, keine Schlüssel und das Erstinstallationssetup installiert ihre CLIs, sodass sie automatisch aktiviert werden (#709). Die AI 1000-Story-Cluster von Digg kommen auf die gleiche Weise ohne X-Authentifizierung an – das Setup installiert die kostenlose Digg-CLI für Sie (#590). Trustpilot bietet Opt-in für die Recherche von Verbrauchermarken an.

### Free Reddit steigerte echte Punktzahlen und Top-Kommentare

Die öffentliche .json-API von Reddit ist gestorben; Der freie Weg kam stärker zurück. Schlüsselloses RSS + Shreddit-Scraping (#457), dedizierte Subreddit-Erkennung mit echten Upvote-Zählungen über Arctic-Shift (#696) und eine Relevanzuntergrenze, damit ein viraler Off-Topic-Beitrag Ihr Briefing nicht kapern kann (#488, danke [@rzachsmith](https://github.com/rzachsmith)). Kein API-Schlüssel. Echte Punkte. Top-Kommentare inklusive.

### Die besten Kommentare in jedem Briefing

Kommentare sind jetzt eine standardmäßige Ebene für alle Quellen: Instagram-Kommentare mit rangbasierter Diversität, sodass nicht alle fünf heißen Takes aus einem Beitrag stammen (#751), YouTube-Kommentare plus ein ScrapeCreators-Transkript-Backup für den Fall, dass yt-dlp ausfällt (#637) und durch Crowd-Voting in „Best Takes“ gewichtete Kommentare, damit die witzigsten Zeilen der Community die Wertung überleben (#592, #608).

### Ein Arztbefehl

Fordern Sie eine Gesundheitsprüfung an, und der Arzt führt jede Quelle durch und verschreibt dann genaue Korrekturen – welcher Schlüssel fehlt, welche CLI außerhalb von PATH liegt, welches Cookie abgelaufen ist (#753). Keine Vermutung mehr, warum X dünn zurückkam.

### X-Suche, neu erstellt

Die X-Pipeline wurde grundlegend überarbeitet: FROM- und ABOUT-Spuren, damit sowohl die eigenen Beiträge einer Person als auch die Konversation über sie einen Rang haben (#610), personenbezogene Unterabfrage-Begriffsklärung (#611), Erstanbieter-Autorenschaftserdung mit Interaktionssignal-Rangliste (#613) und eine einzige X-Quelle mit automatischem Backend-Failover (#622). Plus ein ehrlicher `--diagnose`, der tatsächlich die Authentifizierung prüft (#609).

### Weitere Quellen sind beigetreten

LinkedIn über ScrapeCreators, mit Artikeln mit hohem Signal ([@ravstr](https://github.com/ravstr), #702). StockTwits wird automatisch für Ticker- und Krypto-Themen aktiviert ([@wtiwana](https://github.com/wtiwana), #658). Perplexity wuchs mit direkten API-Modi und asynchroner Deep Research ([@sk-holmes](https://github.com/sk-holmes), #629).

### Von der Community gehärtet

Die Sicherheitswelle war fast ausschließlich Community-Arbeit: Korrekturen gespeicherter XSS im HTML-Renderer ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), gesperrte temporäre Cookie-Dateien, Supply-Chain-gehärtetes CI mit OpenSSF Scorecard und Build-Herkunftsbescheinigung ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), Semgrep- und OSV-Scanner-Scans plus ein PR-Abhängigkeitsüberprüfungs-Gate ([@23241a6749](https://github.com/23241a6749)), eine Testabdeckungsuntergrenze, die bei 60 % eingeführt und seitdem auf 84 % erhöht wurde ([@gourab5139014](https://github.com/gourab5139014)) und ein Hermes-Sicherheitsscan, der alle KRITISCHEN Befunde bereinigt (#768).

### Reicht weiter

Hebräische und nicht-lateinische Sprachen ([@dudyme](https://github.com/dudyme)). CJK-fähige Tokenisierung für chinesische Quellen ([@An-idd](https://github.com/An-idd)). Eine Windows-Kompatibilitätswelle. Cookie-Extraktion aus der gesamten Chromium-Familie – Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) – plus macOS-Schlüsselbund- und Linux-Pass(1)-Anmeldeinformationsquellen. `--as-of` historischer Rückblick ([@chiyi-creator](https://github.com/chiyi-creator)). Automatisch bereitgestelltes Python 3.12 über UV ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` zum Lesen der Stellenseiten eines Unternehmens. Watchlist-Deltas zwischen Läufen.

### Noch in der Box von v3

Die v3-Grundlagen sind alle noch vorhanden: das Gehirn vor der Forschung, das die richtigen Handles, Subreddits und Hashtags auflöst, bevor ein einzelner API-Aufruf ausgelöst wird (erstellt von [@j-sperling](https://github.com/j-sperling)); Best Takes punkten neben Relevanz auch durch Humor und Viralität; Cross-Source-Cluster-Zusammenführung; Single-Pass-Vergleiche („CLI vs. MCP“ in 3 Minuten, nicht 12); automatisch ermittelte `--competitors`-Vergleiche; GitHub-Personenmodus (`--github-user=steipete`); ELI5-Modus („eli5 on“ nach jedem Lauf); und gemeinsam nutzbare, eigenständige HTML-Briefs (`--emit=html`). Konfigurationsknöpfe befinden sich in [CONFIGURATION.md](CONFIGURATION.md).

## Installieren

| Oberfläche | Installieren | Aktualisierungen |
|---------|---------|---------|
| **Claude Code** (empfohlen) | `/plugin marketplace add mvanhorn/last30days-skill` | Automatisch über den Marktplatz oder `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` dann `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI oder einer von über 50 [Agent Skills](https://agentskills.io)-Hosts** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [`last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) herunterladen und über claude.ai hochladen > Anpassen > Fertigkeiten > + > Fertigkeit erstellen > Fertigkeit hochladen | Erneut herunterladen und erneut hochladen |
| **Claude Desktop** | [Laden Sie `.mcpb` für Ihre Plattform herunter](https://github.com/mvanhorn/last30days-skill/releases/latest) und ziehen Sie es in Einstellungen > Erweiterungen | Laden Sie das neue Paket erneut herunter und ziehen Sie es in |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (empfohlen)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Empfohlen, da der Claude Code-Marktplatz Aktualisierungen für Sie übernimmt – der Plugin-Cache ist versioniert und wird automatisch aktualisiert, wenn eine neue Version veröffentlicht wird. Führen Sie `claude plugin update last30days@last30days-skill` aus, um eine Prüfung zu erzwingen.

Wenn Sie lieber den Agent-Skills-Installationspfad für Claude Code verwenden möchten, wird dies ebenfalls unterstützt:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

Das native Plugin und die `npx skills`-Installation können nebeneinander existieren. Beachten Sie, dass Claude Code nicht über alle Installationsmethoden hinweg dedupliziert: Wenn Sie sowohl das Marktplatz-Plugin als auch die `npx skills`-Kopie aktiv haben, zeigt `/last30days` zwei Einträge an. Verwenden Sie eine Installationsmethode pro Computer.

### Grok (xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) installiert last30days als natives Plugin. Die direkte Installation verfolgt das Repository:

```bash
grok plugin install mvanhorn/last30days-skill
```

Oder fügen Sie dieses Repo als Marktplatzquelle hinzu und installieren Sie es dann nach Plugin-Namen:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Fügen Sie `--trust` hinzu, um die Installationsbestätigung zu überspringen. Update mit `grok plugin update last30days`. Grok liest aus Kompatibilitätsgründen auch die Claude-Code-Manifeste; Das native `.grok-plugin/`-Paar ist die erstklassige Spur (und worauf ein offizieller [xAI-Marktplatz](https://github.com/xai-org/plugin-marketplace)-Eintrag hinweist). `npx skills add` bleibt ein gültiger hostübergreifender Fallback.

### Codex, Cursor, Copilot, Gemini CLI und andere Agent Skills-Hosts

Installation über die offene [Agent Skills](https://agentskills.io) CLI – unterstützt mehr als 50 Kabelbäume, einschließlich `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose` und mehr (vollständige Liste im [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

Das Flag `-g` (global) wird in Ihrem Benutzerverzeichnis installiert, sodass der Skill in allen Projekten verfügbar ist. Ohne `-g` wird `npx skills` projektlokal in `./.skills/` installiert (mit dem Repo festgeschrieben). Für ein „Research-the-World“-Tool ist Globalität genau das Richtige für Sie.

Codex-Desktop- und andere Ordnermodus-Hosts können sowohl in normalen Ordnern als auch in Git-Repos funktionieren. Bitten Sie vor der ersten Recherche den Host-Agenten, das gebündelte `scripts/last30days.py --preflight` aus dem geladenen Skill-Verzeichnis auszuführen. Beim Auschecken einer Quelle lautet der entsprechende Befehl `python3 skills/last30days/scripts/last30days.py --preflight`. Es zeigt die Konfigurationsquelle, den Browser-Cookie-Plan, geplante Schreibvorgänge, optionale Befehle und die ignorierte Projektkonfiguration an, ohne Cookies zu lesen, Dateien zu schreiben oder Recherchen durchzuführen.

Standardmäßig wird dies für den von `npx skills` erkannten Kabelbaum installiert. So zielen Sie auf eine bestimmte (oder mehrere) Zielgruppe ab:

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

Oder aktualisieren Sie alles, was Sie global installiert haben, über `npx skills`:

```bash
npx skills update -g
```

Mit `npx skills list -g` und `npx skills remove last30days -g` auflisten und entfernen.

### claude.ai (web)

1. [`last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) von der neuesten Version herunterladen
2. Gehen Sie zu [claude.ai > Anpassen > Skills](https://claude.ai/customize/skills)
3. Klicken Sie im Bedienfeld „Fähigkeiten“ auf die Schaltfläche „`+`“ > klicken Sie auf „`Create skill`“ > „`Upload a skill`“ und durchsuchen Sie die Datei bzw. legen Sie sie dort ab

Aktivieren Sie zuerst „Codeausführung und Dateierstellung“ unter „Fähigkeiten“ – ohne diese Funktion funktionieren die Fertigkeiten nicht.

### Claude Desktop

Claude Desktop installiert `/last30days` als MCP-Server über ein `.mcpb`-Bundle (ein One-Click-Model-Context-Protocol-Paket).

1. Gehen Sie zur [neuesten Version](https://github.com/mvanhorn/last30days-skill/releases/latest) und laden Sie `.mcpb` für Ihre Plattform herunter:
- macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
- macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
- Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Öffnen Sie Claude Desktop, gehen Sie zu Einstellungen > Erweiterungen und ziehen Sie die Datei hinein.
3. Wenn Sie dazu aufgefordert werden, fügen Sie API-Schlüssel für die Quellen ein, die Sie aktivieren möchten. Jedes Feld ist optional – die Engine wechselt in den reinen Webmodus, wenn Sie sie alle überspringen. Schlüssel werden in Ihrem Betriebssystem-Schlüsselbund gespeichert.
4. Starten Sie Claude Desktop neu. Bitten Sie Claude, „Peter Steinberger“ oder ein anderes Thema zu recherchieren, und das Tool `research` wird aufgerufen.

**Hostanforderung:** Python 3.12+ auf PATH. Das Bundle liefert die Engine-Quelle, verwendet aber Ihren lokalen Python-Interpreter. Installation von [python.org](https://www.python.org/downloads/) unter Windows; macOS und die meisten Linux-Distributionen liefern eine kompatible Version aus.

**Schlüssel werden nicht mit dem Code-Skill synchronisiert.** Claude Desktop und Claude Code verwalten von Natur aus separate Anmeldeinformationsspeicher. Wenn Sie `~/.config/last30days/.env` bereits für den Code-Skill konfiguriert haben, geben Sie die gleichen Schlüssel hier einmal erneut ein.

Die Windows-Unterstützung wird zurückgestellt, bis die Manifest-Einstiegspunkte pro Plattform geklärt sind. in einer Folgeausgabe verfolgen.

### OpenClaw

```bash
clawhub install last30days-official
```

Für X/Twitter-Aktionsworkflows außerhalb der `/last30days`-Recherche, z. B. Posten
Tweets oder Antworten, Follower-Export, Medienverwaltung, Monitore und Giveaways
zeichnet, verwenden Sie [TweetClaw](https://github.com/Xquik-dev/tweetclaw) als Begleiter
OpenClaw-Plugin. TweetClaw wird von Xquik-dev verwaltet und ist nur als aufgeführt
optionaler Begleitpfad, keine last30days-Abhängigkeit oder -Befürwortung.

### Handbuch (Entwickler)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

Der Symlink hält die Installation während der Bearbeitung mit Ihrem Arbeitsbaum synchron – ein erneutes Kopieren ist nicht erforderlich. Erstellen Sie für `claude.ai` die Datei `.skill` aus der Quelle: `bash skills/last30days/scripts/build-skill.sh` erzeugt `dist/last30days.skill`.

Reddit (mit Kommentaren), Hacker News, Polymarket und GitHub funktionieren sofort. Nullkonfiguration. Führen Sie `/last30days` einmal aus und der Setup-Assistent schaltet in 30 Sekunden weitere Quellen frei, einschließlich der kostenlosen arXiv- und Techmeme-CLIs.

## Bringen Sie Ihre eigenen Schlüssel mit

Diese Plattformen unterhalten keine Beziehungen untereinander. X weiß nicht, was Reddit denkt. YouTube sieht TikTok nicht. Aber Sie können Ihre eigenen API-Schlüssel und Browser-Token mitbringen und haben plötzlich Zugriff auf alle gleichzeitig.

| Quellen | Was Sie brauchen | Kosten |
|---------|---------------|------|
| Reddit (mit Kommentaren) + HN + Polymarket + GitHub + StockTwits | Nichts | Kostenlos |
| arXiv + Techmeme | Kostenlose CLIs, automatisch installiert bei der Erstinstallation | Kostenlos |
| X / Twitter | Melden Sie sich in einem beliebigen Browser bei x.com an oder stellen Sie `XQUIK_API_KEY` / `XAI_API_KEY` | ein Browser-Cookies sind kostenlos; Schlüssel sind anbieterspezifisch |
| YouTube | `brew install yt-dlp` | Kostenlos |
| Blauer Himmel | App-Passwort von bsky.app | Kostenlos |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube-Kommentare | ScrapeCreators-Schlüssel | 10.000 kostenlose Anrufe, dann PAYG |
| Xiaohongshu (ROT) | Führen Sie ein angemeldetes x-mcp-Browser-Plugin oder einen `xiaohongshu-mcp`-Dienst aus und melden Sie sich mit `--search xhs` pro Lauf oder `INCLUDE_SOURCES=xiaohongshu` in `.env` an. last30days prüft automatisch `http://localhost:18060` und dann `http://host.docker.internal:18060`, oder verwenden Sie `XIAOHONGSHU_API_BASE` für eine benutzerdefinierte URL | Kein last30days-API-Schlüssel; hängt von Ihrem lokalen Browser-Sitzungsdienst ab |
| DripStack (Premium-Finanz-Newsletter) | Opt-in: `--search dripstack` pro Lauf oder `INCLUDE_SOURCES=dripstack` in `.env` | Kein Schlüssel; kostenlose öffentliche Such-API |
| Perplexity Sonar / Such-API / Deep Research | Perplexity-Schlüssel oder OpenRouter-Schlüssel als Sonar-Fallback | Zahlen Sie nach Belieben |
| Websuche | Brave-Suchschlüssel | 2.000 kostenlose Abfragen/Monat |

### macOS-Schlüsselbund (optional)

Unter macOS können Sie Schlüssel im System-Schlüsselbund statt in einer `.env`-Datei speichern. Der Skill erkennt sie automatisch als Quelle mit der niedrigsten Priorität – `.env`-Dateien und Prozessumgebung gewinnen bei Kollisionen immer noch.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Elemente werden unter dem Dienstnamen `last30days-<KEY>` für den aktuellen Benutzer gespeichert. Auf Nicht-Darwin-Plattformen ist der Loader ein No-Op, sodass es für Linux-/Windows-Benutzer keine Verhaltensänderung gibt.

Besitzen Sie bereits Schlüssel unter verschiedenen Namen des Schlüsselbunddienstes? Legen Sie die in [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) beschriebene nicht geheime `LAST30DAYS_KEYCHAIN_ALIASES`-Zuordnung fest, anstatt Geheimnisse zu kopieren.

Unter [CONFIGURATION.md](CONFIGURATION.md) finden Sie die vollständige Schlüsselmatrix pro Quelle, die Argumentationsanbieterpriorität und die Back-End-Priorität für die Websuche.

## Konfiguration

Zwei Dinge, die Sie wahrscheinlich am ersten Tag wissen wollen:

**Wo Forschungsdateien gespeichert werden.** `LAST30DAYS_MEMORY_DIR` ist standardmäßig `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Überschreiben Sie dies, indem Sie diese Umgebungsvariable auf einen beliebigen Pfad in Ihrer Shell oder auf `--save-dir <path>` pro Lauf festlegen. Verwenden Sie `--output <file>`, wenn Sie das gerenderte Ergebnis in einem exakten Pfad benötigen, und verwenden Sie dabei das von `--emit` ausgewählte Format. Verwenden Sie `--save-suffix=<name>`, um mehrere Variationen desselben Themas getrennt zu halten (z. B. pro Kunde). Jeder `--save-dir`-Lauf erzeugt `<slug>-raw[-suffix].md`. Führen Sie `python3 skills/last30days/scripts/last30days.py --preflight` aus, um geplante Schreibvorgänge vor einem Forschungslauf zu überprüfen.

**Strukturierte Ausgabe für Agenten und Workflows.** Fragen Sie `/last30days` nach maschinenlesbarem JSON, um das stabile, versionierte Agentenprofil zu erhalten. Für die direkte Verwendung der Engine in Skripten oder bei der Entwicklung führen Sie `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json` aus; Fügen Sie `--json-profile=raw` nur hinzu, wenn Sie den unversionierten internen `Report`-Dump benötigen. Siehe die [JSON-Exportfeldreferenz und Versionierungsrichtlinie](docs/reference/json-export.md).

**Themenlose Entdeckung.** Bitten Sie `/last30days what's trending in AI agents?` darum, eine Rangfolge-Entdeckungsbeschreibung zu erhalten, anstatt ein Thema zu recherchieren, das Sie bereits kennen. Auf einem Agent-Host wird dadurch das vom Host beurteilte Protokoll mit drei Befehlen ausgeführt (das Modell benennt Themen, filtert Junk, bewertet die Würdigkeit und schreibt die Inhaltsaspekte). Für die direkte Verwendung der Engine in Skripten oder Cron führen Sie `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` aus (einmalig: deterministische Themennamen, keine Winkel); Fügen Sie `--emit=json` für den versionierten Discovery-Vertrag hinzu. Die Entdeckung schließt sich gegenseitig mit einem Positionsthema und `--drill` aus.

**Trendüberwachung über Läufe hinweg.** Der Standardmodus erzeugt pro Lauf einen neuen Markdown-Snapshot. Um Ergebnisse im Laufe der Zeit zu sammeln, fügen Sie `--store` hinzu, um sie in einer SQLite-Datenbank zu speichern, und verwenden Sie dann [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) für geplante Ausführungen (mit optionaler Slack-/Webhook-Bereitstellung für neue Ergebnisse) und [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) für tägliche/wöchentliche Zusammenfassungen. Das vollständige Trittfrequenzmuster finden Sie in [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Eine abonnierbare Forschungsbibliothek.** Bitten Sie `/last30days`, Ihren Bibliotheks-Feed zu erstellen, oder verwenden Sie `python3 skills/last30days/scripts/last30days.py library feed` direkt für die Skripterstellung und Entwicklung. Es wandelt gespeicherte Kurzbriefe in `index.html`, einen lokalen Atom `feed.xml` und lesbare Kurzbriefseiten um. Fügen Sie `--publish` nur hinzu, wenn Sie den HTML-Index und die kurzen Seiten hosten möchten. Die Veröffentlichung erfolgt standardmäßig mit ausdrücklicher Einwilligung und ist öffentlich. Um den Atom-Feed abonnierbar zu machen, hosten Sie das generierte Ausgabeverzeichnis auf einem statischen Host wie GitHub Pages.

**Durchsuchen Sie alles, was Sie recherchiert haben.** Fragen Sie `/last30days search my library for MCP servers` oder `/last30days have I researched MCP servers before?`. Für den direkten Motorgebrauch `python3 skills/last30days/scripts/last30days.py library search "MCP servers"` ausführen. Die Suche ist offline und deterministisch: Sie indiziert inkrementell dieselben gespeicherten Briefings, die vom Bibliotheks-Feed verwendet werden, führt übereinstimmende Filialsichtungen pro Durchlauf zusammen und gruppiert die Ergebnisse nach Thema und Datum. Bei neuen Durchläufen wird auch ein kompakter Abschnitt „Aus Ihrer Bibliothek“ angezeigt, wenn sich frühere Recherchen mit dem aktuellen Thema überschneiden. Legen Sie `LAST30DAYS_LIBRARY_CONTEXT=off` fest, um diesen passiven Kontext zu deaktivieren.

Client-spezifische Wrapper-Skripte, benutzerdefinierte Kategorie-Peer-Subreddits und der experimentelle Beta-Kanal für laufende Anpassungen sind ebenfalls in [CONFIGURATION.md](CONFIGURATION.md) dokumentiert.

## Showcase: Community-Recherche-Feeds

Haben Sie ein wiederkehrendes KI-Update, eine Marktbeobachtung oder eine wunderbar enge Obsession mit last30days veröffentlicht? Teilen Sie die URL der öffentlichen Bibliothek – oder die Atom-URL nach dem Hosten von `feed.xml` auf einem statischen Host – im [Community-Showcase-Thread](https://github.com/mvanhorn/last30days-skill/issues/532). Community-Feeds werden hier verlinkt, sobald ihre Besitzer sie einreichen. Der Thread ist in der Zwischenzeit der Sammelpunkt.

## Wie es funktioniert

1. **Sie geben ein Thema ein.** Person, Unternehmen, Produkt, Technologie, „X vs. Y.“ Irgendetwas.
2. **Der Agent entscheidet, wer zählt.** Findet X-Handles (einschließlich Gründer), GitHub-Repos, Subreddits, TikTok-Hashtags und YouTube-Kanäle. Für „Kanye West“ gibt es R/Hiphopheads, @kanyewest und „Bully Review“ auf YouTube. Für „OpenClaw“ löst es openclaw/openclaw auf GitHub auf und ruft Live-Star-Zählungen ab.
3. **Alle Quellen werden parallel durchsucht.** Erweiterung mit mehreren Abfragen. Ergebnisse bewertet nach Engagement, Relevanz und Aktualität.
4. **Die Tiefe, die niemand sonst hat.** Vollständige YouTube-Transkripte von Reaktionsvideos. Top-Reddit-Kommentare mit Upvote-Zählungen. TikTok-Untertitel. Polymarket-Quoten. Nicht nur Titel und Links.
5. **Gleiche Geschichte, zusammengeführt.** Wireless Festival auf Reddit angekündigt, auf X besprochen, Ticketpreise auf TikTok = ein Cluster, nicht drei separate Artikel.
6. **In einem Brief zusammengefasst.** Basierend auf spezifischen Daten. Zitiert nach Quelle. Geordnet nach dem, womit sich die Leute tatsächlich beschäftigen. Nicht „Hier ist, was ich gefunden habe.“ Es heißt: „Hier kommt es darauf an.“
7. **Dann wird es Ihr Experte.** Nach einem Durchlauf weiß Ihre Claude-Sitzung alles, was die Community weiß. Stellen Sie weitere Fragen. Lassen Sie Eingabeaufforderungen schreiben, E-Mails entwerfen, Reisen planen und Systeme erstellen – alles basiert auf dem, was gerade real ist.

## Was die Leute sagen

> „Ich habe einen Claude-Code-Skill gefunden, der jedes Thema der letzten 30 Tage auf Reddit, -@itsjasonai

> „Diese eine Fähigkeit hat meinen gesamten Recherche-Workflow ersetzt. Sie geben ihm ein Thema, es durchsucht Reddit, X und das Web nach dem, worüber die Leute tatsächlich sprechen. Keine alten Blog-Beiträge. Echte Gespräche der letzten 30 Tage.“ -@itswilsoncharles

> „5 der 10 angesagten Repos auf GitHub sind heute Claude-Tools. #1: mvanhorn/last30days-skill“ -@yieldhunter95

## Open Source

MIT-Lizenz. Keine Nachverfolgung. Keine Analyse. Ihre Forschung bleibt auf Ihrem Computer. Über 2.700 Tests.

Gebaut mit Python 3.12+, yt-dlp, Node.js (vom Anbieter bereitgestellter Bird-Client für die X-Suche) und der ScrapeCreators-API. v3-Engine-Architektur von [@j-sperling](https://github.com/j-sperling).

Siehe [CONTRIBUTING.md](CONTRIBUTING.md) zum Öffnen einer PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) für die vollständige Liste der Community-Mitwirkenden und [CHANGELOG.md](CHANGELOG.md) für den Versionsverlauf.

## Sterngeschichte

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
