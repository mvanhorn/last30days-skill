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

**Eine von KI-Agenten geführte Suchmaschine, bewertet durch Upvotes, Likes und echtes Geld – nicht durch Redakteure.**

Dieses README verfolgt die aktuelle v3-Pipeline. Die Runtime-Skill-Spezifikation befindet sich in [skills/last30days/SKILL.md](skills/last30days/SKILL.md), was die Quelle der Wahrheit für das neueste Befehls- und Setup-Verhalten ist.

**Claude Code (empfohlen — automatische Aktualisierungen über den Marktplatz):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI, oder einer von 50+ [Agent Skills](https://agentskills.io) Hosts:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` installiert sich global für deinen Nutzer und ist in allen Projekten verfügbar. Setze es in den Umfang pro Projekt.)

Weitere Installationsoptionen (claude.ai Web, OpenClaw, Handbuch) im folgenden Abschnitt [Install](#installation) .

Null Konfiguration. Reddit, HN, Polymarketund GitHub funktionieren sofort. Führe es einmal aus und der Setup-Wizard schaltet X, YouTube, TikTok, arXiv, Techmemeund mehr in 30 Sekunden frei.

---

Reddit Upvotes. X Likes. YouTube Transkripte. TikTok Engagement. Polymarket Chancen, die durch echtes Geld und Insiderinformationen gestützt sind. Das sind Millionen von Menschen, die jeden Tag mit Aufmerksamkeit und Geldbörse abstimmen. /last30days durchsucht alles parallel, bewertet es nach dem, worauf echte Menschen tatsächlich interagieren, und ein KI-Agenten-Richter fasst es zu einem Briefing zusammen.

Google aggregiert Redakteure. /last30days sucht nach Leuten.

Du kannst diese Suche nirgendwo anders bekommen, weil keine einzelne KI Zugriff auf alles hat. Google Suche berührt Reddit Kommentare oder X Beiträge nicht. ChatGPT hat einen Deal mit Reddit , kann aber nicht X oder TikToksuchen. Gemini hat YouTube , aber nicht Reddit. Claude hat keine davon nativ. Jede Plattform ist ein abgeschotteter Garten mit eigenem API, eigenen Tokens, eigener Authentifizierung. Aber du kannst deine eigenen Schlüssel und Browsersitzungen mitbringen, und plötzlich kann ein KI-Agent alle gleichzeitig durchsuchen, sie gegeneinander bewerten und dir sagen, was wirklich zählt.

Das ist die Entsperrung. Keine bessere Suchmaschine. Ein Dutzend getrennte Plattformen, von einem Agenten überbrückt.

```
/last30days Peter Steinberger
```

Du hast morgen ein Meeting. Du Google sie. Du bekommst ihre LinkedIn von 2023. /last30days zeigt dir, was sie diesen Monat tatsächlich machen: OpenAI beigetreten, um an Codexzu arbeiten, gegen Anthropic's Verbot von Drittanbieteragenten zu kämpfen, 23 PRs mit 85 % Merge Rate ausgeliefert, "LobsterOS" für geräteübergreifende Agentensteuerung gebaut und r/ClaudeCode erreichte 569 Upvotes, in denen diskutiert wurde, ob er ein Held oder "unerträglich" ist. Über X Beiträge verteilt, Reddit Threads, YouTube Transkripte und GitHub Commits. Nichts davon war auf Google.

## Warum das existiert

Ich habe es gebaut, um mit KI Schritt zu halten. Alles ändert sich jeden Tag und die Reddit und X Nerds sind immer zuerst dran. Ich brauchte bessere Prompts, und die Trainingsdaten lagen immer Monate hinter dem, was die Community bereits herausgefunden hatte.

Aber daraus wurde etwas Größeres. Jetzt führe ich es vor einem Verkaufsgespräch durch, um die Wahrheit der letzten 30 Tage über ein Unternehmen zu erfahren. Vor einem Meeting, um die aktuellen Tweets und Podcast-Transkripte von jemandem zu lesen. Vor einer Disney World Reise, um zu wissen, welche Fahrgeschäfte geschlossen sind und was die Community über Genie+sagt. Bevor ich etwas entwickle, um zu wissen, auf welche Probleme die Leute tatsächlich stoßen.

Wenn du dich mit einem CEO triffst, hast du alle Tweets und YouTube Transkripte der letzten 30 Tage gelesen? Ja, habe ich.

## Quellen, von den Leuten bewertet

| Quelle | Was die Leute dir sagen. |
|--------|--------------------------|
| **Reddit** | Die ungefilterte Sichtweise. Top-Kommentare mit echten Upvote-Zählen, kostenlos, kein API Schlüssel. Die echten Meinungen, die Google vergräbt. |
| **X / Twitter** | Die heiße Meinung, der Experten-Thread, die Bruchreaktion. Zuerst weiß er, streitet zuerst. |
| **YouTube** | Der 45-minütige Tieftauchgang. Vollständige Transkripte, gesucht nach den 5 zitierfähigen Sätzen, die zählen. |
| **TikTok** | Der Creator erreicht 3,6 Millionen Leute mit einer Einnahme, die du auf Googlenie finden wirst. |
| **Instagram Reels** | Die Influencer-Perspektive mit gesprochenen Transkripten. Das visuelle Kultursignal. |
| **Hacker News** | Der Konsens der Entwickler. 825 Punkte, 899 Kommentare. Wo Techniker tatsächlich streiten. |
| **Polymarket** | Nicht Meinungen. Chancen. Gedeckt durch echtes Geld. 96% Vertrauen bei Albumverkäufen. 4% bei einer Übernahme. |
| **GitHub** | Für Personen: PR Geschwindigkeit, Top-Repos nach Sternen, Veröffentlichungshinweise. Für Themen: Themen und Diskussionen. |
| **Digg** | Kuratierte Story-Cluster aus der AI 1000-Bestenliste von Digg(~1000 High-Signal-KI-Konten auf X), mit zuschreibbaren Inline-Quotes (keine X Authentifizierung erforderlich). Automatisch aktiviert, wenn `digg-pp-cli` auf PATHist. |
| **arXiv** | Die Papiere hinter dem Hype. Neue Forschung im Fenster, kostenlos, kein API Schlüssel. Automatisch aktiviert, wenn `arxiv-pp-cli` PATH aktiviert ist (Erstlauf-Setup installiert es). |
| **Techmeme** | Die Tech-News-Redaktionsebene, datumsabhängig auf deine 30 Tage. Kostenlos, kein API Schlüssel. Automatisch aktiviert, wenn `techmeme-pp-cli` PATH aktiviert ist (Erstlauf-Setup installiert es). |
| **LinkedIn** | Das professionelle Signal. Beiträge und Artikel, wobei Artikel als High Signal gewichtet sind. |
| **StockTwits** | Händler-Gefühl. Aktiviert sich automatisch, wenn Ihr Thema ein Ticker oder eine Krypto-Karte ist. |
| **Threads** | Die Post-Twitter-Textebene. Gespräche von Schöpfern und Marken. |
| **Pinterest** | Visuelle Entdeckung. Pins, Speicherstände und Kommentare zu Produkten und Ideen. |
| **Xiaohongshu (RED)** | Chinesischer Lebensstil, Produkt und Creator-Signale. Explizit angefordert mit `--search xhs` , wenn ein eingeloggtes x-mcp-Browser-Plugin oder `xiaohongshu-mcp` Dienst lokal läuft. |
| **Bluesky** | Die dezentrale soziale Schicht. AT Protocol-Beiträge aus der Post-Twitter-Migration. |
| **Perplexity** | Geerdete Sonar-Synthese, rohe Suche API Reihen und Tiefe Forschung. |
| **Web** | Die redaktionelle Berichterstattung, die Blogvergleiche. Ein Signal von vielen, nicht das einzige. |

Community-Mitwirkende fügen immer mehr hinzu. Truth Social und andere Nischenquellen sind in der Engine, weitere sind unterwegs.

Ein Reddit Thread mit 1.500 Upvotes ist ein stärkeres Signal als ein Blogbeitrag, den niemand gelesen hat. Ein TikTok mit 3,6 Millionen Aufrufen sagt mehr darüber aus, was kulturell relevant ist, als eine Pressemitteilung. Polymarket Quoten mit 66.000 Dollar Volumen sind schwerer zu bestreiten als die Vermutung eines Experten.

Die Synthese rangiert nach dem, womit echte Menschen tatsächlich beschäftigt waren. Soziale Relevanz, nicht SEO Relevanz.

## Wofür die Leute es tatsächlich nutzen

**Vor einem Meeting.** `/last30days Peter Steinberger` – bin dem Codex-Team von OpenAIbeigetreten und habe gegen Anthropics Verbot von Drittanbieteragenten gekämpft, 23 PRs mit einer Fusionsrate von 85 % auf GitHubfusioniert und LobsterOS für geräteübergreifende Agentensteuerung gebaut. r/ClaudeCode: "Seit OpenClaw veröffentlicht wurde, war allgemein bekannt, dass man, wenn man es über etwas anderes als das APIlaufen lässt, irgendwann gebannt wird" (227 Upvotes). Das liegt nicht an LinkedIn.

**Um Einstellungssignale zu lesen.** `/last30days Listen Labs --hiring-signals` – aktuelle Job- und Karriereseiten werden zu zitierten Belegen für Fokusverschiebungen: Einstellungen in Unternehmenssicherheit, Kundenerfolg, Infrastruktur oder Produktexpansion. Der Bericht sagt, was die Einstellung zu signalisieren scheint, nicht was die Roadmap liefern wird.

**Um das Thema zu finden, bevor es seinen Höhepunkt erreicht.** Frag `/last30days what's exploding in AI agents?` und die Fähigkeit wechselt in den Discovery-Modus: Die Engine durchsucht Reddit Kategorienlisten, Hacker News Front-/Best-Stories, den AI 1000-Feed von Diggund X bei Authentifizierung; dein Agent bewertet die Nominierungen (Namen, Junk-Filtering, Inhaltswertigkeit) und schreibt Podcast-/ X-Artikel-Winkel; dann bekommst du 5–10 Velocity-bewertete Themen. Jedes Ergebnis enthält Querseitennummern, ein Momentum-Label und eine startklare `/last30days "<topic>"` Folge.

**Wenn etwas fällt.** `/last30days Kanye West` - UK blockierte sein Visum, das Wireless Festival wurde gestrichen, Sponsoren flohen. Aber BULLY debütierte auf #2 bei Billboard. Fantano kam von seinem "Yay Sabbatical" zurück, um es zu rezensieren (653.000 Aufrufe). SoFi Homecoming brachte Lauryn Hill und Travis Scott für 44 Songs heraus. Polymarket: "Wird Kanye wieder twittern?" 86% Ja. 23 Reddit Threads, 17 YouTube Videos, 86.000 Upvotes.

**Um Werkzeuge zu vergleichen.** `/last30days OpenClaw vs Hermes vs Paperclip` – "Das sind keine Konkurrenten, das sind Schichten." OpenClaw ist der Testamentsvollstrecker (351.000 GitHub Sterne, live), Hermes ist das sich selbst verbessernde Gehirn (31.000 Sterne), Paperclip ist das Organigramm (49.000 Sterne). Sternzählungen werden live aus dem GitHub APIgezogen, nicht veraltete Blogbeiträge. Nebeneinander-Tabelle mit Architektur, Speicher, Sicherheit, Best-for. Laut @IMJustinBrooke: "OpenClaw = Glurak, Hermes = Glurak."

**Um die Welt zu verstehen.** `/last30days Iran vs USA` – Tag 38 des Krieges. Trumps Deadline am Dienstag für Iran, die Straße von Hormus wieder zu öffnen. Zwei US-Kampfflugzeuge abgeschossen. Öl zu 126 Dollar pro Barrel. Die IEA bezeichnete es als "die größte Versorgungsstörung in der Geschichte des globalen Ölmarktes." Polymarket: Waffenstillstand bis zum 31. Dezember bei 74%. 27 X Beiträge, 10 YouTube Videos, 20 Prognosemärkte.

**Vor einer Reise.** `/last30days Universal Epic Universe` - Erweiterung bereits im Bau. "Projekt 680"-Genehmigung eingereicht. Feuerwerksshow von der Infrastruktur bestätigt, aber unangekündigt. Wartezeiten: Mine-Cart Madness dauert durchschnittlich 148 Minuten. Noch keine Jahreskarte, und die Einheimischen sind frustriert. Stardust Racers sind bis zum 5. April zur Renovierung eingestellt.

**Um schnell etwas zu lernen.** `/last30days Nano Banana Pro prompting` - JSON-strukturierte Prompts ersetzen Tag-Soup. @pictsbyaiverschachteltes Format verhindert "Concept Bleeding". Der Workflow, der zuerst auf Bearbeitung basiert, ist besser als Regeneration. Dann schreibt er dir einen Produktionsprompt, der genau das verwendet, was die Community als funktionierend bezeichnet.

## Was gibt's Neues

Seit der Ankündigung von v3.3 im Mai, Stand v3.11.1 (Juli 2026): 175 wurden PRs fusioniert – davon 122 von 52 Community-Beitragenden – über 15 Releases verteilt. Das ist es, was gelandet ist.

### Erste Klasse auf OpenAI Codex

/last30days ist jetzt ein natives Codex -Plugin mit geführter Einrichtung – kein Port, sondern ein erstklassiger Bürger. Renderer-bewusste Zitate bedeuten, dass Codex Ausgabe wie ein Brief statt wie eine URL-Suppe (#694) wirkt, und dieselbe Engine läuft auf Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClawund 50+ Agent Skills Hosts. Codex Plugin-Manifest von [@rfoust](https://github.com/rfoust) (#686), Codex Authentifizierungskorrektur von [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmemeund Digg – kostenlos, keine API Schlüssel

arXiv bringt die Zeitungen hinter dem Hype und Techmeme bringt die redaktionelle Tech-News-Ebene – kostenlos, null Schlüssel und First-Run-Setup installiert ihre CLI, sodass sie automatisch aktiviert werden (#709). Digg's AI 1000 Story-Cluster kommen ohne X Authentifizierung auf die gleiche Weise an – die Einrichtung installiert die kostenlose Digg CLI für dich (#590). Trustpilot Opt-in für Verbrauchermarkenforschung.

### Kostenlose Reddit echte Punktzahlen und Top-Kommentare hervorgebracht

Redditöffentliche .json API starb; der kostenlose Weg kam stärker zurück. Schlüsselloses RSS + Shreddit-Scraping (#457), dedizierte Subreddit-Entdeckung mit echten Upvote-Zahlen über arctic-shift (#696) und ein Relevanzboden, damit ein viraler Off-Topic-Beitrag dein Briefing nicht kapern kann (#488, danke [@rzachsmith](https://github.com/rzachsmith)). Kein API Schlüssel. Echte Wertungen. Top-Kommentare eingeschlossen.

### Die besten Kommentare in jedem Briefing

Kommentare sind jetzt eine Standard-Ebene über Quellen hinweg: Instagram-Kommentare mit rangbasierter Diversität, damit fünf heiße Meinungen nicht alle aus einem Beitrag stammen (#751), YouTube Kommentare plus ein ScrapeCreators Transkript-Backup für den Fall, dass yt-DLP ausfällt (#637), und von der Menge gestimmte Kommentare werden in Best Takes gewichtet, damit die lustigsten Zeilen der Community die Bewertung überleben (#592, #608).

### Ein Arztbefehl

Bitten Sie um eine Gesundheitsuntersuchung, der Arzt überprüft alle Quellen und verschreibt dann genaue Lösungen – welcher Schlüssel fehlt, welcher CLI fehlt PATH, welcher Keks abgelaufen ist (#753). Kein Raten mehr, warum X dünn war.

### X Suche, wieder aufgebaut

Die X -Pipeline wurde grundständig überarbeitet: FROM- und ABOUT-Lanes, sodass die eigenen Beiträge und das Gespräch darüber beide rangieren (#610), person-bewusste Subquery-Disambiguierung (#611), First-Party-Autoren-Grounding mit Interaktionssignal-Ranking (#613) und eine einzelne X -Quelle mit automatischem Backend-Failover (#622). Außerdem eine ehrliche `--diagnose` , die tatsächlich die Authentifizierung prüft (#609).

### Weitere Quellen wurden aufgenommen

LinkedIn über ScrapeCreators, mit Artikeln mit hohem Signal ([@ravstr](https://github.com/ravstr), #702). StockTwits aktiviert sich automatisch für Ticker- und Krypto-Themen ([@wtiwana](https://github.com/wtiwana), #658). Perplexity wuchs direkt API Modi und asynchron Deep Research ([@sk-holmes](https://github.com/sk-holmes), #629).

### Von der Gemeinschaft gehärtet

Die Sicherheitswelle bestand fast ausschließlich aus Community-Arbeit: gespeicherte XSS-Korrekturen im HTML Renderer ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), gesperrte Cookie-Temp-Dateien, Supply-Chain-gehärtetes CI mit OpenSSF Scorecard und Build-Herkunftsattestation ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), Semgrep- und OSV-Scanner-Scans sowie ein PR Abhängigkeits-Review-Gate ([@23241a6749](https://github.com/23241a6749)), ein Testdeckungsboden mit 60 % eingeführt und inzwischen auf 84 % erhöht ([@gourab5139014](https://github.com/gourab5139014)), und ein Hermes-Sicherheitsscan, der von allen KRITISCHEN Befunden freigearbeitet wurde (#768).

### Weitere Reichweiten

Hebräische und nicht-lateinische Sprachen ([@dudyme](https://github.com/dudyme)). CJK-bewusste Tokenisierung für chinesische Quellen ([@An-idd](https://github.com/An-idd)). Eine Windows Kompatibilitätswelle. Cookie-Extraktion über die gesamte Chromium-Familie – Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) – plus macOS Keychain und Linux Pass(1)-Berechtigungsquellen. `--as-of` historischer Rückblick ([@chiyi-creator](https://github.com/chiyi-creator)). Automatisch bereitgestellt Python 3.12 über UV ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` zum Lesen der Jobseiten eines Unternehmens. Watchlist-Deltas zwischen den Durchläufen.

### Immer noch in der Box von v3

Die v3-Grundlagen sind alle noch vorhanden: das Pre-Research-Gehirn, das die richtigen Handles, Subreddits und Hashtags löst, bevor ein einzelner API Aufruf auslöst (von [@j-sperling](https://github.com/j-sperling)gebaut); Best Takes Bewertung von Humor und Viralität neben Relevanz; Cross-Source-Cluster-Zusammenführung; Einzelpass-Vergleiche ("CLI 1 vs. MCP" in 3 Minuten, nicht 12); automatisch entdeckte `--competitors` Vergleiche; GitHub Person-Modus (`--github-user=steipete`); ELI5 Modus ("eli5 an" nach jedem Durchlauf); und teilbare, eigenständige HTML Briefs (`--emit=html`). Konfigurationsknöpfe befinden sich in [CONFIGURATION.md](CONFIGURATION.md).

## Installation

| Oberfläche | Installation | Aktualisierungen |
|---------|---------|---------|
| **Claude Code**(empfohlen) | `/plugin marketplace add mvanhorn/last30days-skill` | Auto über Marktplatz oder `claude plugin update last30days@last30days-skill` |
| **Grok**(xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` dann `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLIoder einer von 50+ [Agent Skills](https://agentskills.io) Hosts** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai**(Web) | [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) und hochladen über claude.ai > > Fähigkeiten anpassen > + > Fertigkeit erstellen > eine Fähigkeit hochladen | Neues Herunterladen und erneutes Hochladen |
| **Claude Desktop** | [Download the `.mcpb` for your platform](https://github.com/mvanhorn/last30days-skill/releases/latest) und ziehen in Einstellungen > Erweiterungen | Lade das neue Bundle neu herunter und ziehe es hinein |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (empfohlen)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Empfohlen, weil der Claude Code Marktplatz Updates für dich verarbeitet – der Plugin-Cache ist versioniert und aktualisiert sich automatisch, wenn eine neue Version veröffentlicht wird. Führe `claude plugin update last30days@last30days-skill` aus, um eine Überprüfung zu erzwingen.

Wenn du lieber den Agent-Skills-Installationspfad auf Claude Codenutzen möchtest, wird das ebenfalls unterstützt:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

Das native Plugin und die `npx skills` Installation können koexistieren. Beachte, dass Claude Code nicht über Installationsmethoden hinweg dedupliziert: Wenn du sowohl das Marketplace-Plugin als auch die `npx skills` Kopie aktiv hast, werden `/last30days` zwei Einträge angezeigt. Nutze pro Maschine eine Installationsmethode.

### Grok (xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) Installationen dauern 30 Tage als natives Plugin. Die direkte Installation verfolgt das Repository:

```bash
grok plugin install mvanhorn/last30days-skill
```

Oder füge dieses Repository als Marktplatzquelle hinzu und installiere dann nach Plugin-Namen:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Füge `--trust` hinzu, um die Installationsbestätigung zu überspringen. Aktualisiere mit `grok plugin update last30days`. Grok liest auch die Claude Code Manifeste zur Kompatibilität; das native `.grok-plugin/` -Paar ist die First-Class-Lane (und was ein offizieller [xAI marketplace](https://github.com/xai-org/plugin-marketplace) auflistet). `npx skills add` bleibt ein gültiger Cross-Host-Fallback.

### Codex, Cursor, Copilot, Gemini CLIund andere Agent Skills Hosts

Installieren Sie über die offene [Agent Skills](https://agentskills.io) CLI – unterstützt 50+ Kabelbäume, darunter `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose`und mehr (vollständige Liste auf dem [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

Das `-g` (globale) Flag wird in deinem Benutzerverzeichnis installiert, sodass die Skill in allen Projekten verfügbar ist. Ohne `-g`installiert `npx skills` projektlokal in `./.skills/` (mit dem Repository committiert). Für ein Research-the-World-Tool ist global das Richtige dafür.

Codex Desktop- und andere Ordnermodus-Hosts können sowohl in normalen Ordnern als auch in Git-Repos funktionieren. Vor der ersten Recherche bitten Sie den Host-Agenten, das mitgelieferte `scripts/last30days.py --preflight` aus dem geladenen Skill-Verzeichnis auszuführen; bei einem Quellcode-Checkout lautet der entsprechende Befehl `python3 skills/last30days/scripts/last30days.py --preflight`. Er zeigt die Konfigurationsquelle, den Browser-Cookie-Plan, geplante Schreibvorgänge, optionale Befehle und ignorierte Projektkonfigurationen an, ohne Cookies zu lesen, Dateien zu schreiben oder Recherchen durchzuführen.

Standardmäßig wird dies für das Kabelbaum installiert, das `npx skills` erkennt. Um eine bestimmte (oder mehrere) anzuvisieren:

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Update später mit:

```bash
npx skills update last30days -g
```

Oder aktualisiere alles, was du installiert hast, global über `npx skills`:

```bash
npx skills update -g
```

Liste und Entferne mit `npx skills list -g` und `npx skills remove last30days -g`.

### claude.ai (Web)

1. [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) aus der neuesten Veröffentlichung
2. Geh zu [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Klicke auf die `+` -Taste im Skills-Panel > klicke auf `Create skill` > `Upload a skill` und durchstöber/leg die Datei ein

Aktiviere zuerst "Code-Ausführung und Dateierstellung" unter Capabilities – Skills laufen ohne diese nicht aus.

### Claude Desktop

Claude Desktop installiert `/last30days` als MCP -Server über ein `.mcpb` -Bundle (ein One-Click Model Context Protocol-Paket).

1. Gehen Sie zum [latest release](https://github.com/mvanhorn/last30days-skill/releases/latest) und laden Sie die `.mcpb` für Ihre Plattform herunter:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Öffnen Sie Claude Desktop, gehen Sie zu Einstellungen > Erweiterungen und ziehen Sie die Datei hinein.
3. Wenn du aufgefordert wirst, füge API Schlüssel für die Quellen ein, die du aktivieren möchtest. Jedes Feld ist optional – die Engine verschlechtert sich in den reinen Webmodus, wenn du alle überspringst. Schlüssel werden in deinem Betriebssystem-Schlüsselbund gespeichert.
4. Starte Claude Desktopneu. Bitte Claude , "Peter Steinberger" oder ein anderes Thema zu recherchieren, und es wird das `research` Tool aufgerufen.

**Host-Anforderung:** Python 3.12+ auf PATH. Das Bundle liefert die Engine-Quelle, verwendet aber deinen lokalen Python -Interpreter. Installiere von [python.org](https://www.python.org/downloads/) auf Windows; macOS und die meisten Linux Distributionen liefern eine kompatible Version aus.

**Schlüssel synchronisieren sich nicht mit der Code-Fähigkeit.** Claude Desktop und Claude Code pflegen absichtlich separate Zugangsdatenspeicher. Wenn du `~/.config/last30days/.env` bereits für die Code-Fähigkeit konfiguriert hast, gibst du dieselben Schlüssel hier einmal erneut ein.

Windows Unterstützung wird aufgeschoben, bis die Einstiegspunkte pro Plattform geregelt sind; eine Folgeangelegenheit verfolgen.

### OpenClaw

```bash
clawhub install last30days-official
```

Für X/Twitter-Aktionsworkflows außerhalb `/last30days` Forschung, wie zum Beispiel Beiträge
Tweets oder Antworten, Follower-Export, Medienhandhabung, Monitore und Gewinnspiele
Bei Draws [TweetClaw](https://github.com/Xquik-dev/tweetclaw) als Begleiter verwenden
OpenClaw Plugin. TweetClaw wird von Xquik-dev gepflegt und nur als ein
Optionaler Begleitpfad, keine Abhängigkeit oder Unterstützung der letzten 30 Tage.

### Handbuch (Entwickler)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

Der Symlink hält die Installation beim Bearbeiten synchron mit deinem Arbeitsbaum – kein erneutes Kopieren erforderlich. Für `claude.ai`baue die `.skill` -Datei aus der Quelle: `bash skills/last30days/scripts/build-skill.sh` erzeugt `dist/last30days.skill`.

Reddit (mit Kommentaren), Hacker News, Polymarketund GitHub funktionieren sofort. Null Konfiguration. Führe `/last30days` einmal aus und der Setup-Wizard schaltet in 30 Sekunden weitere Quellen frei, einschließlich der kostenlosen arXiv und Techmeme CLIs.

## Bringen Sie Ihre eigenen Schlüssel mit

Diese Plattformen haben keine Beziehungen zueinander. X weiß nicht, was Reddit denkt. YouTube sieht TikToknicht. Aber du kannst deine eigenen API Schlüssel und Browser-Tokens mitbringen, und plötzlich hast du Zugriff auf alle auf einmal.

| Quellen | Was du brauchst | Kosten |
|---------|---------------|------|
| Reddit (mit Kommentaren) + HN + Polymarket + GitHub + StockTwits | Nichts | Kostenlos |
| arXiv + Techmeme | Kostenlose CLIS, automatisch durch Erststart-Einrichtung installiert | Kostenlos |
| X / Twitter | Melden Sie sich in jedem Browser bei x.com an oder stellen Sie `XQUIK_API_KEY` / `XAI_API_KEY` | Browser-Cookies sind kostenlos; Schlüssel sind anbieterspezifisch |
| YouTube | `brew install yt-dlp` | Kostenlos |
| Bluesky | App-Passwort von bsky.app | Kostenlos |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube Kommentare | ScrapeCreators Schlüssel | 10.000 kostenlose Anrufe, dann PAYG |
| Xiaohongshu (RED) | Führen Sie ein eingeloggtes x-mcp-Browser-Plugin oder `xiaohongshu-mcp` Dienst aus und melden Sie sich mit `--search xhs` pro Durchlauf oder `INCLUDE_SOURCES=xiaohongshu` in `.env`an; die letzten 30 Tage testen automatisch `http://localhost:18060` dann `http://host.docker.internal:18060`, oder verwenden Sie `XIAOHONGSHU_API_BASE` für eine benutzerdefinierte URL | Kein Last 30days API Key; hängt von deinem lokalen Browser-Session-Dienst ab |
| DripStack (Premium-Finanznewsletter) | Opt-in: `--search dripstack` pro Durchlauf oder `INCLUDE_SOURCES=dripstack` in `.env` | Kein Schlüssel; kostenlose öffentliche Suche API |
| Perplexity Sonar / Suche API / Tiefe Forschung | Perplexity Schlüssel oder OpenRouter-Schlüssel als Sonar-Fallback | Zahle nach Beginn. |
| Web Suche | Brave Suchtaste | 2.000 kostenlose Anfragen pro Monat |

### macOS Keychain (optional)

Auf macOS kannst du Schlüssel im System Keychain statt in einer `.env` -Datei speichern. Die Fähigkeit erkennt sie automatisch als Quelle mit niedrigster Priorität – `.env` die Dateien und die Prozessumgebung gewinnen trotzdem bei der Kollision.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Elemente werden unter Service-Namen `last30days-<KEY>` für den aktuellen Nutzer gespeichert. Auf Nicht-Darwin-Plattformen ist der Loader ein No-Op, sodass es keine Verhaltensänderung für Linux/Windows Nutzer gibt.

Haben Sie bereits Schlüssel unter verschiedenen Keychain Service-Namen? Stellen Sie das in [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) beschriebene nicht-geheime `LAST30DAYS_KEYCHAIN_ALIASES` Mapping ein, anstatt Geheimnisse zu kopieren.

Siehe [CONFIGURATION.md](CONFIGURATION.md) für die vollständige Schlüsselmatrix pro Quelle, Priorität des Reasoning-Providers und die Backend-Priorität der Websuche.

## Konfiguration

Zwei Dinge, die du wahrscheinlich am ersten Tag wissen solltest:

**Wo Forschungsdateien gespeichert sind.** `LAST30DAYS_MEMORY_DIR` standardmäßig auf `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Überschreiben Sie, indem Sie diesen Env-Var auf einen beliebigen Pfad in Ihrer Shell oder `--save-dir <path>` pro Durchlauf setzen. Verwenden Sie `--output <file>` , wenn Sie das gerenderte Ergebnis auf einem exakten Pfad benötigen, und verwenden Sie das von `--emit`gewählte Format. Verwenden Sie `--save-suffix=<name>` , um mehrere Varianten desselben Themas getrennt zu halten (z. B. pro Client). Jeder `--save-dir` Run erzeugt `<slug>-raw[-suffix].md`. Führen Sie `python3 skills/last30days/scripts/last30days.py --preflight` aus, um geplante Schreibarbeiten vor einem Forschungslauf zu überprüfen.

**Strukturierte Ausgabe für Agenten und Workflows.** Fragen Sie `/last30days` nach maschinenlesbaren JSON , um das stabile, versionierte Agentenprofil zu erhalten. Für den direkten Einsatz in der Engine in Skripten oder Entwicklung führen Sie `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`aus ; fügen Sie `--json-profile=raw` nur dann hinzu, wenn Sie den unversionierten internen `Report` Dump benötigen. Siehe das [JSON export field reference and versioning policy](docs/reference/json-export.md).

**Themenlose Entdeckung.** Bitten Sie `/last30days what's trending in AI agents?` , ein ranglistetes Discovery-Briefing zu erhalten, anstatt ein bereits bekanntes Thema zu recherchieren – auf einem Agentenhost läuft das Drei-Befehle-Host-Judged Protocol (das Modell benennt Themen, filtert Müll, bewertet den Wert und schreibt die Inhaltswinkel). Für den direkten Einsatz in Skripten oder Cron, führen Sie `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` aus (One-Shot: deterministische Themennamen, keine Angles); fügen Sie `--emit=json` für den versionierten Discovery-Vertrag hinzu. Discovery schließt sich gegenseitig mit einem Positionsthema und `--drill`aus.

**Trendüberwachung über Durchläufe hinweg.** Der Standardmodus erzeugt pro Durchlauf einen frischen Markdown-Snapshot. Um Erkenntnisse im Laufe der Zeit zu sammeln, fügen Sie `--store` hinzu, um in einer SQLite-Datenbank gespeichert zu werden, verwenden Sie [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) dann für geplante Durchläufe (mit optionalem Slack / Webhook-Delivery bei neuen Ergebnissen) und [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) für tägliche/wöchentliche Digests. Das vollständige Rhythmusmuster ist in [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Eine abonnierbare Forschungsbibliothek.** Bitten Sie `/last30days` , Ihren Bibliotheksfeed zu erstellen, oder verwenden Sie `python3 skills/last30days/scripts/last30days.py library feed` direkt für Skripting und Entwicklung. Sie wandelt gespeicherte Briefs in `index.html`, ein lokales Atom- `feed.xml`und lesbare Brief-Seiten um. Fügen Sie `--publish` nur dann hinzu, wenn Sie den HTML Index und die Brief-Seiten gehostet haben möchten; die Veröffentlichung ist standardmäßig explizit opt-in und öffentlich. Um den Atom-Feed abonnierbar zu machen, hosten Sie das generierte Ausgabeverzeichnis auf einem statischen Host wie GitHub Pages.

**Durchsuche alles, was du recherchiert hast.** Frag `/last30days search my library for MCP servers` oder `/last30days have I researched MCP servers before?`. Für die direkte Engine-Nutzung führe `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`aus. Die Suche ist offline und deterministisch: Sie indexiert schrittweise dieselben gespeicherten Briefs, die vom Bibliotheksfeed verwendet werden, führt übereinstimmende Shop-Sichtungen pro Durchlauf zusammen und gruppiert Ergebnisse nach Thema und Datum. Frische Durchläufe zeigen außerdem einen kompakten Abschnitt **Aus deiner Bibliothek** an, wenn frühere Forschung das aktuelle Thema überschneidet; setze `LAST30DAYS_LIBRARY_CONTEXT=off` so ein, dass dieser passive Kontext deaktiviert wird.

Pro-Client-Wrapper-Skripte, benutzerdefinierte Kategorie-Peer-Subreddits und der experimentelle Beta-Kanal für laufende Anpassungen sind ebenfalls in [CONFIGURATION.md](CONFIGURATION.md)dokumentiert.

## Showcase: Community-Forschungsfeeds

Hast du ein wiederkehrendes KI-Update, eine Marktbeobachtung oder eine wunderbar enge Obsession mit den letzten 30 Tagen veröffentlicht? Teile die URL der öffentlichen Bibliothek – oder die Atom-URL nach dem Hosting `feed.xml` auf einem statischen Host – in [the community showcase thread](https://github.com/mvanhorn/last30days-skill/issues/532)Community-Feeds werden hier verlinkt, sobald ihre Eigentümer sie einreichen; der Thread ist in der Zwischenzeit der Sammelpunkt.

## Wie es funktioniert

1. **Du tippst ein Thema ein.** Person, Unternehmen, Produkt, Technologie, "X vs. Y." Alles.
2. **Der Agent entscheidet, wer zählt.** Findet X Handles (einschließlich Gründer), GitHub Reposis, Subreddits, TikTok Hashtags YouTube Kanäle. Für "Kanye West" kennt es r/hiphopheads, @kanyewestund "bully review" auf YouTube. Für "OpenClaw" wird openclaw/openclaw auf GitHub aufgelöst und live die Sternenzahlen angezeigt.
3. **Alle Quellen wurden parallel gesucht.** Multi-Query-Erweiterung. Ergebnisse bewertet nach Engagement, Relevanz, Frische.
4. **Die Tiefe, die sonst niemand hat.** Vollständige YouTube Transkripte aus Reaktionsvideos. Top Reddit Kommentare mit Upvote-Zählen. TikTok Bildunterschriften. Polymarket Chancen. Nicht nur Titel und Links.
5. **Gleiche Geschichte, fusioniert.** Wireless Festival am Redditangekündigt, besprochen am X, Ticketpreise auf TikTok = ein Cluster, nicht drei separate Artikel.
6. **Zusammengefasst in einem Brief.** Basierend auf spezifischen Daten. Zitiert von der Quelle. Bewertet nach dem, womit sich die Leute tatsächlich beschäftigen. Nicht "Hier ist, was ich gefunden habe." Es ist "Hier ist, was zählt."
7. **Dann wird es dein Experte.** Nach einem Durchlauf weiß deine Claude Sitzung alles, was die Community weiß. Stelle Nachfragen. Lass sie Prompts schreiben, E-Mails entwerfen, Reisen planen, Architektursysteme aufbauen – alles basiert auf dem, was gerade real ist.

## Was die Leute sagen

> "Ich habe eine Claude Code Fähigkeit gefunden, die jedes Thema aus den letzten 30 Tagen Reddit, X, YouTubeund HN erforscht. Dann schreibt er die Prompts für dich. Ich habe vor jedem Inhalt, den ich schreibe, manuell Reddit und X nach Recherchen gesucht. Tab für Tab. Faden für Faden. Das ist der Teil, der 90 Minuten dauert. Das eliminiert es." -@itsjasonai

> "Diese eine Fähigkeit hat meinen gesamten Recherche-Workflow ersetzt. Du gibst ihm ein Thema, sie sammelt Reddit, Xund das Web nach dem, worüber die Leute tatsächlich sprechen. Keine alten Blogbeiträge. Echte Gespräche der letzten 30 Tage." -@itswilsoncharles

> "5 der 10 trendigen Repos auf GitHub heute sind Claude Werkzeuge. #1: Mvanhorn/last30days-Fähigkeit" -@yieldhunter95

## Open Source

MIT-Lizenz. Kein Tracking. Keine Analyse. Deine Forschung bleibt auf deinem Rechner. 2.700+ Tests.

Gebaut mit Python 3.12+, yt-dlp, Node.js (bereitgestellt Bird Client für X Suche) und ScrapeCreators API. v3-Engine-Architektur von [@j-sperling](https://github.com/j-sperling).

Siehe [CONTRIBUTING.md](CONTRIBUTING.md) , um einen PRzu öffnen, [CONTRIBUTORS.md](CONTRIBUTORS.md) für die vollständige Liste der Community-Mitwirkenden und [CHANGELOG.md](CHANGELOG.md) für die Versionshistorie.

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
