# obsidian2date

[English](README.md) | [Français](README.fr.md) | Deutsch | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

**Recherchiere jedes beliebige Zeitfenster. Behalte das Nützliche in Obsidian.**

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Tests](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml/badge.svg)](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml)

`obsidian2date` recherchiert, was Menschen tatsächlich zu einem Thema sagen —
auf Reddit, X, YouTube, HN, GitHub, Polymarket und im Web — über jedes
Zeitfenster deiner Wahl (letzte Woche, letzte 7 Tage, letzte 90 Tage; 30 Tage
sind nur der Standard) — und macht aus jedem Lauf dauerhafte, verknüpfte
Obsidian-Notizen.

Jeder Lauf erzeugt:

- eine quellenbasierte **Run-Notiz**
- ein kompaktes **Briefing**
- `[[Wikilinks]]` zu verwandten Läufen
- einen aktualisierten **Index** und ein **Dashboard**

Kein Tracking. MIT. Öffentlicher Fork von
[last30days-skill](https://github.com/mvanhorn/last30days-skill); die
Upstream-Research-Engine bleibt mergebar. Benötigt Python 3.12+ und ein
Obsidian-Vault; Quellen und API-Keys sind optional — siehe
[CONFIGURATION.md](CONFIGURATION.md).

## Als Slash-Command benutzen (Hauptpfad)

`obsidian2date` ist ein Agent Skill: installiere das Repo einmal und tippe
dann `/obsidian2date <topic>` in deinen Agenten. Der Skill startet die
Research-Engine, löst deinen Vault auf, schreibt die Notizen und meldet die
Pfade. Keine Flags zum Auswendiglernen — sag "letzte Woche" oder "über die
letzten 90 Tage" in der Anfrage, und der Skill übersetzt das in die richtigen
Engine-Flags.

| Host | Installation | Danach |
| --- | --- | --- |
| Claude Code | `npx skills add pauleschwarz/obsidian2date -g -y` (oder dieses Repo als `.claude-plugin` hinzufügen) | `/obsidian2date <topic>` |
| Codex | Repo bringt `.codex-plugin/plugin.json` mit | `/obsidian2date <topic>` |
| Grok | `grok plugin marketplace add pauleschwarz/obsidian2date` | `/obsidian2date <topic>` |
| Gemini CLI | Repo bringt `gemini-extension.json` mit | `/obsidian2date <topic>` |
| OpenClaw / agents.md-Hosts | Repo bringt `.agents/`-Manifest mit | `/obsidian2date <topic>` |
| pi / jeder skill-fähige Agent | `skills/obsidian2date/` ins Skills-Verzeichnis des Agenten symlinken oder kopieren | `/obsidian2date <topic>` |

Was der Skill bei jedem Lauf tut (siehe
[`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md) — die
kanonische Laufzeitspezifikation, die das Modell liest):

1. deinen Vault auflösen (einmal fragen, dann für die Session merken)
2. das Zeitfenster aus deiner Anfrage ableiten (Standard: 30 Tage)
3. die Research-Engine mit `--emit=obsidian` ausführen
4. Briefing-Pfad, Run-Notiz-Pfad und alle partiellen oder nicht erreichbaren Quellen ehrlich melden

## Schnellstart (CLI-Fallback)

Für Skripte, Cron oder Dev-Zeit-Engine-Tests rufst du die CLI direkt auf.
Das ist der Fallback-Pfad, nicht der Hauptpfad — der Slash-Command oben ist
das Produkt.

```bash
git clone https://github.com/pauleschwarz/obsidian2date.git
cd obsidian2date

python3 skills/last30days/scripts/last30days.py \
  "local LLM agent frameworks" \
  --emit=obsidian \
  --obsidian-vault /pfad/zu/deinem/vault
```

Oder den Vault einmal konfigurieren:

```bash
export OBSIDIAN2DATE_VAULT=/pfad/zu/deinem/vault
python3 skills/last30days/scripts/last30days.py "topic" --emit=obsidian
```

### Zeitfenster

`30` Tage sind nur der Standard. Frag nach allem Möglichen:

```bash
python3 skills/last30days/scripts/last30days.py "AI video tools" --emit=obsidian --days 7    # letzte Woche
python3 skills/last30days/scripts/last30days.py "rust async runtimes" --emit=obsidian --days 90  # Quartals-Sweep
python3 skills/last30days/scripts/last30days.py "election odds" --emit=obsidian --days 14 --as-of 2026-08-15
```

Im Slash-Command sagst du es einfach: "recherchiere die letzten 7 Tage zu AI
video tools".

### Vault-Auflösung

Das Export-Ziel wird in dieser Reihenfolge aufgelöst:

1. `--obsidian-vault PATH` (ein explizit fehlender Pfad wird für den Export angelegt)
2. `OBSIDIAN2DATE_VAULT`
3. `LAST30DAYS_OBSIDIAN_VAULT`
4. ein vorhandenes `~/Desktop/brain-paul`

Umgebungs- und Desktop-Kandidaten müssen bereits Verzeichnisse sein. Ein
vorhandener leerer oder nur aus Whitespace bestehender Vault-Umgebungswert
deaktiviert absichtlich alle impliziten Fallbacks. Wenn nichts aufgelöst
wird, stoppt der Befehl mit:

```text
No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT.
```

Verwende `~/...` oder absolute Pfade in `.env`-Dateien; `$HOME` wird dort
nicht expandiert. Vorhandene Notizen werden nie überschrieben;
Dateinamen-Kollisionen erhalten ein numerisches Suffix.

## Was geschrieben wird

Standard-Layout unter dem Vault-Root:

```text
90_Quellen/obsidian2date/
  runs/YYYY-MM-DD-<slug>.md
  briefings/YYYY-MM-DD-<slug>-briefing.md
  Index.md
  Dashboard.md
```

Notizen werden nie überschrieben. Kollisionen am selben Tag erhalten
numerische Suffixe. Verwandte frühere Läufe werden über Obsidian
`[[Wikilinks]]` verknüpft, wenn Token-Überlappung erkannt wird.

## Quellen & Keys

Gleicher Standard wie Upstream:

- **Ohne Keys standardmäßig:** Reddit, Hacker News, Polymarket, GitHub, Web
- **Optional:** X (Browser-Cookies / Backends), YouTube (`yt-dlp`),
  TikTok/IG (ScrapeCreators), plus weitere bezahlte/opt-in Backends

Siehe [`CONFIGURATION.md`](CONFIGURATION.md) für die vollständige Matrix und
das Key-Setup.

## Sichere Diagnose

Vor der Recherche einen reinen Rechte-Check ausführen:

```text
$ python3 skills/last30days/scripts/last30days.py --preflight
last30days preflight
Status: Ready to research with safe defaults.
...
Local writes:
- none planned
```

`--preflight` ist sicher: Er läuft **ohne Cookies zu lesen, Dateien zu
schreiben oder Recherche zu starten**. Zur Fehlerbehebung von Quellen oder
installierten Backends nutze stattdessen den Health-Check:

```bash
python3 skills/last30days/scripts/last30days.py doctor
```

## Upstream-Modi funktionieren weiterhin

```bash
# ursprüngliche kompakte Synthese-Ausgabe
python3 skills/last30days/scripts/last30days.py "topic" --emit=compact

# Agent-JSON
python3 skills/last30days/scripts/last30days.py "topic" --emit=json

# Production-Brief
python3 skills/last30days/scripts/last30days.py "topic" --emit=brief
```

## Verhältnis zu Upstream

| Aspekt | Richtlinie |
| --- | --- |
| Research-Engine | Mergebar mit `upstream/main` bleiben |
| Obsidian-Export | Additiv-Modul: `lib/obsidian_export.py` |
| Branding / Skill | `obsidian2date` |
| Lizenz | MIT; Upstream-Copyright-Hinweise erhalten |

```bash
git remote add upstream https://github.com/mvanhorn/last30days-skill.git
git fetch upstream
git merge upstream/main
```

## Credits

- Upstream-Research-Engine: [Matt Van Horn / last30days](https://github.com/mvanhorn/last30days-skill)
- Obsidian-Export-Pfad + öffentliches Fork-Packaging: [pauleschwarz](https://github.com/pauleschwarz)

## Lizenz

MIT. Siehe [LICENSE](LICENSE).
