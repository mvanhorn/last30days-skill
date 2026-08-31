# obsidian2date

[English](README.md) | Français | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

**Recherchez n'importe quelle fenêtre récente. Gardez l'essentiel dans Obsidian.**

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Tests](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml/badge.svg)](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml)

`obsidian2date` recherche ce que les gens disent réellement d'un sujet sur
Reddit, X, YouTube, HN, GitHub, Polymarket et le web — sur la fenêtre que
vous demandez (la semaine dernière, les 7 derniers jours, les 90 derniers
jours ; 30 jours n'est que la valeur par défaut) — et transforme chaque
exécution en notes Obsidian durables et liées entre elles.

Chaque exécution produit :

- une **note d'exécution** sourcée
- un **briefing** compact
- des `[[wikilinks]]` vers les exécutions liées
- un **Index** et un **Dashboard** mis à jour

Pas de suivi. MIT. Fork public de
[last30days-skill](https://github.com/mvanhorn/last30days-skill) ; le moteur
de recherche amont reste fusionnable. Requiert Python 3.12+ et un coffre
(vault) Obsidian ; les sources et les clés API sont optionnelles — voir
[CONFIGURATION.md](CONFIGURATION.md).

## Utiliser comme commande slash (voie principale)

`obsidian2date` est un Agent Skill : installez le dépôt une fois, puis
tapez `/obsidian2date <sujet>` dans votre agent. Le skill lance le moteur de
recherche, résout votre coffre, écrit les notes et signale les chemins.
Aucun flag à mémoriser — dites « la semaine dernière » ou « sur les 90
derniers jours » dans la demande, et le skill le traduit en flags moteur
appropriés.

| Hôte | Installation | Ensuite |
| --- | --- | --- |
| Claude Code | `npx skills add pauleschwarz/obsidian2date -g -y` (ou ajoutez ce dépôt comme `.claude-plugin`) | `/obsidian2date <sujet>` |
| Codex | le dépôt fournit `.codex-plugin/plugin.json` | `/obsidian2date <sujet>` |
| Grok | `grok plugin marketplace add pauleschwarz/obsidian2date` | `/obsidian2date <sujet>` |
| Gemini CLI | le dépôt fournit `gemini-extension.json` | `/obsidian2date <sujet>` |
| OpenClaw / hôtes agents.md | le dépôt fournit le manifeste `.agents/` | `/obsidian2date <sujet>` |
| pi / tout agent compatible skills | liez ou copiez `skills/obsidian2date/` dans le dossier de skills de l'agent | `/obsidian2date <sujet>` |

Ce que le skill fait à chaque exécution (voir
[`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md) — la
spécification d'exécution canonique que lit le modèle) :

1. résoudre votre coffre (demander une fois, s'en souvenir pour la session)
2. déduire la fenêtre de votre demande (30 jours par défaut)
3. lancer le moteur de recherche avec `--emit=obsidian`
4. signaler honnêtement le chemin du briefing, de la note d'exécution et toute source partielle ou indisponible

## Démarrage rapide (fallback CLI)

Pour le scripting, cron ou les tests du moteur en développement, appelez la
CLI directement. C'est la voie de secours, pas la principale — la commande
slash ci-dessus est le produit.

```bash
git clone https://github.com/pauleschwarz/obsidian2date.git
cd obsidian2date

python3 skills/last30days/scripts/last30days.py \
  "local LLM agent frameworks" \
  --emit=obsidian \
  --obsidian-vault /chemin/vers/votre/vault
```

Ou configurez le coffre une fois :

```bash
export OBSIDIAN2DATE_VAULT=/chemin/vers/votre/vault
python3 skills/last30days/scripts/last30days.py "topic" --emit=obsidian
```

### Fenêtre temporelle

`30` jours n'est que la valeur par défaut. Demandez ce que vous voulez :

```bash
python3 skills/last30days/scripts/last30days.py "AI video tools" --emit=obsidian --days 7    # la semaine dernière
python3 skills/last30days/scripts/last30days.py "rust async runtimes" --emit=obsidian --days 90  # balayage trimestriel
python3 skills/last30days/scripts/last30days.py "election odds" --emit=obsidian --days 14 --as-of 2026-08-15
```

Dans la commande slash, dites-le simplement : « recherche les 7 derniers
jours d'AI video tools ».

### Résolution du coffre

La cible d'export est résolue dans cet ordre :

1. `--obsidian-vault PATH` (un chemin explicite inexistant est créé pour l'export)
2. `OBSIDIAN2DATE_VAULT`
3. `LAST30DAYS_OBSIDIAN_VAULT`
4. un `~/Desktop/brain-paul` existant

Les candidats d'environnement et de bureau doivent déjà être des
répertoires. Une valeur d'environnement de coffre présente mais vide ou
composée d'espaces désactive délibérément les replis implicites. Si rien ne
se résout, la commande s'arrête avec :

```text
No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT.
```

Utilisez `~/...` ou un chemin absolu dans les fichiers `.env` ; `$HOME` n'y
est pas développé. Les notes existantes ne sont jamais écrasées ; les
collisions de noms de fichiers reçoivent un suffixe numérique.

## Ce qui est écrit

Disposition par défaut sous la racine du coffre :

```text
90_Quellen/obsidian2date/
  runs/YYYY-MM-DD-<slug>.md
  briefings/YYYY-MM-DD-<slug>-briefing.md
  Index.md
  Dashboard.md
```

Les notes ne s'écrasent jamais. Les collisions le même jour reçoivent des
suffixes numériques. Les exécutions antérieures liées sont connectées via
les `[[wikilinks]]` Obsidian lorsqu'un chevauchement de tokens est détecté.

## Sources et clés

Le même socle que l'amont :

- **Sans clé par défaut :** Reddit, Hacker News, Polymarket, GitHub, Web
- **Optionnelles :** X (cookies de navigateur / backends), YouTube
  (`yt-dlp`), TikTok/IG (ScrapeCreators), plus d'autres backends payants/opt-in

Voir [`CONFIGURATION.md`](CONFIGURATION.md) pour la matrice complète et la
configuration des clés.

## Diagnostics sûrs

Exécutez une vérification des seules permissions avant la recherche :

```text
$ python3 skills/last30days/scripts/last30days.py --preflight
last30days preflight
Status: Ready to research with safe defaults.
...
Local writes:
- none planned
```

`--preflight` est sûr : il s'exécute **sans lire les cookies, écrire des
fichiers ni lancer de recherche**. Pour dépanner des sources ou des backends
installés, utilisez plutôt la vérification de santé :

```bash
python3 skills/last30days/scripts/last30days.py doctor
```

## Les modes amont fonctionnent toujours

```bash
# enveloppe de synthèse compacte d'origine
python3 skills/last30days/scripts/last30days.py "topic" --emit=compact

# JSON pour agents
python3 skills/last30days/scripts/last30days.py "topic" --emit=json

# brief de production
python3 skills/last30days/scripts/last30days.py "topic" --emit=brief
```

## Relation avec l'amont

| Sujet | Politique |
| --- | --- |
| Moteur de recherche | Rester fusionnable avec `upstream/main` |
| Export Obsidian | Module additif : `lib/obsidian_export.py` |
| Marque / skill | `obsidian2date` |
| Licence | MIT ; conserver les mentions de copyright amont |

```bash
git remote add upstream https://github.com/mvanhorn/last30days-skill.git
git fetch upstream
git merge upstream/main
```

## Crédits

- Moteur de recherche amont : [Matt Van Horn / last30days](https://github.com/mvanhorn/last30days-skill)
- Chemin d'export Obsidian + packaging du fork public : [pauleschwarz](https://github.com/pauleschwarz)

## Licence

MIT. Voir [LICENSE](LICENSE).
