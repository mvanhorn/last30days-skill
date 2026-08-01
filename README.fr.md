# /last30days

[English](README.md) | Français | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

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

**Un moteur de recherche piloté par un agent IA, qui classe les résultats selon les upvotes, les likes et l'argent réel — pas selon des rédacteurs.**

Ce README décrit le pipeline v3 actuel. La spécification d'exécution de la skill se trouve dans [skills/last30days/SKILL.md](skills/last30days/SKILL.md), qui fait référence pour le comportement des commandes et de la configuration.

**Claude Code (recommandé — mises à jour automatiques via la marketplace) :**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI, ou l'un des 50+ hôtes [Agent Skills](https://agentskills.io) :**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` installe la skill globalement pour votre utilisateur, donc disponible dans tous vos projets. Omettez ce flag pour une installation limitée au projet.)

D'autres options d'installation (claude.ai web, OpenClaw, manuelle) dans la section [Installation](#installation) ci-dessous.

Zéro configuration. Reddit, HN, Polymarket et GitHub fonctionnent immédiatement. Lancez la skill une fois : l'assistant de configuration débloque X, YouTube, TikTok, arXiv, Techmeme et d'autres sources en 30 secondes.

---

Les upvotes de Reddit. Les likes de X. Les transcriptions YouTube. L'engagement TikTok. Les cotes Polymarket, adossées à de l'argent réel et à des informations d'initiés. Ce sont des millions de personnes qui votent chaque jour avec leur attention et leur portefeuille. /last30days interroge tout cela en parallèle, classe les résultats selon ce avec quoi les gens interagissent vraiment, et un agent IA joue le rôle de juge pour en tirer un seul brief.

Google agrège des rédactions. /last30days interroge les gens.

Cette recherche est introuvable ailleurs, parce qu'aucune IA n'a accès à l'ensemble. Google ne touche ni aux commentaires Reddit ni aux posts X. ChatGPT a un accord avec Reddit mais ne sait chercher ni sur X ni sur TikTok. Gemini a YouTube mais pas Reddit. Claude n'a nativement accès à aucun des trois. Chaque plateforme est un jardin clos, avec son API, ses tokens et son authentification. Mais vous pouvez apporter vos propres clés et vos sessions de navigateur : d'un coup, un agent IA peut toutes les interroger en même temps, les comparer entre elles et vous dire ce qui compte vraiment.

C'est ça, le déclic. Pas un meilleur moteur de recherche. Une douzaine de plateformes cloisonnées, reliées par un agent.

```
/last30days Peter Steinberger
```

Vous avez une réunion demain. Vous cherchez la personne sur Google. Vous tombez sur son LinkedIn de 2023. /last30days vous donne ce qu'elle fait vraiment ce mois-ci : elle a rejoint OpenAI pour travailler sur Codex, elle conteste l'interdiction des agents tiers décrétée par Anthropic, elle a livré 23 PR avec un taux de merge de 85 %, elle construit « LobsterOS » pour piloter des agents entre appareils, et un fil r/ClaudeCode a atteint 569 upvotes en débattant de savoir si elle est un héros ou « insupportable ». Le tout dispersé entre des posts X, des fils Reddit, des transcriptions YouTube et des commits GitHub. Rien de tout ça n'était sur Google.

## Pourquoi ce projet existe

Je l'ai construit pour suivre le rythme de l'IA. Tout change chaque jour, et les passionnés de Reddit et de X sont toujours au courant les premiers. J'avais besoin de meilleurs prompts, et les données d'entraînement avaient toujours plusieurs mois de retard sur ce que la communauté avait déjà compris.

Mais c'est devenu quelque chose de plus large. Aujourd'hui je le lance avant un rendez-vous commercial, pour connaître la vérité des 30 derniers jours sur une entreprise. Avant une réunion, pour lire les tweets récents et les transcriptions de podcasts de mon interlocuteur. Avant un séjour à Disney World, pour savoir quelles attractions sont fermées et ce que la communauté pense de Genie+. Avant de construire quoi que ce soit, pour savoir sur quels problèmes les gens butent réellement.

Si vous rencontrez un PDG, avez-vous lu tous ses tweets et toutes ses transcriptions YouTube des 30 derniers jours ? Moi, oui.

## Les sources, classées par les gens

| Source | Ce que les gens vous disent |
|--------|--------------------------|
| **Reddit** | L'avis brut. Les meilleurs commentaires avec leur vrai nombre d'upvotes, gratuit, sans clé API. Les vraies opinions que Google enterre. |
| **X / Twitter** | La réaction à chaud, le fil d'expert, la première réaction à l'actualité. Premiers informés, premiers à débattre. |
| **YouTube** | L'analyse approfondie de 45 minutes. Des transcriptions complètes, fouillées pour en extraire les 5 phrases citables qui comptent. |
| **TikTok** | Le créateur qui touche 3,6 millions de personnes avec un angle que vous ne trouverez jamais sur Google. |
| **Instagram Reels** | Le regard des influenceurs, avec la transcription de ce qui est dit. Le signal de la culture visuelle. |
| **Hacker News** | Le consensus des développeurs. 825 points, 899 commentaires. Là où les gens techniques débattent vraiment. |
| **Polymarket** | Pas des opinions. Des cotes. Adossées à de l'argent réel. 96 % de probabilité sur des ventes d'album. 4 % sur une acquisition. |
| **GitHub** | Pour les personnes : rythme des PR, meilleurs dépôts par étoiles, notes de version. Pour les sujets : issues et discussions. |
| **Digg** | Des groupes d'articles sélectionnés depuis le classement AI 1000 de Digg (environ 1000 comptes IA à fort signal sur X), avec des citations attribuables intégrées (sans authentification X). Activé automatiquement quand `digg-pp-cli` est présent dans le PATH. |
| **arXiv** | Les articles scientifiques derrière le battage médiatique. La recherche publiée dans la fenêtre, gratuit, sans clé API. Activé automatiquement quand `arxiv-pp-cli` est présent dans le PATH (la configuration initiale l'installe). |
| **Techmeme** | La couche éditoriale de l'actu tech, restreinte à votre fenêtre de 30 jours. Gratuit, sans clé API. Activé automatiquement quand `techmeme-pp-cli` est présent dans le PATH (la configuration initiale l'installe). |
| **LinkedIn** | Le signal professionnel. Posts et articles, les articles étant pondérés comme signal fort. |
| **StockTwits** | Le sentiment des traders. S'active automatiquement quand votre sujet est un ticker ou une crypto. |
| **Threads** | La couche texte de l'après-Twitter. Les conversations des créateurs et des marques. |
| **Pinterest** | La découverte visuelle. Épingles, enregistrements et commentaires sur des produits et des idées. |
| **Xiaohongshu (RED)** | Les signaux chinois sur le lifestyle, les produits et les créateurs. À demander explicitement avec `--search xhs` quand un plugin de navigateur x-mcp connecté ou un service `xiaohongshu-mcp` tourne en local. |
| **Bluesky** | La couche sociale décentralisée. Les posts AT Protocol issus de la migration post-Twitter. |
| **Perplexity** | La synthèse Sonar sourcée, les résultats bruts de la Search API et Deep Research. |
| **Web** | La couverture éditoriale, les comparatifs de blogs. Un signal parmi d'autres, pas le seul. |

La communauté en ajoute sans cesse. Truth Social et d'autres sources de niche sont déjà dans le moteur, et d'autres arrivent.

Un fil Reddit à 1 500 upvotes est un signal plus fort qu'un billet de blog que personne n'a lu. Un TikTok à 3,6 millions de vues en dit plus sur ce qui compte culturellement qu'un communiqué de presse. Des cotes Polymarket adossées à 66 000 $ de volume sont plus difficiles à contester que l'intuition d'un éditorialiste.

La synthèse classe selon ce avec quoi de vraies personnes ont vraiment interagi. La pertinence sociale, pas la pertinence SEO.

## Ce que les gens en font vraiment

**Avant une réunion.** `/last30days Peter Steinberger` — a rejoint l'équipe Codex d'OpenAI, conteste l'interdiction des agents tiers décrétée par Anthropic, 23 PR mergées avec un taux de merge de 85 % sur GitHub, construit LobsterOS pour piloter des agents entre appareils. r/ClaudeCode : « Depuis la sortie d'OpenClaw, tout le monde savait que si vous passiez par autre chose que l'API, vous finiriez par être banni » (227 upvotes). Ça, ce n'est pas sur LinkedIn.

**Pour lire les signaux de recrutement.** `/last30days Listen Labs --hiring-signals` — les offres d'emploi et les pages carrières actuelles deviennent des preuves citées de changements de priorités : recrutements en sécurité entreprise, customer success, infrastructure ou expansion produit. Le rapport dit ce que le recrutement semble signaler, pas ce que la roadmap va livrer.

**Pour repérer un sujet avant son pic.** Demandez `/last30days what's exploding in AI agents?` et la skill bascule en mode découverte : le moteur balaie les listings de catégories Reddit, la une et les meilleures histoires de Hacker News, le flux AI 1000 de Digg, et X si vous êtes authentifié ; votre agent évalue les candidats (noms, filtrage du bruit, intérêt réel) et rédige des angles pour un podcast ou un article X ; vous obtenez ensuite 5 à 10 sujets classés par vélocité. Chaque résultat comprend des chiffres multi-sources, une étiquette de momentum et une commande `/last30days "<topic>"` prête à lancer.

**Quand quelque chose sort.** `/last30days Kanye West` — le Royaume-Uni a bloqué son visa, le Wireless Festival est annulé, les sponsors ont fui. Mais BULLY est entré n° 2 au Billboard. Fantano est revenu de son « Yay sabbatical » pour le chroniquer (653 000 vues). SoFi Homecoming a fait monter Lauryn Hill et Travis Scott sur scène pour 44 titres. Polymarket : « Kanye tweetera-t-il de nouveau ? » 86 % de oui. 23 fils Reddit, 17 vidéos YouTube, 86 000 upvotes.

**Pour comparer des outils.** `/last30days OpenClaw vs Hermes vs Paperclip` — « Ce ne sont pas des concurrents, ce sont des couches. » OpenClaw est la couche d'exécution (351 000 étoiles GitHub, en production), Hermes est le cerveau qui s'améliore tout seul (31 000 étoiles), Paperclip est l'organigramme (49 000 étoiles). Nombres d'étoiles récupérés en direct via l'API GitHub, pas repris de billets de blog périmés. Tableau comparatif avec architecture, mémoire, sécurité et cas d'usage idéal. Selon @IMJustinBrooke : « OpenClaw = Salamèche, Hermes = Dracaufeu. »

**Pour comprendre le monde.** `/last30days Iran vs USA` — 38e jour de guerre. Ultimatum de Trump, fixé à mardi, pour que l'Iran rouvre le détroit d'Ormuz. Deux avions de combat américains abattus. Le pétrole à 126 $ le baril. L'AIE parle de « la plus grande perturbation d'approvisionnement de l'histoire du marché pétrolier mondial ». Polymarket : cessez-le-feu avant le 31 décembre à 74 %. 27 posts X, 10 vidéos YouTube, 20 marchés de prédiction.

**Avant un voyage.** `/last30days Universal Epic Universe` — l'extension est déjà en construction. Permis « Project 680 » déposé. Spectacle de feux d'artifice confirmé par les travaux mais toujours pas annoncé. Temps d'attente : Mine-Cart Madness à 148 minutes en moyenne. Toujours pas de pass annuel, et les habitants s'agacent. Stardust Racers fermé pour rénovation jusqu'au 5 avril.

**Pour apprendre vite.** `/last30days Nano Banana Pro prompting` — les prompts structurés en JSON remplacent l'empilement de tags. Le format imbriqué de @pictsbyai évite le « concept bleeding ». Mieux vaut éditer que régénérer. Et ensuite, la skill vous écrit un prompt de production en appliquant exactement ce que la communauté a validé.

## Nouveautés

Depuis l'annonce de la v3.3 en mai, et jusqu'à la v3.11.1 (juillet 2026) : 175 PR mergées — dont 122 venant de 52 contributeurs de la communauté — réparties sur 15 versions. Voici ce qui a atterri.

### Citoyen de première classe sur OpenAI Codex

/last30days est désormais un plugin Codex natif avec configuration guidée : pas un portage, un vrai citoyen de première classe. Les citations tiennent compte du rendu, ce qui fait que la sortie Codex se lit comme un brief et non comme une soupe d'URL (#694), et le même moteur tourne sur Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClaw et 50+ hôtes Agent Skills. Manifeste du plugin Codex par [@rfoust](https://github.com/rfoust) (#686), correctif d'authentification Codex par [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmeme et Digg — gratuits, sans clé API

arXiv apporte les articles scientifiques derrière le battage médiatique et Techmeme la couche éditoriale de l'actu tech — gratuits, sans aucune clé, et la configuration initiale installe leurs CLI pour qu'ils s'activent tout seuls (#709). Les groupes d'articles AI 1000 de Digg arrivent de la même façon, sans authentification X : la configuration installe pour vous la CLI Digg gratuite (#590). Trustpilot est disponible en option pour la recherche sur les marques grand public.

### Reddit gratuit, avec de vrais scores et les meilleurs commentaires

L'API .json publique de Reddit a disparu ; la voie gratuite est revenue plus forte. Flux RSS sans clé et scraping de shreddit (#457), découverte de subreddits dédiés avec de vrais décomptes d'upvotes via arctic-shift (#696), et un seuil de pertinence pour qu'un post viral hors sujet ne détourne pas votre brief (#488, merci [@rzachsmith](https://github.com/rzachsmith)). Pas de clé API. De vrais scores. Les meilleurs commentaires inclus.

### Les meilleurs commentaires dans chaque brief

Les commentaires sont maintenant une couche activée par défaut sur toutes les sources : commentaires Instagram avec une diversité fondée sur le rang, pour que cinq avis tranchés ne viennent pas tous du même post (#751), commentaires YouTube plus une récupération de transcription via ScrapeCreators quand yt-dlp échoue (#637), et commentaires plébiscités par la communauté intégrés au scoring Best Takes, pour que les meilleures punchlines survivent au classement (#592, #608).

### Une seule commande doctor

Demandez un diagnostic : doctor teste chaque source, puis prescrit les correctifs exacts — quelle clé manque, quelle CLI est absente du PATH, quel cookie a expiré (#753). Fini de deviner pourquoi X est revenu à vide.

### La recherche X, reconstruite

Le pipeline X a été repensé de fond en comble : des voies FROM et ABOUT pour que les posts d'une personne et la conversation à son sujet soient classés tous les deux (#610), désambiguïsation des sous-requêtes selon la personne visée (#611), vérification de la paternité des posts avec classement par signaux d'interaction (#613), et une source X unique avec bascule automatique entre backends (#622). Plus un `--diagnose` honnête qui teste vraiment l'authentification (#609).

### De nouvelles sources

LinkedIn via ScrapeCreators, avec les articles comme signal fort ([@ravstr](https://github.com/ravstr), #702). StockTwits s'active automatiquement sur les sujets liés aux tickers et aux cryptos ([@wtiwana](https://github.com/wtiwana), #658). Perplexity a gagné des modes API directs et Deep Research en asynchrone ([@sk-holmes](https://github.com/sk-holmes), #629).

### Durci par la communauté

La vague sécurité est presque entièrement le fait de la communauté : correctifs XSS stocké dans le rendu HTML ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), fichiers temporaires de cookies verrouillés, CI durcie contre les attaques de chaîne d'approvisionnement avec OpenSSF Scorecard et attestation de provenance des builds ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), analyses Semgrep et OSV-Scanner plus un contrôle de revue des dépendances sur chaque PR ([@23241a6749](https://github.com/23241a6749)), un seuil plancher de couverture de tests instauré à 60 % puis relevé à 84 % ([@gourab5139014](https://github.com/gourab5139014)), et un audit de sécurité Hermes désormais sans aucune finding CRITICAL (#768).

### Une portée plus large

L'hébreu et les langues non latines ([@dudyme](https://github.com/dudyme)). Une tokenisation adaptée au CJK pour les sources chinoises ([@An-idd](https://github.com/An-idd)). Une vague d'améliorations sur Windows. L'extraction des cookies sur toute la famille Chromium — Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) — plus le trousseau macOS et pass(1) sous Linux comme sources d'identifiants. Le retour en arrière historique avec `--as-of` ([@chiyi-creator](https://github.com/chiyi-creator)). L'installation automatique de Python 3.12 via uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` pour lire les pages emploi d'une entreprise. Les écarts de watchlist d'une exécution à l'autre.

### Toujours livré depuis la v3

Les fondations de la v3 sont toujours là : le cerveau de pré-recherche qui identifie les bons comptes, subreddits et hashtags avant le moindre appel API (construit par [@j-sperling](https://github.com/j-sperling)) ; le scoring Best Takes, qui prend en compte l'humour et la viralité en plus de la pertinence ; la fusion de clusters entre sources ; les comparaisons en une seule passe (« CLI vs MCP » en 3 minutes, pas 12) ; les comparaisons `--competitors` découvertes automatiquement ; le mode personne de GitHub (`--github-user=steipete`) ; le mode ELI5 (« eli5 on » après n'importe quelle exécution) ; et des briefs HTML autonomes et partageables (`--emit=html`). Les options de configuration sont détaillées dans [CONFIGURATION.md](CONFIGURATION.md).

## Installation

| Environnement | Installation | Mises à jour |
|---------|---------|---------|
| **Claude Code** (recommandé) | `/plugin marketplace add mvanhorn/last30days-skill` | Automatiques via la marketplace, ou `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` puis `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI, ou l'un des 50+ hôtes [Agent Skills](https://agentskills.io)** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [Téléchargez `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) et envoyez-le via claude.ai > Customize > Skills > + > Create skill > Upload a skill | Retélécharger et renvoyer |
| **Claude Desktop** | [Téléchargez le `.mcpb` de votre plateforme](https://github.com/mvanhorn/last30days-skill/releases/latest) et glissez-le dans Settings > Extensions | Retélécharger et glisser le nouveau bundle |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recommandé)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Recommandé parce que la marketplace Claude Code gère les mises à jour pour vous : le cache du plugin est versionné et se rafraîchit automatiquement à chaque nouvelle version publiée. Lancez `claude plugin update last30days@last30days-skill` pour forcer une vérification.

Si vous préférez passer par le chemin d'installation Agent Skills sur Claude Code, c'est également pris en charge :

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

Le plugin natif et l'installation `npx skills` peuvent coexister. Attention : Claude Code ne déduplique pas entre méthodes d'installation. Si le plugin de la marketplace et la copie `npx skills` sont actifs tous les deux, `/last30days` apparaîtra en double. Utilisez une seule méthode d'installation par machine.

### Grok (xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) installe last30days comme plugin natif. L'installation directe suit le dépôt :

```bash
grok plugin install mvanhorn/last30days-skill
```

Ou ajoutez ce dépôt comme source de marketplace, puis installez par nom de plugin :

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Ajoutez `--trust` pour sauter la confirmation d'installation. Mettez à jour avec `grok plugin update last30days`. Grok lit aussi les manifestes Claude Code par compatibilité ; la paire native `.grok-plugin/` reste la voie principale, et c'est elle que pointe une entrée officielle dans la [marketplace xAI](https://github.com/xai-org/plugin-marketplace). `npx skills add` reste une solution de repli valable, tous hôtes confondus.

### Codex, Cursor, Copilot, Gemini CLI et autres hôtes Agent Skills

Installez via la CLI ouverte [Agent Skills](https://agentskills.io) — elle prend en charge 50+ hôtes, dont `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose` et d'autres (liste complète sur le [dépôt vercel-labs/skills](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

Le flag `-g` (global) installe dans votre répertoire utilisateur, ce qui rend la skill disponible dans tous vos projets. Sans `-g`, `npx skills` installe localement dans `./.skills/` (versionné avec le dépôt). Pour un outil qui sert à explorer le monde entier, c'est bien l'installation globale que vous voulez.

Codex desktop et les autres hôtes qui travaillent au niveau du dossier fonctionnent aussi bien dans un dossier ordinaire que dans un dépôt Git. Avant la première recherche, demandez à l'agent hôte de lancer le `scripts/last30days.py --preflight` fourni depuis le répertoire de la skill chargée ; dans un clone du dépôt source, la commande équivalente est `python3 skills/last30days/scripts/last30days.py --preflight`. Elle affiche l'origine de la configuration, le plan de lecture des cookies de navigateur, les fichiers qui seront écrits, les commandes optionnelles et la configuration projet ignorée — sans lire de cookies, sans écrire de fichier et sans lancer de recherche.

Par défaut, l'installation cible l'hôte que `npx skills` détecte. Pour en viser un en particulier (ou plusieurs) :

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Pour mettre à jour plus tard :

```bash
npx skills update last30days -g
```

Ou mettez à jour tout ce que vous avez installé globalement via `npx skills` :

```bash
npx skills update -g
```

Listez et désinstallez avec `npx skills list -g` et `npx skills remove last30days -g`.

### claude.ai (web)

1. [Téléchargez `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) depuis la dernière version publiée
2. Allez sur [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Cliquez sur le bouton `+` du panneau Skills, puis sur `Create skill` > `Upload a skill`, et déposez le fichier

Activez d'abord « Code execution and file creation » dans Capabilities — sans cela, les skills ne s'exécutent pas.

### Claude Desktop

Claude Desktop installe `/last30days` comme serveur MCP via un bundle `.mcpb` (un paquet Model Context Protocol en un clic).

1. Ouvrez la [dernière version publiée](https://github.com/mvanhorn/last30days-skill/releases/latest) et téléchargez le `.mcpb` correspondant à votre plateforme :
   - macOS Apple Silicon : `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel : `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64 : `last30days-pp-mcp-linux-amd64.mcpb`
2. Ouvrez Claude Desktop, allez dans Settings > Extensions et glissez-y le fichier.
3. Quand l'application vous les demande, collez les clés API des sources que vous voulez activer. Tous les champs sont facultatifs : si vous les ignorez tous, le moteur se rabat sur le mode web uniquement. Les clés sont stockées dans le trousseau de votre système.
4. Redémarrez Claude Desktop. Demandez à Claude de « faire des recherches sur Peter Steinberger », ou sur n'importe quel sujet, et il appellera l'outil `research`.

**Prérequis côté hôte :** Python 3.12+ dans le PATH. Le bundle embarque le code du moteur mais utilise votre interpréteur Python local. Installez-le depuis [python.org](https://www.python.org/downloads/) sous Windows ; macOS et la plupart des distributions Linux fournissent déjà une version compatible.

**Les clés ne sont pas partagées avec la skill Claude Code.** Claude Desktop et Claude Code maintiennent délibérément des stockages d'identifiants distincts. Si vous avez déjà configuré `~/.config/last30days/.env` pour la skill Claude Code, il faudra ressaisir les mêmes clés ici, une fois.

La prise en charge de Windows est reportée le temps de régler les points d'entrée par plateforme dans le manifeste ; le suivi se fait dans une issue dédiée.

### OpenClaw

```bash
clawhub install last30days-official
```

Pour les workflows d'action sur X/Twitter en dehors des recherches `/last30days` —
publier des tweets ou des réponses, exporter des abonnés, gérer les médias,
surveiller des comptes, organiser des tirages au sort — utilisez
[TweetClaw](https://github.com/Xquik-dev/tweetclaw), le plugin OpenClaw
complémentaire. TweetClaw est maintenu par Xquik-dev et n'est mentionné que comme
option complémentaire : ce n'est ni une dépendance ni une recommandation de last30days.

### Installation manuelle (développeurs)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

Le lien symbolique garde l'installation synchronisée avec votre copie de travail au fil de vos modifications — inutile de recopier quoi que ce soit. Pour `claude.ai`, construisez le fichier `.skill` depuis les sources : `bash skills/last30days/scripts/build-skill.sh` produit `dist/last30days.skill`.

Reddit (avec les commentaires), Hacker News, Polymarket et GitHub fonctionnent immédiatement. Zéro configuration. Lancez `/last30days` une fois : l'assistant de configuration débloque d'autres sources en 30 secondes, dont les CLI gratuites arXiv et Techmeme.

## Apportez vos propres clés

Ces plateformes n'ont aucune relation entre elles. X ignore ce que pense Reddit. YouTube ne voit pas TikTok. Mais vous pouvez apporter vos propres clés API et vos tokens de navigateur, et vous avez soudain accès à toutes en même temps.

| Sources | Ce qu'il vous faut | Coût |
|---------|---------------|------|
| Reddit (avec les commentaires) + HN + Polymarket + GitHub + StockTwits | Rien | Gratuit |
| arXiv + Techmeme | Des CLI gratuites, installées automatiquement à la configuration initiale | Gratuit |
| X / Twitter | Connectez-vous à x.com dans n'importe quel navigateur, ou définissez `XQUIK_API_KEY` / `XAI_API_KEY` | Les cookies de navigateur sont gratuits ; les clés dépendent du fournisseur |
| YouTube | `brew install yt-dlp` | Gratuit |
| Bluesky | Un mot de passe d'application depuis bsky.app | Gratuit |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + commentaires YouTube | Une clé ScrapeCreators | 10 000 appels gratuits, puis paiement à l'usage |
| Xiaohongshu (RED) | Faites tourner un plugin de navigateur x-mcp connecté ou un service `xiaohongshu-mcp`, puis activez la source avec `--search xhs` pour une exécution ou `INCLUDE_SOURCES=xiaohongshu` dans `.env` ; last30days teste automatiquement `http://localhost:18060` puis `http://host.docker.internal:18060`, ou utilisez `XIAOHONGSHU_API_BASE` pour une URL personnalisée | Aucune clé API last30days ; dépend de votre service local de session de navigateur |
| DripStack (newsletters financières premium) | Sur activation : `--search dripstack` pour une exécution, ou `INCLUDE_SOURCES=dripstack` dans `.env` | Aucune clé ; API de recherche publique et gratuite |
| Perplexity Sonar / Search API / Deep Research | Une clé Perplexity, ou une clé OpenRouter en repli pour Sonar | Paiement à l'usage |
| Recherche web | Une clé Brave Search | 2 000 requêtes gratuites par mois |

### Trousseau macOS (facultatif)

Sous macOS, vous pouvez stocker vos clés dans le trousseau système plutôt que dans un fichier `.env`. La skill les récupère automatiquement, comme source de plus faible priorité : en cas de conflit, les fichiers `.env` et les variables d'environnement du processus l'emportent toujours.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Les entrées sont enregistrées sous le nom de service `last30days-<KEY>` pour l'utilisateur courant. Sur les plateformes non Darwin, le chargeur ne fait rien : aucun changement de comportement pour les utilisateurs Linux et Windows.

Vous avez déjà des clés sous d'autres noms de service dans le trousseau ? Définissez la correspondance non secrète `LAST30DAYS_KEYCHAIN_ALIASES` décrite dans [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items), plutôt que de recopier vos secrets.

Voir [CONFIGURATION.md](CONFIGURATION.md) pour la matrice complète des clés par source, l'ordre de priorité des fournisseurs de raisonnement et celui des backends de recherche web.

## Configuration

Deux choses que vous voudrez sans doute savoir dès le premier jour :

**Où sont enregistrés les fichiers de recherche.** `LAST30DAYS_MEMORY_DIR` vaut par défaut `~/Documents/Last30Days/` (sous Windows : `C:\Users\<you>\Documents\Last30Days\`). Redéfinissez cette variable d'environnement dans votre shell pour pointer ailleurs, ou passez `--save-dir <path>` sur une exécution. Utilisez `--output <file>` quand vous voulez le résultat rendu à un chemin précis, dans le format choisi par `--emit`. Utilisez `--save-suffix=<name>` pour garder séparées plusieurs variantes d'un même sujet (par client, par exemple). Chaque exécution avec `--save-dir` produit `<slug>-raw[-suffix].md`. Lancez `python3 skills/last30days/scripts/last30days.py --preflight` pour vérifier les écritures prévues avant une recherche.

**Sortie structurée pour les agents et les workflows.** Demandez à `/last30days` du JSON exploitable par une machine pour obtenir le profil d'agent stable et versionné. Pour un usage direct du moteur en script ou en développement, lancez `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json` ; n'ajoutez `--json-profile=raw` que si vous avez besoin du dump interne non versionné de `Report`. Voir la [référence des champs de l'export JSON et la politique de versionnement](docs/reference/json-export.md).

**Découverte sans sujet imposé.** Demandez `/last30days what's trending in AI agents?` pour obtenir un brief de découverte classé, au lieu de rechercher un sujet que vous connaissez déjà. Sur un hôte agentique, cela déclenche le protocole en trois commandes arbitré par l'hôte (le modèle propose les sujets, écarte le bruit, note leur intérêt et rédige les angles éditoriaux). Pour un usage direct du moteur en script ou en cron, lancez `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (en une passe : noms de sujets déterministes, sans angles) ; ajoutez `--emit=json` pour le contrat de découverte versionné. La découverte est incompatible avec un sujet positionnel et avec `--drill`.

**Suivi des tendances d'une exécution à l'autre.** Le mode par défaut produit un instantané Markdown à chaque exécution. Pour accumuler les résultats dans le temps, ajoutez `--store` afin de les conserver dans une base SQLite, puis utilisez [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) pour les exécutions planifiées (avec envoi facultatif sur Slack ou via webhook à chaque nouveau résultat) et [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) pour des synthèses quotidiennes ou hebdomadaires. Le schéma de cadence complet est dans [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Une bibliothèque de recherche à laquelle s'abonner.** Demandez à `/last30days` de générer le flux de votre bibliothèque, ou utilisez directement `python3 skills/last30days/scripts/last30days.py library feed` pour vos scripts et vos développements. La commande transforme les briefs enregistrés en un `index.html`, un `feed.xml` Atom local et des pages de brief lisibles. N'ajoutez `--publish` que si vous voulez héberger l'index HTML et les pages de brief ; la publication est un choix explicite, et publique par défaut. Pour rendre le flux Atom réellement abonnable, hébergez le répertoire de sortie généré sur un hébergeur statique comme GitHub Pages.

**Cherchez dans tout ce que vous avez déjà recherché.** Demandez `/last30days search my library for MCP servers` ou `/last30days have I researched MCP servers before?`. Pour un usage direct du moteur, lancez `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. La recherche est hors ligne et déterministe : elle indexe au fil de l'eau les mêmes briefs enregistrés que le flux de bibliothèque, y fusionne les occurrences correspondantes conservées dans le store, et regroupe les résultats par sujet et par date. Les nouvelles exécutions affichent aussi une section compacte **From your library** (« depuis votre bibliothèque ») quand des recherches antérieures recoupent le sujet en cours ; définissez `LAST30DAYS_LIBRARY_CONTEXT=off` pour désactiver ce contexte passif.

Les scripts d'encapsulation par client, les subreddits de catégorie personnalisés et le canal bêta expérimental pour les personnalisations en cours sont également documentés dans [CONFIGURATION.md](CONFIGURATION.md).

## Vitrine : les flux de recherche de la communauté

Vous avez publié une veille IA récurrente, un suivi de marché ou une obsession merveilleusement pointue avec last30days ? Partagez l'URL de votre bibliothèque publique — ou l'URL Atom une fois `feed.xml` hébergé sur un hébergeur statique — dans [le fil vitrine de la communauté](https://github.com/mvanhorn/last30days-skill/issues/532). Les flux communautaires seront listés ici au fur et à mesure que leurs auteurs les proposeront ; en attendant, le fil sert de point de collecte.

## Comment ça marche

1. **Vous saisissez un sujet.** Une personne, une entreprise, un produit, une technologie, « X vs Y ». N'importe quoi.
2. **L'agent identifie qui compte.** Il trouve les comptes X (y compris ceux des fondateurs), les dépôts GitHub, les subreddits, les hashtags TikTok, les chaînes YouTube. Pour « Kanye West », il sait qu'il faut r/hiphopheads, @kanyewest et « bully review » sur YouTube. Pour « OpenClaw », il identifie openclaw/openclaw sur GitHub et récupère le nombre d'étoiles en direct.
3. **Toutes les sources interrogées en parallèle.** Expansion multi-requêtes. Résultats classés selon l'engagement, la pertinence et la fraîcheur.
4. **Une profondeur que personne d'autre n'a.** Les transcriptions YouTube complètes des vidéos de réaction. Les meilleurs commentaires Reddit avec leur nombre d'upvotes. Les légendes TikTok. Les cotes Polymarket. Pas seulement des titres et des liens.
5. **Une même histoire, fusionnée.** Le Wireless Festival annoncé sur Reddit, commenté sur X, avec le prix des billets sur TikTok : un seul cluster, pas trois entrées distinctes.
6. **Synthétisé en un seul brief.** Ancré dans des données précises. Sourcé. Classé selon ce avec quoi les gens interagissent vraiment. Pas « voilà ce que j'ai trouvé », mais « voilà ce qui compte ».
7. **Ensuite, la skill devient votre experte.** Après une seule exécution, votre session Claude sait tout ce que sait la communauté. Posez vos questions de suivi. Faites-lui écrire des prompts, rédiger des e-mails, planifier des voyages, concevoir des architectures — le tout ancré dans la réalité du moment.

## Ce que les gens en disent

> « J'ai trouvé une skill Claude Code qui fait des recherches sur n'importe quel sujet à travers Reddit, X, YouTube et HN sur les 30 derniers jours. Et elle écrit les prompts à votre place. Avant chaque contenu que j'écris, je faisais ces recherches à la main sur Reddit et X. Onglet par onglet. Fil par fil. C'est la partie qui prend 90 minutes. Elle disparaît. » — @itsjasonai

> « Cette seule skill a remplacé tout mon workflow de recherche. Vous lui donnez un sujet, elle récupère sur Reddit, X et le web ce dont les gens parlent vraiment. Pas de vieux billets de blog. De vraies conversations des 30 derniers jours. » — @itswilsoncharles

> « 5 des 10 dépôts tendance du jour sur GitHub sont des outils Claude. N° 1 : mvanhorn/last30days-skill » — @yieldhunter95

## Open source

Licence MIT. Aucun tracking. Aucune analytics. Vos recherches restent sur votre machine. Plus de 2 700 tests.

Construit avec Python 3.12+, yt-dlp, Node.js (client Bird intégré pour la recherche X) et l'API ScrapeCreators. Architecture du moteur v3 par [@j-sperling](https://github.com/j-sperling).

Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour ouvrir une PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) pour la liste complète des contributeurs de la communauté, et [CHANGELOG.md](CHANGELOG.md) pour l'historique des versions.

## Évolution des étoiles

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
