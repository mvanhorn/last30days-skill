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

**Un moteur de recherche dirigé par un agent IA et évalué par les votes positifs, les likes et l'argent réel - et non par les éditeurs.**

Ce README suit le pipeline v3 actuel. La spécification de compétence d'exécution se trouve dans [skills/last30days/SKILL.md](skills/last30days/SKILL.md), qui est la source de vérité pour le dernier comportement de commande et de configuration.

**Claude Code (recommandé — mises à jour automatiques via la place de marché) :**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI ou l'un des 50+ [Hôtes Agent Skills](https://agentskills.io) :**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` s'installe globalement pour votre utilisateur, disponible dans tous les projets. Déposez-le dans la portée de chaque projet.)

Plus d'options d'installation (claude.ai web, OpenClaw, manuel) dans la section [Install](#installation) ci-dessous.

Zéro configuration. Reddit, HN, Polymarket et GitHub fonctionnent immédiatement. Exécutez-le une fois et l'assistant de configuration déverrouille X, YouTube, TikTok, arXiv, Techmeme et plus encore en 30 secondes.

---

Votes positifs sur Reddit. X aime. Transcriptions YouTube. Engagement sur TikTok. Cotes Polymarket soutenues par de l'argent réel et des informations privilégiées. Cela représente des millions de personnes qui votent chaque jour avec leur attention et leur portefeuille. /last30days recherche tout cela en parallèle, le note en fonction de ce avec quoi de vraies personnes interagissent réellement, et un juge d'agent IA le synthétise en un seul dossier.

Google regroupe les éditeurs. /last30days recherche des personnes.

Vous ne pouvez obtenir cette recherche nulle part ailleurs car aucune IA n’a accès à tout cela. La recherche Google ne touche pas les commentaires Reddit ou les publications X. ChatGPT a un accord avec Reddit mais ne peut pas rechercher X ou TikTok. Gemini a YouTube mais pas Reddit. Claude n'en possède aucun nativement. Chaque plateforme est un jardin clos avec sa propre API, ses propres tokens, sa propre authentification. Mais vous pouvez apporter vos propres clés et sessions de navigateur, et tout à coup, un agent IA peut toutes les rechercher en même temps, les comparer les unes aux autres et vous dire ce qui compte réellement.

C'est le déverrouillage. Il n'y a pas de meilleur moteur de recherche. Une douzaine de plateformes déconnectées, pontées par un agent.

```
/last30days Peter Steinberger
```

Vous avez une réunion demain. Vous les recherchez sur Google. Vous obtenez leur LinkedIn à partir de 2023. /last30days vous montre ce qu'ils font réellement ce mois-ci : ils ont rejoint OpenAI pour travailler sur le Codex, luttent contre l'interdiction d'Anthropic sur les agents tiers, expédient 23 PR à un taux de fusion de 85 %, créent "LobsterOS" pour le contrôle des agents multi-appareils, et r/ClaudeCode a obtenu 569 votes positifs en débattant s'il est un héros ou "insupportable". Dispersé dans les publications X, les fils de discussion Reddit, les transcriptions YouTube et les commits GitHub. Rien de tout cela n'était sur Google.

## Pourquoi cela existe

Je l'ai construit pour suivre le rythme de l'IA. Tout change chaque jour et les nerds de Reddit et X sont toujours au top en premier. J'avais besoin de meilleures invites, et les données de formation étaient toujours en retard de plusieurs mois par rapport à ce que la communauté avait déjà compris.

Mais cela s’est transformé en quelque chose de plus grand. Maintenant, je l'exécute avant un appel commercial pour connaître la vérité des 30 derniers jours sur une entreprise. Avant une réunion, pour lire les tweets récents et les transcriptions de podcasts de quelqu'un. Avant un voyage à Disney World pour savoir quels manèges sont fermés et ce que dit la communauté de Genie+. Avant de construire quoi que ce soit, je dois savoir quels problèmes les gens rencontrent réellement.

Si vous rencontrez un PDG, avez-vous lu tous ses tweets et transcriptions YouTube des 30 derniers jours ? J'ai.

## Sources, notées par le peuple

| Source | Ce que les gens vous disent |
|--------|--------------------------|
| **Reddit** | La prise non filtrée. Meilleurs commentaires avec un nombre réel de votes positifs, gratuits, sans clé API. Les vraies opinions que Google enterre. |
| **X / Twitter** | La prise chaude, le fil expert, la réaction cassante. Premier à savoir, premier à argumenter. |
| **YouTube** | La plongée profonde de 45 minutes. Les transcriptions complètes ont recherché les 5 phrases citables qui comptent. |
| **TikTok** | Le créateur touche 3,6 millions de personnes avec une prise que vous ne trouverez jamais sur Google. |
| **Instagram Reels** | Le point de vue de l'influenceur avec des transcriptions de créations orales. Le signal de la culture visuelle. |
| **Hacker News** | Le consensus des développeurs. 825 points, 899 commentaires. Là où les techniciens discutent réellement. |
| **Polymarket** | Pas des avis. Chances. Soutenu par de l'argent réel. 96% de confiance sur les ventes d'albums. 4% sur une acquisition. |
| **GitHub** | Pour les personnes : vitesse des relations publiques, meilleurs dépôts par stars, notes de version. Pour les sujets : problèmes et discussions. |
| **Digg** | Groupes d'histoires sélectionnés à partir du classement AI 1000 de Digg (~ 1 000 comptes IA à signal élevé sur X), avec des citations en ligne attribuables (aucune authentification X requise). Activé automatiquement lorsque `digg-pp-cli` est sur PATH. |
| **arXiv** | Les journaux derrière le battage médiatique. Nouvelle recherche dans la fenêtre, gratuite, sans clé API. Activé automatiquement lorsque `arxiv-pp-cli` est sur PATH (l'installation de première exécution l'installe). |
| **Techmème** | La couche éditoriale d'actualités technologiques, fenêtrée sur vos 30 jours. Gratuit, sans clé API. Activé automatiquement lorsque `techmeme-pp-cli` est sur PATH (l'installation de première exécution l'installe). |
| **LinkedIn** | Le signal professionnel. Publications et articles, avec des articles considérés comme un signal élevé. |
| **StockTwits** | Sentiment des commerçants. S'active automatiquement lorsque votre sujet est un ticker ou une crypto. |
| **Fils** | La couche de texte post-Twitter. Conversations de créateurs et de marques. |
| **Pinterest** | Découverte visuelle. Épinglez, enregistrez et commentez les produits et les idées. |
| **Xiaohongshu (ROUGE)** | Signaux de style de vie, de produit et de créateur chinois. Demandé explicitement avec `--search xhs` lorsqu'un plug-in de navigateur x-mcp connecté ou un service `xiaohongshu-mcp` s'exécute localement. |
| **Ciel bleu** | La couche sociale décentralisée. Publications du protocole AT issues de la migration post-Twitter. |
| **Perplexité** | Synthèse Sonar mise à la terre, lignes brutes de l'API de recherche et recherche approfondie. |
| **Internet** | La couverture éditoriale, les comparaisons de blogs. Un signal parmi tant d’autres, mais pas le seul. |

Les contributeurs de la communauté continuent d’en ajouter. Truth Social et d’autres sources de niche sont dans le moteur et d’autres sont en route.

Un fil de discussion Reddit avec 1 500 votes positifs est un signal plus fort qu’un article de blog que personne n’a lu. Un TikTok avec 3,6 millions de vues vous en dit plus sur ce qui est culturellement pertinent qu'un communiqué de presse. Les cotes du polymarché soutenues par un volume de 66 000 $ sont plus difficiles à contester que la supposition d'un expert.

La synthèse est classée en fonction de ce avec quoi de vraies personnes se sont réellement engagées. Pertinence sociale, pas pertinence SEO.

## Pourquoi les gens l'utilisent réellement

**Avant une réunion.** `/last30days Peter Steinberger` - a rejoint l'équipe Codex d'OpenAI, luttant contre l'interdiction d'Anthropic sur les agents tiers, 23 PR ont fusionné à un taux de fusion de 85 % sur GitHub, créant LobsterOS pour le contrôle des agents multi-appareils. r/ClaudeCode : "Depuis la sortie d'OpenClaw, il était largement connu que si vous l'exécutiez via autre chose que l'API, vous finiriez par être banni" (227 votes positifs). Ce n'est pas sur LinkedIn.

**Pour lire les signaux d'embauche.** `/last30days Listen Labs --hiring-signals` : les pages d'emplois et de carrières actuelles deviennent des preuves citées de changements d'orientation : embauche dans la sécurité de l'entreprise, la réussite des clients, l'infrastructure ou l'expansion des produits. Le rapport indique ce que l’embauche semble signaler, et non ce que la feuille de route prévoit.

**Pour trouver le sujet avant qu'il n'atteigne son apogée.** Demandez à `/last30days what's exploding in AI agents?` et la compétence passe en mode découverte : le moteur balaie les listes de catégories Reddit, les fronts/meilleures histoires de Hacker News, le flux AI 1000 de Digg et X une fois authentifié ; votre agent juge les nominations (noms, filtrage des courriers indésirables, valeur du contenu) et rédige les angles des podcasts/articles X ; Ensuite, vous obtenez 5 à 10 sujets classés en fonction de la vitesse. Chaque résultat comprend des numéros multi-sources, une étiquette Momentum et un suivi `/last30days "<topic>"` prêt à l'emploi.

**Quand quelque chose tombe.** `/last30days Kanye West` - Le Royaume-Uni a bloqué son visa, le Wireless Festival a été annulé, les sponsors ont fui. Mais BULLY a fait ses débuts au numéro 2 du Billboard. Fantano est revenu de son "Yay sabbatique" pour le revoir (653K vues). SoFi Homecoming a fait sortir Lauryn Hill et Travis Scott pour 44 chansons. Polymarket : "Est-ce que Kanye tweetera encore ?" 86% Oui. 23 fils de discussion Reddit, 17 vidéos YouTube, 86 000 votes positifs.

**Pour comparer les outils.** `/last30days OpenClaw vs Hermes vs Paperclip` - "Ce ne sont pas des concurrents, ce sont des couches." OpenClaw est l'exécuteur (351 000 étoiles GitHub, en direct), Hermes est le cerveau qui s'améliore automatiquement (31 000 étoiles), Paperclip est l'organigramme (49 000 étoiles). Le nombre d'étoiles est extrait en direct de l'API GitHub, et non d'articles de blog obsolètes. Table côte à côte avec architecture, mémoire, sécurité, le meilleur pour. Selon @IMJustinBrooke : "OpenClaw = Charmander, Hermes = Charizard."

**Pour comprendre le monde.** `/last30days Iran vs USA` - Jour 38 de la guerre. La date limite fixée mardi par Trump pour que l'Iran rouvre le détroit d'Ormuz. Deux avions de guerre américains abattus. Pétrole à 126$/baril. L'AIE l'a qualifié de "plus grande rupture d'approvisionnement dans l'histoire du marché pétrolier mondial". Polymarket : cessez-le-feu d'ici le 31 décembre à 74 %. 27 publications X, 10 vidéos YouTube, 20 marchés de prédiction.

**Avant un voyage.** `/last30days Universal Epic Universe` - Agrandissement déjà en construction. Permis « Projet 680 » déposé. Feu d'artifice confirmé par les infrastructures mais inopiné. Temps d'attente : Mine-Cart Madness en moyenne 148 minutes. Pas encore de laissez-passer annuel et les habitants sont frustrés. Les Stardust Racers seront rénovés jusqu'au 5 avril.

**Pour apprendre quelque chose rapidement.** `/last30days Nano Banana Pro prompting` - Les invites structurées en JSON remplacent la soupe de balises. Le format imbriqué du @pictsbyai évite le « saignement des concepts ». Le flux de travail de modification en premier surpasse la régénération. Ensuite, il vous écrit une invite de production utilisant exactement ce que la communauté a dit fonctionner.

## Quoi de neuf

Depuis l'annonce de la v3.3 en mai, à partir de la v3.11.1 (juillet 2026) : 175 PR fusionnés - dont 122 provenant de 52 contributeurs de la communauté - dans 15 versions. C'est ce qui a atterri.

### Première classe sur OpenAI Codex

/last30days est désormais un plugin Codex natif avec une configuration guidée - pas un port, un citoyen de première classe. Les citations compatibles avec le moteur de rendu signifient que la sortie du Codex se lit comme un bref au lieu d'une soupe d'URL (#694), et que le même moteur fonctionne sur les hôtes Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClaw et plus de 50 Agent Skills. Manifeste du plugin Codex par [@rfoust](https://github.com/rfoust) (#686), correctif d'authentification Codex par [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmeme et Digg - gratuits, pas de clés API

arXiv apporte les journaux derrière le battage médiatique et Techmeme apporte la couche d'actualités technologiques éditoriales - gratuite, sans clé, et la première configuration installe leurs CLI afin qu'elles s'activent automatiquement (#709). Les clusters d'histoires AI 1000 de Digg arrivent sans authentification X de la même manière : le programme d'installation installe la CLI Digg gratuite pour vous (#590). Trustpilot propose une option d'adhésion pour les recherches sur les marques grand public.

### Free Reddit a augmenté ses scores réels et ses meilleurs commentaires

L'API publique .json de Reddit est morte ; le libre chemin est revenu plus fort. RSS sans clé + scraping shreddit (#457), découverte de subreddit dédié avec un nombre réel de votes positifs via arctic-shift (#696) et un seuil de pertinence pour qu'une publication virale hors sujet ne puisse pas détourner votre brief (#488, merci [@rzachsmith](https://github.com/rzachsmith)). Aucune clé API. De vrais scores. Principaux commentaires inclus.

### Les meilleurs commentaires dans chaque brief

Les commentaires sont désormais une couche par défaut entre les sources : commentaires Instagram avec une diversité basée sur le classement afin que cinq prises chaudes ne proviennent pas toutes d'une seule publication (#751), commentaires YouTube plus une sauvegarde de transcription ScrapeCreators pour le moment où yt-dlp est supprimé (#637), et les commentaires votés par la foule pondérés dans les meilleures prises afin que les lignes les plus drôles de la communauté survivent au score (#592, #608).

### Une commande de médecin

Demandez un bilan de santé et le médecin exécute chaque source, puis prescrit des correctifs exacts : quelle clé manque, quelle CLI est hors PATH, quel cookie a expiré (#753). Plus besoin de deviner pourquoi X est revenu mince.

### Recherche X, reconstruite

Le pipeline X a fait l'objet d'une refonte de fond : les voies FROM et ABOUT afin que les propres publications d'une personne et la conversation à leur sujet soient toutes deux classées (#610), la désambiguïsation des sous-requêtes sensibles à la personne (#611), la paternité de première partie avec classement des signaux d'interaction (#613) et une source X unique avec basculement automatique du backend (#622). Plus un honnête `--diagnose` qui sonde réellement l'authentification (#609).

### Plus de sources jointes

LinkedIn via ScrapeCreators, avec des articles comme signal fort ([@ravstr](https://github.com/ravstr), #702). StockTwits s'active automatiquement pour les sujets de ticker et de cryptographie ([@wtiwana](https://github.com/wtiwana), #658). Perplexity a développé les modes API directs et la recherche approfondie asynchrone ([@sk-holmes](https://github.com/sk-holmes), #629).

### Endurci par la communauté

La vague de sécurité était presque entièrement un travail communautaire : correctifs XSS stockés dans le moteur de rendu HTML ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), fichiers temporaires de cookies verrouillés, CI renforcés par la chaîne d'approvisionnement avec OpenSSF Scorecard et attestation de provenance de la construction ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), analyses Semgrep et OSV-Scanner ainsi qu'une porte d'examen des dépendances PR ([@23241a6749](https://github.com/23241a6749)), un plancher de couverture de test introduit à 60 % et augmenté depuis à 84 % ([@gourab5139014](https://github.com/gourab5139014)) et une analyse de sécurité Hermes effacée de toutes les découvertes CRITIQUES (#768).

### Va plus loin

Langues hébraïques et non latines ([@dudyme](https://github.com/dudyme)). Tokenisation compatible CJK pour les sources chinoises ([@An-idd](https://github.com/An-idd)). Une vague de compatibilité Windows. Extraction de cookies dans toute la famille Chromium - Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) - ainsi que les sources d'informations d'identification macOS Keychain et Linux pass(1). Analyse historique `--as-of` ([@chiyi-creator](https://github.com/chiyi-creator)). Python 3.12 provisionné automatiquement via uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` pour lire les pages d'emploi d'une entreprise. Deltas de liste de surveillance entre les exécutions.

### Toujours dans la boîte de la v3

Les fondations de la v3 sont toujours là : le cerveau de pré-recherche qui résout les bons identifiants, sous-reddits et hashtags avant qu'un seul appel d'API ne se déclenche (construit par [@j-sperling](https://github.com/j-sperling)) ; Meilleurs scores pour l'humour et la viralité ainsi que la pertinence ; fusion de clusters multi-sources ; comparaisons en un seul passage (« CLI vs MCP » en 3 minutes, et non 12) ; comparaisons `--competitors` découvertes automatiquement ; Mode personne GitHub (`--github-user=steipete`) ; Mode ELI5 (« eli5 activé » après toute exécution) ; et des mémoires HTML autonomes et partageables (`--emit=html`). Les boutons de configuration se trouvent dans [CONFIGURATION.md](CONFIGURATION.md).

## Installer

| Surfaces | Installer | Mises à jour |
|---------|---------|---------|
| **Claude Code** (recommandé) | `/plugin marketplace add mvanhorn/last30days-skill` | Auto via Marketplace, ou `claude plugin update last30days@last30days-skill` |
| **Grok** (CLI xAI Build) | `grok plugin marketplace add mvanhorn/last30days-skill` puis `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI ou l'un des 50+ [Hôtes Agent Skills](https://agentskills.io)** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [Téléchargez `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) et téléchargez via claude.ai > Personnaliser > Compétences > + > Créer une compétence > Télécharger une compétence | Re-télécharger et ré-uploader |
| **Claude Desktop** | [Téléchargez le `.mcpb` pour votre plateforme](https://github.com/mvanhorn/last30days-skill/releases/latest) et faites-le glisser dans Paramètres > Extensions | Re-téléchargez et faites glisser le nouveau bundle dans |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recommandé)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Recommandé car le marché Claude Code gère les mises à jour pour vous : le cache du plugin est versionné et s'actualise automatiquement lors de la publication d'une nouvelle version. Exécutez `claude plugin update last30days@last30days-skill` pour forcer une vérification.

Si vous préférez utiliser le chemin d'installation des compétences d'agent sur Claude Code, celui-ci est également pris en charge :

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

Le plugin natif et l'installation `npx skills` peuvent coexister. Notez que Claude Code n'effectue pas de déduplication entre les méthodes d'installation : si le plugin Marketplace et la copie `npx skills` sont actifs, `/last30days` affichera deux entrées. Utilisez une méthode d'installation par machine.

### Grok (CLI xAI Build)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) installe last30days en tant que plugin natif. L'installation directe suit le référentiel :

```bash
grok plugin install mvanhorn/last30days-skill
```

Ou ajoutez ce dépôt en tant que source de marché, puis installez par nom de plugin :

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Ajoutez `--trust` pour ignorer la confirmation d'installation. Mise à jour avec `grok plugin update last30days`. Grok lit également les manifestes du Claude Code pour vérifier leur compatibilité ; la paire native `.grok-plugin/` est la voie de première classe (et à quelle liste officielle [xAI Marketplace](https://github.com/xai-org/plugin-marketplace) pointe). `npx skills add` reste une solution de secours entre hôtes valide.

### Codex, Cursor, Copilot, Gemini CLI et autres hôtes de compétences d'agent

Installation via la CLI ouverte [Agent Skills](https://agentskills.io) — prend en charge plus de 50 harnais, notamment `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose` et plus (liste complète sur le [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

L'indicateur `-g` (global) s'installe dans votre répertoire utilisateur afin que la compétence soit disponible dans tous les projets. Sans `-g`, `npx skills` installe le projet localement dans `./.skills/` (engagé avec le dépôt). Pour un outil de recherche sur le monde, vous voulez une approche globale.

Le bureau Codex et d'autres hôtes en mode dossier peuvent fonctionner dans des dossiers ordinaires ainsi que dans les dépôts Git. Avant la première recherche, demandez à l'agent hôte d'exécuter le `scripts/last30days.py --preflight` fourni à partir du répertoire de compétences chargé ; dans une extraction de source, la commande équivalente est `python3 skills/last30days/scripts/last30days.py --preflight`. Il affiche la source de configuration, le plan des cookies du navigateur, les écritures planifiées, les commandes facultatives et la configuration du projet ignorée sans lire les cookies, écrire des fichiers ou exécuter des recherches.

Par défaut, cela s'installe pour le faisceau détecté par `npx skills`. Pour en cibler un en particulier (ou plusieurs) :

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Mettre à jour plus tard avec :

```bash
npx skills update last30days -g
```

Ou mettez à jour tout ce que vous avez installé globalement via `npx skills` :

```bash
npx skills update -g
```

Répertoriez et supprimez avec `npx skills list -g` et `npx skills remove last30days -g`.

### claude.ai (web)

1. [Téléchargez `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) à partir de la dernière version
2. Accédez à [claude.ai > Personnaliser > Skills](https://claude.ai/customize/skills)
3. Cliquez sur le bouton `+` dans le panneau Compétences > cliquez sur `Create skill` > `Upload a skill` et parcourez/déposez le fichier dans

Activez d'abord « Exécution de code et création de fichiers » sous Fonctionnalités : les compétences ne fonctionneront pas sans cela.

### Bureau Claude

Claude Desktop installe `/last30days` en tant que serveur MCP via un bundle `.mcpb` (un package Model Context Protocol en un clic).

1. Accédez à la [dernière version ](https://github.com/mvanhorn/last30days-skill/releases/latest) et téléchargez le `.mcpb` pour votre plateforme :
- macOS Apple Silicon : `last30days-pp-mcp-darwin-arm64.mcpb`
- macOS Intel : `last30days-pp-mcp-darwin-amd64.mcpb`
-Linux x86_64 : `last30days-pp-mcp-linux-amd64.mcpb`
2. Ouvrez Claude Desktop, accédez à Paramètres > Extensions et faites glisser le fichier.
3. Lorsque vous y êtes invité, collez les clés API des sources que vous souhaitez activer. Chaque champ est facultatif : le moteur passe en mode Web uniquement si vous les ignorez tous. Les clés sont stockées dans le trousseau de votre système d’exploitation.
4. Redémarrez Claude Desktop. Demandez à Claude de « rechercher Peter Steinberger » ou n'importe quel sujet et il appellera l'outil `research`.

**Exigence d'hôte :** Python 3.12+ sur PATH. Le bundle fournit la source du moteur mais utilise votre interpréteur Python local. Installez depuis [python.org](https://www.python.org/downloads/) sous Windows ; macOS et la plupart des distributions Linux proposent une version compatible.

**Les clés ne sont pas synchronisées avec la compétence Code.** Claude Desktop et Claude Code conservent des magasins d'informations d'identification distincts de par leur conception. Si vous avez déjà configuré `~/.config/last30days/.env` pour la compétence Code, vous saisirez à nouveau les mêmes clés ici une fois.

La prise en charge de Windows est différée jusqu'à ce que les points d'entrée du manifeste par plate-forme soient réglés ; suivre dans un numéro de suivi.

### OpenClaw

```bash
clawhub install last30days-official
```

Pour les flux de travail d'action X/Twitter en dehors de la recherche `/last30days`, tels que la publication
tweets ou réponses, exportation de followers, gestion des médias, moniteurs et cadeaux
tirages, utilisez [TweetClaw](https://github.com/Xquik-dev/tweetclaw) comme compagnon
Plugin OpenClaw. TweetClaw est maintenu par Xquik-dev et est répertorié uniquement en tant que
chemin compagnon facultatif, pas une dépendance ou une approbation des 30 derniers jours.

### Manuel (développeur)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

Le lien symbolique maintient l'installation synchronisée avec votre arborescence de travail pendant que vous modifiez - aucune recopie n'est nécessaire. Pour `claude.ai`, créez le fichier `.skill` à partir de la source : `bash skills/last30days/scripts/build-skill.sh` produit `dist/last30days.skill`.

Reddit (avec commentaires), Hacker News, Polymarket et GitHub fonctionnent immédiatement. Zéro configuration. Exécutez `/last30days` une fois et l'assistant de configuration déverrouille plus de sources en 30 secondes, y compris les CLI gratuites arXiv et Techmeme.

## Apportez vos propres clés

Ces plateformes n'ont pas de relations entre elles. X ne sait pas ce que pense Reddit. YouTube ne voit pas TikTok. Mais vous pouvez apporter vos propres clés API et jetons de navigateur, et du coup vous avez accès à tous en même temps.

| Sources | Ce dont vous avez besoin | Coût |
|---------|---------------|------|
| Reddit (avec commentaires) + HN + Polymarket + GitHub + StockTwits | Rien | Gratuit |
| arXiv + Techmeme | CLI gratuites, installées automatiquement lors de la première configuration | Gratuit |
| X/Twitter | Connectez-vous à x.com dans n'importe quel navigateur ou définissez `XQUIK_API_KEY` / `XAI_API_KEY` | Les cookies du navigateur sont gratuits ; les clés sont spécifiques au fournisseur |
| YouTube | `brew install yt-dlp` | Gratuit |
| Ciel bleu | Mot de passe de l'application de bsky.app | Gratuit |
| TikTok + Instagram + Fils de discussion + Pinterest + LinkedIn + Commentaires YouTube | Clé ScrapeCreators | 10 000 appels gratuits, puis PAYG |
| Xiaohongshu (ROUGE) | Exécutez un plug-in de navigateur x-mcp connecté ou un service `xiaohongshu-mcp` et inscrivez-vous avec `--search xhs` par exécution ou `INCLUDE_SOURCES=xiaohongshu` dans `.env` ; last30days sonde automatiquement `http://localhost:18060` puis `http://host.docker.internal:18060`, ou utilise `XIAOHONGSHU_API_BASE` pour une URL personnalisée | Aucune clé API des 30 derniers jours ; dépend de votre service de session de navigateur local |
| DripStack (newsletters financières premium) | Opt-in : `--search dripstack` par exécution, ou `INCLUDE_SOURCES=dripstack` dans `.env` | Pas de clé ; API de recherche publique gratuite |
| Sonar Perplexity / API de recherche / Recherche approfondie | Clé Perplexité ou clé OpenRouter comme solution de secours Sonar | Payez au fur et à mesure |
| Recherche sur le Web | Clé de recherche courageuse | 2 000 requêtes gratuites/mois |

### Trousseau macOS (facultatif)

Sur macOS, vous pouvez stocker les clés dans le trousseau système au lieu d'un fichier `.env`. La compétence les sélectionne automatiquement en tant que source de priorité la plus faible : les fichiers `.env` et l'environnement de processus gagnent toujours en cas de collision.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Les éléments sont stockés sous le nom de service `last30days-<KEY>` pour l'utilisateur actuel. Sur les plates-formes non Darwin, le chargeur ne fonctionne pas, il n'y a donc aucun changement de comportement pour les utilisateurs Linux/Windows.

Vous disposez déjà de clés sous différents noms de service de trousseau ? Définissez le mappage non secret `LAST30DAYS_KEYCHAIN_ALIASES` décrit dans [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) au lieu de copier les secrets.

Voir [CONFIGURATION.md](CONFIGURATION.md) pour la matrice complète des clés par source, la priorité du fournisseur de raisonnement et la priorité du backend de recherche Web.

## Configuration

Deux choses que vous voudrez probablement savoir dès le premier jour :

**Où les fichiers de recherche sont enregistrés.** `LAST30DAYS_MEMORY_DIR` est par défaut `~/Documents/Last30Days/` (Windows : `C:\Users\<you>\Documents\Last30Days\`). Remplacez en définissant cette variable d'environnement sur n'importe quel chemin de votre shell, ou `--save-dir <path>` par exécution. Utilisez `--output <file>` lorsque vous avez besoin du résultat rendu selon un chemin exact, en utilisant le format sélectionné par `--emit`. Utilisez `--save-suffix=<name>` pour séparer plusieurs variantes du même sujet (par exemple, par client). Chaque exécution de `--save-dir` produit `<slug>-raw[-suffix].md`. Exécutez `python3 skills/last30days/scripts/last30days.py --preflight` pour examiner les écritures planifiées avant une exécution de recherche.

**Sortie structurée pour les agents et les flux de travail.** Demandez à `/last30days` un JSON lisible par machine pour recevoir le profil d'agent stable et versionné. Pour une utilisation directe du moteur dans les scripts ou le développement, exécutez `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json` ; ajoutez `--json-profile=raw` uniquement lorsque vous avez besoin du vidage interne `Report` non versionné. Consultez la [référence du champ d'exportation JSON et la politique de gestion des versions](docs/reference/json-export.md).

**Découverte sans sujet.** Demandez à `/last30days what's trending in AI agents?` d'obtenir un dossier de découverte classé au lieu de rechercher un sujet que vous connaissez déjà. Sur un hôte d'agent, cela exécute le protocole d'évaluation de l'hôte à trois commandes (le modèle nomme les sujets, filtre les indésirables, note la valeur et écrit les angles du contenu). Pour une utilisation directe du moteur dans des scripts ou cron, exécutez `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (one-shot : noms de sujets déterministes, sans angles) ; ajoutez `--emit=json` pour le contrat de découverte versionné. La découverte s'exclut mutuellement avec un sujet positionnel et `--drill`.

**Surveillance des tendances entre les exécutions.** Le mode par défaut produit un nouvel instantané de démarque par exécution. Pour accumuler les résultats au fil du temps, ajoutez `--store` pour persister dans une base de données SQLite, puis utilisez [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) pour les exécutions planifiées (avec livraison Slack/webhook en option sur les nouveaux résultats) et [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) pour les résumés quotidiens/hebdomadaires. Le modèle de cadence complet se trouve dans [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Une bibliothèque de recherche avec abonnement.** Demandez à `/last30days` de créer votre flux de bibliothèque ou utilisez `python3 skills/last30days/scripts/last30days.py library feed` directement pour les scripts et le développement. Il transforme les mémoires enregistrés en `index.html`, en Atom `feed.xml` local et en pages brèves lisibles. Ajoutez `--publish` uniquement lorsque vous souhaitez héberger l'index HTML et les pages brèves ; la publication est explicite et publique par défaut. Pour rendre le flux Atom accessible, hébergez le répertoire de sortie généré sur un hôte statique tel que GitHub Pages.

**Recherchez tout ce que vous avez recherché.** Demandez à `/last30days search my library for MCP servers` ou `/last30days have I researched MCP servers before?`. Pour une utilisation directe du moteur, exécutez `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. La recherche est hors ligne et déterministe : elle indexe progressivement les mêmes résumés enregistrés que ceux utilisés par le flux de la bibliothèque, fusionne les observations de magasin correspondantes par exécution et regroupe les résultats par sujet et par date. De nouvelles analyses font également apparaître une section compacte **De votre bibliothèque** lorsque des recherches antérieures chevauchent le sujet actuel ; définissez `LAST30DAYS_LIBRARY_CONTEXT=off` pour désactiver ce contexte passif.

Les scripts wrapper par client, les sous-reddits de catégorie personnalisés et le canal bêta expérimental pour les personnalisations en cours sont également documentés dans [CONFIGURATION.md](CONFIGURATION.md).

## Showcase : flux de recherche communautaire

Vous avez publié une mise à jour récurrente de l'IA, une surveillance du marché ou une obsession merveilleusement étroite pour les 30 derniers jours ? Partagez l'URL de la bibliothèque publique (ou l'URL Atom après avoir hébergé `feed.xml` sur un hôte statique) dans [le fil de discussion de la communauté ](https://github.com/mvanhorn/last30days-skill/issues/532). Les flux communautaires seront liés ici au fur et à mesure que leurs propriétaires les soumettront ; le fil est le point de collecte entre-temps.

## Comment ça marche

1. **Vous saisissez un sujet.** Personne, entreprise, produit, technologie, « X contre Y ». Rien.
2. **L'agent décide qui compte.** Trouve X identifiants (y compris les fondateurs), les dépôts GitHub, les subreddits, les hashtags TikTok, les chaînes YouTube. Pour "Kanye West", il connaît r/hiphopheads, @kanyewest et "bully review" sur YouTube. Pour "OpenClaw", il résout openclaw/openclaw sur GitHub et récupère le nombre d'étoiles en direct.
3. **Toutes les sources recherchées en parallèle.** Expansion multi-requêtes. Des résultats notés par engagement, pertinence, fraîcheur.
4. **La profondeur que personne d'autre n'a.** Transcriptions YouTube complètes de vidéos de réaction. Meilleurs commentaires Reddit avec le nombre de votes positifs. Légendes TikTok. Cotes du polymarché. Pas seulement des titres et des liens.
5. **Même histoire, fusionné.** Wireless Festival annoncé sur Reddit, discuté sur X, prix des billets sur TikTok = un cluster, pas trois éléments distincts.
6. **Synthétisé en un seul mémoire.** Fondé sur des données spécifiques. Cité par la source. Classé en fonction de ce avec quoi les gens s'engagent réellement. Pas "voici ce que j'ai trouvé". C'est "voici ce qui compte".
7. **Il devient alors votre expert.** Après une seule exécution, votre session Claude sait tout ce que la communauté sait. Posez des questions de suivi. Demandez-lui de rédiger des invites, de rédiger des e-mails, de planifier des voyages, de concevoir des systèmes - le tout fondé sur la réalité actuelle.

## Ce que disent les gens

> "J'ai trouvé une compétence Claude Code qui recherche n'importe quel sujet sur Reddit, X, YouTube et HN au cours des 30 derniers jours. Ensuite, j'écris les invites pour vous. J'ai effectué des recherches manuelles sur Reddit et X avant chaque élément de contenu que j'écris. Onglet par onglet. Fil par fil. C'est la partie qui prend 90 minutes. Cela l'élimine. " -@itsjasonai

> "Cette compétence a remplacé l'ensemble de mon flux de travail de recherche. Vous lui donnez un sujet, elle supprime Reddit, X et le Web de ce dont les gens parlent réellement. Pas d'anciens articles de blog. De vraies conversations des 30 derniers jours." -@itswilsoncharles

> "5 des 10 dépôts tendances sur GitHub aujourd'hui sont des outils Claude. #1 : mvanhorn/last30days-skill" -@yieldhunter95

## Open source

Licence MIT. Aucun suivi. Aucune analyse. Votre recherche reste sur votre machine. Plus de 2 700 tests.

Construit avec Python 3.12+, yt-dlp, Node.js (client Bird fourni pour la recherche X) et l'API ScrapeCreators. Architecture du moteur v3 par [@j-sperling](https://github.com/j-sperling).

Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour ouvrir un PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) pour la liste complète des contributeurs de la communauté et [CHANGELOG.md](CHANGELOG.md) pour l'historique des versions.

## Historique des étoiles

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
