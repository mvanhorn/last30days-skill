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

**Un moteur de recherche dirigé par un agent IA, noté par des votes positifs, des likes et de l’argent réel - pas par des éditeurs.**

Ce README suit le pipeline v3 actuel. La spécification de compétence à l’exécution se trouve dans [skills/last30days/SKILL.md](skills/last30days/SKILL.md), qui est la source de vérité pour le comportement de commande et de configuration les plus récents.

**Claude Code (recommandé — mises à jour automatiques via le marketplace):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI, ou n’importe lequel des 50+ [Agent Skills](https://agentskills.io) hôtes :**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` s’installe globalement pour votre utilisateur, disponible sur tous les projets. Réduisez-le à la portée par projet.)

Plus d’options d’installation (claude.ai web, OpenClaw, manuel) dans la section [Install](#installation) ci-dessous.

Zéro configuration. Reddit, HN, Polymarketet GitHub fonctionnent immédiatement. Lancez-le une fois et l’assistant de configuration débloque X, YouTube, TikTok, arXiv, Techmemeet plus encore en 30 secondes.

---

Reddit votes positifs. X likes. YouTube transcriptions. TikTok engagement. Polymarket probabilités soutenues par de l’argent réel et des informations privilégiées. Cela fait des millions de personnes votant chaque jour avec leur attention et leur portefeuille. /last30days recherche tout cela en parallèle, évalue selon ce avec quoi les vraies personnes interagissent réellement, et un juge IA synthétise tout en un seul mémoire.

Google agréga des éditeurs. /last30days recherche des gens.

Vous ne pouvez pas trouver cette recherche ailleurs car aucune IA unique n’a accès à tout. Google recherche ne touche ni Reddit commentaires ni X publications. ChatGPT a un accord avec Reddit mais ne peut pas rechercher X ni TikTok. Gemini a YouTube mais pas Reddit. Claude n’en a aucun d’eux nativement. Chaque plateforme est un jardin clos avec ses propres API, ses propres jetons, sa propre authentification. Mais vous pouvez apporter vos propres clés et sessions de navigateur, et soudain un agent IA peut toutes les rechercher en même temps, les évaluer entre elles et vous dire ce qui compte réellement.

C’est ça le déblocage. Pas un meilleur moteur de recherche. Une douzaine de plateformes déconnectées, pontées par un agent.

```
/last30days Peter Steinberger
```

Vous avez une réunion demain. Vous les Google . Vous obtenez leur LinkedIn de 2023. /last30days vous donne ce qu’ils font réellement ce mois-ci : rejoindre OpenAI pour travailler sur Codex, combattre l’interdiction d’Anthropic sur les agents tiers, expédier 23 PRs à un taux de fusion de 85 %, construire des «LobsterOS» pour le contrôle multi-appareils des agents, et r/ClaudeCode a obtenu 569 votes positifs pour débattre de savoir s’il est un héros ou « insupportable ». Dispersé dans X posts, Reddit fils, YouTube transcriptions et GitHub commits. Rien de tout cela n’était sur Google.

## Pourquoi cela existe-t-il

Je l’ai construit pour suivre le rythme de l’IA. Tout change chaque jour et les Reddit et X nerds sont toujours à l’affût en premier. J’avais besoin de meilleurs indices, et les données d’entraînement avaient toujours des mois de retard sur ce que la communauté avait déjà compris.

Mais cela s’est transformé en quelque chose de plus important. Maintenant, je le passe avant un appel commercial pour connaître la vérité des 30 derniers jours sur une entreprise. Avant une réunion pour lire les tweets récents et les transcriptions de podcasts de quelqu’un. Avant un Disney World voyage pour savoir quelles attractions sont fermées et ce que la communauté dit à propos de Genie+. Avant de construire quoi que ce soit pour savoir quels problèmes les gens rencontrent réellement.

Si vous rencontrez un PDG, avez-vous lu tous ses tweets et YouTube transcriptions des 30 derniers jours ? Oui.

## Sources, notées par le peuple

| Source | Ce que les gens te disent |
|--------|--------------------------|
| **Reddit** | L’avis non filtré. Meilleurs commentaires avec de vrais votes positifs, gratuits, sans API clé. Les vraies opinions que Google enterre. |
| **X / Twitter** | L’opinion controversée, le fil narratif de l’expert, la réaction qui s’est brisée. Premier à savoir, premier à argumenter. |
| **YouTube** | L’analyse approfondie de 45 minutes. Les transcriptions complètes ont été recherchées pour les 5 phrases citables qui comptent. |
| **TikTok** | Le créateur atteint 3,6 millions de personnes avec une vision que vous ne trouverez jamais sur Google. |
| **Instagram Reels** | La perspective de l’influenceur avec des transcriptions de spoken word. Le signal visuel de la culture. |
| **Hacker News** | Le consensus des développeurs. 825 points, 899 commentaires. Là où les techniciens argumentent réellement. |
| **Polymarket** | Pas des opinions. Des cotes. Soutenu par de l’argent réel. 96 % de confiance sur les ventes d’album. 4 % sur une acquisition. |
| **GitHub** | Pour les gens : PR Velocity, Top Repos par étoiles, notes de release. Pour les sujets : problèmes et discussions. |
| **Digg** | Clusters d’histoires sélectionnés à partir du classement AI 1000 de Digg(~1000 comptes IA high-signal sur X), avec des citations en ligne attribuables (sans authentification X requise). Autoactivé lorsque `digg-pp-cli` est activé PATH. |
| **arXiv** | Les papiers derrière tout ce battage médiatique. Nouvelle recherche dans la fenêtre, gratuite, sans API de touche. Auto-activé quand `arxiv-pp-cli` est en PATH (la première configuration l’installe). |
| **Techmeme** | La couche éditoriale tech-news, avec une fenêtre de date à 30 jours. Gratuit, sans clé API . Activé automatiquement quand `techmeme-pp-cli` est en PATH (la première configuration l’installe). |
| **LinkedIn** | Le signal professionnel. Publications et articles, avec des éléments pondérés en haut du signal. |
| **StockTwits** | Le sentiment du trader. S’active automatiquement lorsque votre sujet est un ticker ou une crypto. |
| **Threads** | La couche texte post-Twitter. Conversations de créateurs et de marques. |
| **Pinterest** | Découverte visuelle. Épinglez, sauvegardez et commente des produits et des idées. |
| **Xiaohongshu (RED)** | Signaux de mode de vie, produit et créateur chinois. Demandé explicitement avec `--search xhs` lorsqu’un plugin ou un service `xiaohongshu-mcp` navigateur x-mcp connecté fonctionne localement. |
| **Bluesky** | La couche sociale décentralisée. Les publications du protocole AT issues de la migration post-Twitter. |
| **Perplexity** | Synthèse sonar au sol, lignes de API de recherche brute, et recherche profonde. |
| **Web** | La couverture éditoriale, les comparaisons sur les blogs. Un signal parmi tant d’autres, pas le seul. |

Les contributeurs de la communauté n’arrêtent pas d’en ajouter. Truth Social et d’autres sources de niche sont en cours avec d’autres en préparation.

Un fil de discussion Reddit avec 1 500 upvotes est un signal plus fort qu’un article de blog que personne n’a lu. Un TikTok avec 3,6 millions de vues en dit plus sur ce qui est culturellement pertinent qu’un communiqué de presse. Polymarket cotes soutenues par 66 000 $ de volume sont plus difficiles à contester qu’une supposition d’un commentateur.

La synthèse se classe selon ce avec quoi les vraies personnes interagissent réellement. La pertinence sociale, pas SEO pertinence.

## À quoi servent réellement les gens

**Avant une réunion.** `/last30days Peter Steinberger` - a rejoint l’équipe Codex de OpenAI, luttant contre l’interdiction d’Anthropic sur les agents tiers, 23 PRs fusionnés avec un taux de fusion de 85 % sur GitHub, construisant LobsterOS pour le contrôle inter-appareils des agents. r/ClaudeCode : « Depuis la sortie de OpenClaw, il était largement connu que si vous le passiez par autre chose que le API, vous finiriez par être banni » (227 votes positifs). Ce n’est pas la faute de LinkedIn.

**Pour lire les signaux d’embauche.** `/last30days Listen Labs --hiring-signals` - les pages actuelles d’emplois et de carrières deviennent des preuves citées de changements de focus : recrutement vers la sécurité d’entreprise, la réussite client, l’infrastructure ou l’expansion produit. Le rapport indique ce que le recrutement semble signifier, pas ce que la feuille de route présentera.

**Pour trouver le sujet avant qu’il ne soit au sommet.** Demandez `/last30days what's exploding in AI agents?` et la compétence passe en mode découverte : le moteur balaie Reddit listes de catégories, Hacker News les articles de première ligne/les meilleures histoires, le fil AI 1000 de Digg, et X lorsqu’authentifié ; votre agent juge les nominations (noms, filtrage des indésirables, qualité du contenu) et écrit des angles d’article pour podcast / X; puis vous obtenez 5 à 10 sujets classés en vélocité. Chaque résultat inclut des chiffres cross-source, une étiquette de momentum, et un `/last30days "<topic>"` suivi prêt à être publié.

**Quand quelque chose tombe.** `/last30days Kanye West` - Le Royaume-Uni a bloqué son visa, le Wireless Festival annulé, les sponsors ont fui. Mais BULLY a débuté #2 sur Billboard. Fantano est revenu de son « Yay sabbatique » pour le critiquer (653 000 vues). SoFi Homecoming a fait venir Lauryn Hill et Travis Scott pour 44 chansons. Polymarket: « Kanye tweetera-t-il encore ? » 86 % Oui. 23 fils Reddit , 17 vidéos YouTube , 86 000 votes positifs.

**Pour comparer les outils.** `/last30days OpenClaw vs Hermes vs Paperclip` - « Ce ne sont pas des concurrents, ce sont des couches. » OpenClaw est l’exécuteur (351K GitHub étoiles, en direct), Hermès est le cerveau qui s’améliore lui-même (31K étoiles), Paperclip est l’organigramme (49K étoiles). Comptage d’étoiles extrait en direct depuis le GitHub API, pas des articles de blog obsolètes. Table côte à côte avec architecture, mémoire, sécurité, meilleur pour. Par @IMJustinBrooke: «OpenClaw = Salamèche, Hermès = Dracaufeu. »

**Pour comprendre le monde.** `/last30days Iran vs USA` - 38e jour de la guerre. La date limite de Trump mardi pour la réouverture du détroit d’Ormuz par l’Iran. Deux avions de guerre américains abattus. Le pétrole à 126 $ le baril. L’AIE a qualifié cela de « plus grande perturbation de l’approvisionnement de l’histoire du marché mondial du pétrole ». Polymarket: cessez-le-feu d’ici le 31 décembre à 74 %. 27 X posts, 10 vidéos YouTube , 20 marchés de prévision.

**Avant un voyage.** `/last30days Universal Epic Universe` - Extension déjà en construction. Permis « Projet 680 » déposé. Feu d’artifice confirmé par l’infrastructure mais sans prévenir. Temps d’attente : Mine Wagon Madness en moyenne 148 minutes. Pas encore de pass annuel, et les habitants sont frustrés. Stardust Racers en rénovation jusqu’au 5 avril.

**Pour apprendre quelque chose rapidement.** `/last30days Nano Banana Pro prompting` - JSONles prompts structurés remplacent la soupe de tags. Le format imbriqué de @pictsbyaiempêche le « concept saccade ». Le workflow d’édition d’abord vaut la régénération. Ensuite, il vous écrit une invite de production en utilisant exactement ce que la communauté a dit fonctionner.

## Quoi de neuf

Depuis l’annonce de la v3.3 en mai, à partir de la v3.11.1 (juillet 2026), 175 ont fusionné PRs - 122 d’entre eux issus de 52 contributeurs de la communauté - répartis sur 15 versions. C’est ce qui a été adopté.

### Première classe sur OpenAI Codex

/last30days est désormais un plugin Codex natif avec configuration guidée – pas un port, un citoyen de première classe. Les citations conscientes du rendu signifient que Codex sortie se lit comme un brief plutôt qu’un soup URL (#694), et le même moteur fonctionne sur Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClawet 50+ hôtes Agent Skills . Codex manifeste du plugin par [@rfoust](https://github.com/rfoust) (#686), Codex correction d’authentification par [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmemeet Digg - gratuit, sans API clés

arXiv apporte les journaux derrière le battage médiatique et Techmeme apporte la couche éditoriale-actualité technique - gratuit, zéro clé, et la configuration en première exécution installe leurs CLIpour qu’ils s’activent automatiquement (#709). Les clusters d’histoires IA 1000 de Diggarrivent sans authentification X de la même manière – setup installe le Digg CLI gratuit pour vous (#590). Trustpilot envoient l’option pour la recherche sur la marque grand public.

### Les Reddit gratuits ont fait fructifier de vrais scores et ont fait grimper les commentaires

Redditpublic .json API est mort ; le chemin gratuit est revenu plus fort. RSS sans clé + scraping shreddit (#457), découverte de subreddit dédié avec de vrais décomptes de votes via arctic-shift (#696), et un plancher de pertinence pour qu’un post viral hors sujet ne puisse pas détourner votre brief (#488, merci [@rzachsmith](https://github.com/rzachsmith)). Pas de clé API . Scores réels. Commentaires principaux inclus.

### Les meilleurs commentaires dans chaque mémoire

Les commentaires sont désormais une couche par défaut entre les sources : les commentaires Instagram avec une diversité basée sur le classement, donc cinq opinions brûlantes ne proviennent pas toutes d’un même post (#751), YouTube commentaires plus une sauvegarde de ScrapeCreators transcription pour quand yt-dlp est éliminé (#637), et les commentaires votés par le public mis en Best Takes pour que les répliques les plus drôles de la communauté survivent au score (#592, #608).

### Commande un médecin

Demandez un contrôle de santé et le médecin analyse toutes les sources, puis prescrit des solutions exactes - quelle clé manque, laquelle CLI est décalée PATH, quel cookie a expiré (#753). Plus de devinettes sur les raisons X sont revenues faibles.

### X recherche, reconstruite

Le pipeline X a bénéficié d’une refonte complète : les voies FROM et ABOUT pour que les posts d’une personne et la conversation à leur sujet soient tous deux classés (#610), désambiguïsation des sous-requêtes conscientes (#611), mise à la terre de l’auteur de première partie avec classement du signal d’interaction (#613), et une source X unique avec basculement automatique backend (#622). En plus d’un `--diagnose` honnête qui sonde réellement l’authentification (#609).

### D’autres sources ont rejoint

LinkedIn via ScrapeCreators, avec des articles comme signal élevé ([@ravstr](https://github.com/ravstr), #702). StockTwits s’active automatiquement pour les thèmes ticker et crypto ([@wtiwana](https://github.com/wtiwana), #658). Perplexity développé en modes API directs et asynchrone Deep Research ([@sk-holmes](https://github.com/sk-holmes), #629).

### Endurci par la communauté

La vague de sécurité consistait presque entièrement en travail communautaire : correctifs stocked-XSS dans le moteur de rendu HTML ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), fichiers temporaires de cookies verrouillés, CI renforcé par la chaîne d’approvisionnement avec OpenSSF Scorecard et attestation de provenance de compilation ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), scans Semgrep et OSV-Scanner plus une porte de contrôle de dépendance PR ([@23241a6749](https://github.com/23241a6749)), un plancher de couverture de test introduit à 60 % et depuis porté à 84 % ([@gourab5139014](https://github.com/gourab5139014)), et un scan de sécurité Hermes effacé de toutes les conclusions CRITIQUES (#768).

### Va plus loin

Hébreu et langues non latines ([@dudyme](https://github.com/dudyme)). Tokenisation consciente CJKpour les sources chinoises ([@An-idd](https://github.com/An-idd)). Une vague de compatibilité Windows . Extraction des cookies à travers toute la famille Chromium - Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) - plus macOS Keychain et Linux pass(1) sources de certification. `--as-of` rétrospection historique ([@chiyi-creator](https://github.com/chiyi-creator)). Provisionnement automatique Python 3.12 via uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` pour lire les pages d’emplois d’une entreprise. Deltas de la liste de surveillance entre les exécutions.

### Toujours dans la boîte depuis la v3

Les fondations de la v3 sont toujours là : le cerveau pré-recherche qui résout les bons pseudos, subreddits et hashtags avant qu’un seul appel de API ne se déclenche (construit par [@j-sperling](https://github.com/j-sperling)) ; Best Takes la notation pour l’humour et la viralité en plus de la pertinence ; la fusion de clusters multisources ; les comparaisons en un seul passage («CLI vs MCP» en 3 minutes, pas 12) ; la découverte automatique `--competitors` comparaisons ; GitHub mode personne (`--github-user=steipete`) ; ELI5 mode (« eli5 on » après chaque partie ; et des briefs HTML partageables et autonomes (`--emit=html`). Les boutons de configuration sont présents dans [CONFIGURATION.md](CONFIGURATION.md).

## Installation

| Surface | Installation | Mises à jour |
|---------|---------|---------|
| **Claude Code**(recommandé) | `/plugin marketplace add mvanhorn/last30days-skill` | Auto via le marché, ou `claude plugin update last30days@last30days-skill` |
| **Grok**(Build xAI CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` alors `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI, ou n’importe lequel des 50+ [Agent Skills](https://agentskills.io) hôtes** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai**(toile) | [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) et téléverser via claude.ai > Personnaliser > compétences > + > Créer une compétence > Télécharger une compétence | Téléchargez et ré-téléchargez |
| **Claude Desktop** | [Download the `.mcpb` for your platform](https://github.com/mvanhorn/last30days-skill/releases/latest) et glisser dans Paramètres > Extensions | Retéléchargez et faites glisser le nouveau bundle |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recommandé)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Recommandé car le marché Claude Code gère les mises à jour pour vous — le cache des plugins est versionné et se rafraîchit automatiquement lorsqu’une nouvelle version est publiée. Lancez `claude plugin update last30days@last30days-skill` pour forcer une vérification.

Si vous préférez utiliser le chemin d’installation des compétences agent-skills sur Claude Code, c’est aussi pris en charge :

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

Le plugin natif et l’installation `npx skills` peuvent coexister. Notez que Claude Code ne déduppe pas entre les méthodes d’installation : si vous avez activé à la fois le plugin marketplace et la copie `npx skills` , `/last30days` affichera deux entrées. Utilisez une méthode d’installation par machine.

### Grok ( CLIde construction xAI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) s’installe en dur30days en tant que plugin natif. L’installation directe suit le dépôt :

```bash
grok plugin install mvanhorn/last30days-skill
```

Ou ajoutez ce dépôt comme source marketplace, puis installez par nom de plugin :

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Ajouter `--trust` pour sauter la confirmation d’installation. Mettre à jour avec `grok plugin update last30days`. Grok lit aussi les manifestes Claude Code pour la compatibilité ; la paire native `.grok-plugin/` est la voie de première classe (et ce à quoi indique une liste officielle [xAI marketplace](https://github.com/xai-org/plugin-marketplace) ). `npx skills add` reste une solution de secours valide entre hôtes.

### Codex, Cursor, Copilot, Gemini CLI, et autres hôtes Agent Skills

Installation via l’open [Agent Skills](https://agentskills.io) CLI — prend en charge 50+ harnais incluant `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose`et plus encore (liste complète sur le [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

Le drapeau `-g` (global) s’installe dans votre répertoire utilisateur, donc la compétence est disponible sur tous les projets. Sans `-g`, `npx skills` installe localement dans `./.skills/` (engagé avec le dépôt). Pour un outil de recherche du monde, global, c’est ce qu’il vous faut.

Codex hôtes de bureau et autres en mode dossier peuvent fonctionner dans des dossiers ordinaires ainsi que dans des dépôts Git. Avant de faire une première recherche, demandez à l’agent hôte d’exécuter les `scripts/last30days.py --preflight` fournis depuis le répertoire skill chargé ; lors d’une vérification de source, la commande équivalente est `python3 skills/last30days/scripts/last30days.py --preflight`. Elle affiche la source de configuration, le plan de cookies-navigateur, les écritures planifiées, les commandes optionnelles et la configuration du projet ignorée sans lire les cookies, écrire des fichiers ou lancer de recherche.

Par défaut, cela s’installe pour le faisceau `npx skills` détecte. Pour cibler un ou plusieurs :

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Mise à jour plus tard avec :

```bash
npx skills update last30days -g
```

Ou mettez à jour tout ce que vous avez installé globalement via `npx skills`:

```bash
npx skills update -g
```

Listez et supprimez avec `npx skills list -g` et `npx skills remove last30days -g`.

### claude.ai (web)

1. [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) de la dernière sortie
2. Va à [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Cliquez sur le bouton `+` dans le panneau Compétences > cliquez sur `Create skill` > `Upload a skill` et parcourez/déposez le fichier

Activez d’abord « Exécution de code et création de fichiers » sous Capacités — les compétences ne s’exécuteront pas sans cela.

### Claude Desktop

Claude Desktop installe `/last30days` en tant que serveur MCP via un bundle `.mcpb` (un package Model Context Protocol en un clic).

1. Allez sur le [latest release](https://github.com/mvanhorn/last30days-skill/releases/latest) et téléchargez le `.mcpb` pour votre plateforme :
   - macOS Apple Silicon : `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel : `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64 : `last30days-pp-mcp-linux-amd64.mcpb`
2. Ouvre Claude Desktop, va dans Paramètres > Extensions, et fais glisser le fichier dedans.
3. Lorsqu’on vous le demande, collez API clés pour les sources que vous souhaitez activer. Chaque champ est optionnel — le moteur passe au mode web uniquement si vous les sautez tous. Les clés sont stockées dans votre trousseau d’OS.
4. Redémarrez Claude Desktop. Demandez- Claude de « rechercher Peter Steinberger » ou n’importe quel sujet, et cela appellera l’outil `research` .

**Exigence de l’hôte :** Python 3.12+ sur PATH. Le bundle envoie la source moteur mais utilise votre interprète Python local. Installez depuis [python.org](https://www.python.org/downloads/) sur Windows; macOS et la plupart des distributions Linux livrent une version compatible.

**Les clés ne se synchronisent pas avec la compétence Code.** Claude Desktop et Claude Code maintiennent des magasins d’identifiants séparés par conception. Si vous avez déjà configuré `~/.config/last30days/.env` pour la compétence Code, vous saisirez à nouveau les mêmes clés ici une fois.

Windows support est différé jusqu’à ce que les points d’entrée des manifestes par plateforme soient réglés ; suivre un problème de suivi.

### OpenClaw

```bash
clawhub install last30days-official
```

Pour Xflux d’action /Twitter en dehors de la recherche `/last30days` , comme la publication
tweets ou réponses, exportation d’abonnés, gestion des médias, surveillance et concours
pioche, utilise [TweetClaw](https://github.com/Xquik-dev/tweetclaw) comme compagnon
OpenClaw plugin. TweetClaw est maintenu par Xquik-dev et n’est listé que comme un
Chemin de compagnon optionnel, pas une dépendance ou une endosse de Last30Days.

### Manuel (développeur)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

Le lien sym maintient l’installation synchronisée avec votre arbre de travail pendant la modification — pas besoin de recopier. Pour `claude.ai`, construisez le fichier `.skill` à partir de la source : `bash skills/last30days/scripts/build-skill.sh` produit `dist/last30days.skill`.

Reddit (avec commentaires), Hacker News, Polymarketet GitHub fonctionnent immédiatement. Zéro configuration. Lancez- `/last30days` une fois et l’assistant de configuration débloque plus de sources en 30 secondes, y compris les arXiv gratuits et les Techmeme CLIs.

## Apportez vos propres clés

Ces plateformes n’ont pas de relations entre elles. X ne sait pas ce que Reddit pense. YouTube ne voit pas TikTok. Mais vous pouvez apporter vos propres clés de API et jetons navigateur, et soudainement vous avez accès à tout en même temps.

| Sources | Ce dont tu as besoin | Coût |
|---------|---------------|------|
| Reddit (avec commentaires) + HN + Polymarket + GitHub + StockTwits | Rien | Gratuit |
| arXiv + Techmeme | Free CLIs, installé automatiquement par la première exécution | Gratuit |
| X / Twitter | Connectez-vous à x.com dans n’importe quel navigateur, ou configurez `XQUIK_API_KEY` / `XAI_API_KEY` | Les cookies du navigateur sont gratuits ; les clés sont spécifiques à chaque fournisseur |
| YouTube | `brew install yt-dlp` | Gratuit |
| Bluesky | Mot de passe de l’application depuis bsky.app | Gratuit |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube commentaires | ScrapeCreators clé | 10 000 appels gratuits, puis PAYG |
| Xiaohongshu (RED) | Exécutez un plugin de navigateur x-mcp connecté ou un service `xiaohongshu-mcp` et optez pour `--search xhs` par exécution ou `INCLUDE_SOURCES=xiaohongshu` dans `.env`; last30days s’auto-sonde `http://localhost:18060` puis `http://host.docker.internal:18060`, ou utilisez `XIAOHONGSHU_API_BASE` pour une URL personnalisée | Pas de clé de API des derniers 30 jours ; cela dépend de votre service local de session de navigateur |
| DripStack (newsletters financières premium) | Opt-in : `--search dripstack` par partie, ou `INCLUDE_SOURCES=dripstack` en `.env` | Pas de clé ; recherche publique gratuite API |
| Perplexity Sonar / API de recherche / Recherche approfondie | Perplexity clé, ou clé OpenRouter comme solution de secours Sonar | Payez au fur et à mesure |
| Web recherche | Clé de recherche Brave | 2 000 requêtes gratuites par mois |

### macOS Keychain (optionnel)

Sur macOS , vous pouvez stocker les clés dans le Keychain système au lieu d’un fichier `.env` . La compétence les détecte automatiquement comme source de priorité la plus basse — `.env` fichiers et environnement de processus l’emportent toujours en cas de collision.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Les éléments sont stockés sous le nom de service `last30days-<KEY>` pour l’utilisateur actuel. Sur les plateformes non-Darwin, le chargeur est un no-op, donc il n’y a pas de changement de comportement pour les utilisateurs Linux/Windows .

Avez-vous déjà des clés sous différents noms de service Keychain ? Définissez la `LAST30DAYS_KEYCHAIN_ALIASES` non secrète décrite dans [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) au lieu de copier les secrets.

Voir [CONFIGURATION.md](CONFIGURATION.md) pour la matrice complète de clés par source, la priorité du fournisseur de raisonnement et la priorité backend de recherche web.

## Configuration

Deux choses que vous voudrez probablement savoir dès le premier jour :

**Où les fichiers de recherche sont sauvegardés.** `LAST30DAYS_MEMORY_DIR` par défaut est `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Remplaça en définissant cette variable d’environnement sur n’importe quel chemin dans ton shell, ou `--save-dir <path>` par exécution. Utilise `--output <file>` lorsque tu as besoin que le résultat affiché soit sur un chemin exact, en utilisant le format choisi par `--emit`. Utilise `--save-suffix=<name>` pour séparer plusieurs variantes du même sujet (par exemple par client). Chaque exécution `--save-dir` produit `<slug>-raw[-suffix].md`. Exécute `python3 skills/last30days/scripts/last30days.py --preflight` pour revoir les écritures prévues avant une exécution de recherche.

**Sortie structurée pour les agents et les flux de travail.** Demandez `/last30days` JSON lisibles par machine pour recevoir le profil d’agent stable et versionné. Pour une utilisation directe du moteur dans les scripts ou le développement, exécutez `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; ajoutez `--json-profile=raw` uniquement lorsque vous avez besoin du vidage interne de `Report` non versionné. Voir le [JSON export field reference and versioning policy](docs/reference/json-export.md).

**Découverte sans sujet.** Demandez- `/last30days what's trending in AI agents?` d’obtenir un brief de découverte classé au lieu de rechercher un sujet déjà connu – sur un agent hôte, cela exécute le protocole à trois commandes hôte-jugé (le modèle nomme les sujets, filtre les indésirables, note la valeur et écrit les angles de contenu). Pour une utilisation directe du moteur dans des scripts ou des crons, exécutez `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (one-shot : noms de sujets déterministes, sans angles) ; ajoutez `--emit=json` pour le contrat de découverte versionné. La découverte est mutuellement exclusive avec un sujet positionnel et `--drill`.

**Surveillance des tendances entre les exécutions.** Le mode par défaut produit un instantané de remarques fraîchement par exécution. Pour accumuler les résultats au fil du temps, ajoutez `--store` pour persister dans une base SQLite, puis utilisez [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) pour les exécutions planifiées (avec une livraison optionnelle par Slack / webhook sur les nouvelles découvertes) et [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) pour les digestes quotidiens / hebdomadaires. Le schéma de cadence complet est dans [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Une bibliothèque de recherche abonnée.** Demandez- `/last30days` de construire votre fil d’actualité de bibliothèque, ou utilisez- `python3 skills/last30days/scripts/last30days.py library feed` directement pour le script et le développement. Cela transforme les briefs sauvegardés en `index.html`, un `feed.xml`Atom local et des pages brèves lisibles. Ajoutez `--publish` uniquement lorsque vous souhaitez que l’index HTML et les pages de briefs soient hébergés ; la publication est explicitement volontaire et publique par défaut. Pour rendre le flux Atom abonné, hébergez le répertoire de sortie généré sur un hôte statique tel que GitHub Pages.

**Recherchez tout ce que vous avez recherché.** Demandez `/last30days search my library for MCP servers` ou `/last30days have I researched MCP servers before?`. Pour une utilisation directe du moteur, exécutez `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. La recherche est hors ligne et déterministe : elle indexe progressivement les mêmes briefs sauvegardés utilisés par le fil de la bibliothèque, fusionne les observations correspondantes à chaque exécution, et regroupe les résultats par sujet et date. Les nouvelles sessions font également apparaître une section compacte **Depuis votre bibliothèque** lorsque des recherches antérieures chevauchent le sujet actuel ; réglez `LAST30DAYS_LIBRARY_CONTEXT=off` pour désactiver ce contexte passif.

Des scripts wrapper par client, des subreddits personnalisés par catégories peer et le canal bêta expérimental pour les personnalisations en cours sont également documentés dans [CONFIGURATION.md](CONFIGURATION.md).

## Vitrine : flux de recherche communautaires

Publié une mise à jour récurrente de l’IA, une surveillance du marché, ou une obsession merveilleusement étroite pour les dernier30 jours ? Partagez l’URL de la bibliothèque publique — ou l’URL Atom après `feed.xml` hébergé sur un hébergeur statique — dans [the community showcase thread](https://github.com/mvanhorn/last30days-skill/issues/532). Les fils communautaires seront liés ici au fur et à mesure que leurs propriétaires les soumettent ; le fil de discussion sert de point de collecte en attendant.

## Comment ça fonctionne

1. **Tu tapes un sujet.** Personne, entreprise, produit, technologie, «X vs Y. » N’importe quoi.
2. **L’agent décide qui compte.** Trouve X pseudos (y compris les fondateurs), GitHub dépôts, subreddits, hashtags TikTok YouTube chaînes. Pour « Kanye West », il connaît r/hiphopheads, @kanyewest, et « bully review » sur YouTube. Pour «OpenClaw», il résout openclaw/openclaw sur GitHub et récupère les comptes d’étoiles en direct.
3. **Toutes les sources recherchées en parallèle.** Extension multi-requêtes. Résultats notés par engagement, pertinence, fraîcheur.
4. **La profondeur que personne d’autre n’a.** Transcriptions complètes YouTube des vidéos de réactions. Meilleurs Reddit commentaires avec le nombre de votes positifs. TikTok légendes. Polymarket chances. Pas seulement les titres et les liens.
5. **Même histoire, fusionnée.** Wireless Festival annoncé le Reddit, discuté sur X, prix des billets sur TikTok = un cluster, pas trois articles séparés.
6. **Synthétisé en un seul mémoire.** Fondé sur des données spécifiques. Cité par source. Classée selon ce avec quoi les gens interagissent réellement. Pas « voici ce que j’ai trouvé ». C’est « voici ce qui compte ».
7. **Puis il devient votre expert.** Après une seule partie, votre Claude session sait tout ce que la communauté sait. Posez des questions de suivi. Faites-lui écrire des suggestions, rédiger des e-mails, planifier des voyages, créer des systèmes d’architecture – tout cela ancré dans ce qui est réel en ce moment.

## Ce que les gens disent

> « J’ai trouvé une compétence Claude Code qui recherche n’importe quel sujet à travers Reddit, X, YouTubeet HN des 30 derniers jours. Ensuite, il écrit les consignes pour vous. J’ai cherché manuellement Reddit et X recherches avant chaque contenu que j’écris. Onglet par onglet. Fil par fil. C’est la partie qui prend 90 minutes. Ça l’élimine. » -@itsjasonai

> « Cette compétence a remplacé tout mon flux de recherche. Tu lui donnes un sujet, ça gratte Reddit, X, et le web pour trouver ce dont les gens parlent vraiment. Pas de vieux articles de blog. De vraies conversations des 30 derniers jours. » -@itswilsoncharles

> « 5 des 10 dépôts tendance sur GitHub aujourd’hui sont Claude outils. #1 : Mvanhorn/last30days- compétence » -@yieldhunter95

## Open source

Licence MIT. Pas de suivi. Pas d’analyses. Votre recherche reste sur votre machine. 2 700+ tests.

Construit avec Python architecture du moteur v3.12+, yt-dlp, Node.js (client Bird fourni pour X recherche), et ScrapeCreators API. Architecture du moteur v3 par [@j-sperling](https://github.com/j-sperling).

Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour ouvrir un PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) pour la liste complète des contributeurs de la communauté, et [CHANGELOG.md](CHANGELOG.md) pour l’historique des versions.

## Histoire de Star

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
