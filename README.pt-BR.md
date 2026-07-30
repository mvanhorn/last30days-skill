# /last30days

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | Português (Brasil) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

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

**Um mecanismo de busca liderado por agentes de IA, pontuado por votos positivos, curtidas e dinheiro real - não por editores.**

Este README acompanha o pipeline v3 atual. A especificação de habilidade em tempo de execução está em [skills/last30days/SKILL.md](skills/last30days/SKILL.md), que é a fonte de verdade para o comportamento mais recente de comandos e configurações.

**Claude Code (recomendado — atualizações automáticas via marketplace):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI, ou qualquer um dos 50+ [Agent Skills](https://agentskills.io) apresentadores:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` instala globalmente para seu usuário, disponível em todos os projetos. Coloque para o escopo por projeto.)

Mais opções de instalação (claude.ai web, OpenClaw, manual) na seção [Install](#instalação) abaixo.

Zero configuração. Reddit, HN, Polymarkete GitHub funcionam imediatamente. Execute uma vez e o assistente de configuração desbloqueia X, YouTube, TikTok, arXiv, Techmemee mais em 30 segundos.

---

Reddit votos positivos. X gostos. YouTube transcrições. TikTok engajamento. Polymarket probabilidades respaldadas por dinheiro real e informações privilegiadas. São milhões de pessoas votando com sua atenção e carteiras todos os dias. /last30days pesquisa tudo em paralelo, pontua pelo que pessoas reais realmente envolvem, e um juiz agente de IA sintetiza tudo em um único recurso.

Google agrega editores. /last30days pesquisa pessoas.

Você não pode encontrar essa busca em nenhum outro lugar porque nenhuma IA única tem acesso a tudo. Google busca não mexe Reddit comentários ou X postagens. ChatGPT tem um acordo com Reddit , mas não pode pesquisar X ou TikTok. Gemini tem YouTube , mas não Reddit. Claude não tem nenhuma delas nativamente. Cada plataforma é um jardim murado com seu próprio API, seus próprios tokens, sua própria autenticação. Mas você pode trazer suas próprias chaves e sessões de navegador, e de repente um agente de IA pode pesquisar todas de uma vez, pontuá-las entre si e dizer o que realmente importa.

Esse é o desbloqueio. Não há um motor de busca melhor. Uma dúzia de plataformas desconectadas, conectadas por um agente.

```
/last30days Peter Steinberger
```

Você tem uma reunião amanhã. Você Google eles. Você recebe o LinkedIn deles de 2023. /last30days te dá o que eles realmente estão fazendo este mês: entrou na OpenAI para trabalhar em Codex, lutou contra a proibição da Anthropic sobre agentes terceirizados, enviou 23 PRs com 85% de taxa de fusão, construiu "LobsterOS" para controle de agentes entre dispositivos, e o código r/Claudeatingiu 569 votos positivos debatendo se ele é um herói ou "insuportável". Espalhados por X postagens, Reddit tópicos, transcrições YouTube e GitHub commits. Nada disso estava no Google.

## Por que isso existe

Eu o construí para acompanhar a IA. Tudo muda todo dia e os nerds Reddit e X sempre estão por dentro primeiro. Eu precisava de prompts melhores, e os dados de treinamento sempre estavam meses atrás do que a comunidade já havia descoberto.

Mas virou algo maior. Agora eu faço isso antes de uma ligação de vendas para saber a verdade dos últimos 30 dias sobre um negócio. Antes de uma reunião para ler os tweets recentes e transcrições de podcasts de alguém. Antes de uma Disney World viagem para saber quais brinquedos estão fechados e o que a comunidade diz sobre Genie+. Antes de construir qualquer coisa para saber quais problemas as pessoas realmente estão enfrentando.

Se você está se reunindo com um CEO, já leu todos os tweets e transcrições de YouTube dos últimos 30 dias? Leu.

## Fontes, pontuadas pelo povo

| Fonte | O que as pessoas te dizem |
|--------|--------------------------|
| **Reddit** | A opinião sem filtros. Comentários no topo com contagens reais de votos, gratuito, sem API chave. As opiniões reais que Google enterram. |
| **X / Twitter** | A opinião polêmica, o fio do especialista, a reação de quebra. Primeiro a saber, primeiro a discutir. |
| **YouTube** | O mergulho profundo de 45 minutos. Transcrições completas buscadas pelas 5 frases citáveis que importam. |
| **TikTok** | O criador alcançando 3,6 milhões de pessoas com uma opinião que você nunca encontrará sobre Google. |
| **Instagram Reels** | A perspectiva do influenciador com transcrições de spoken word. O sinal cultural visual. |
| **Hacker News** | O consenso dos desenvolvedores. 825 pontos, 899 comentários. Onde pessoas técnicas realmente discutem. |
| **Polymarket** | Não opiniões. Probabilidades. Garantido por dinheiro real. 96% de confiança nas vendas do álbum. 4% em uma aquisição. |
| **GitHub** | Para as pessoas: PR Velocity, top repos por estrelas, notas de release. Para tópicos: questões e discussões. |
| **Digg** | Clusters de histórias selecionados do ranking AI 1000 da Digg(~1000 contas de IA de alta sinalização no X), com citações inline atribuíbles (sem necessidade de autenticação X ). Autoativado quando `digg-pp-cli` está ativado PATH. |
| **arXiv** | Os papéis por trás do hype. Nova pesquisa na janela, gratuita, sem chave API . Ativado automaticamente quando `arxiv-pp-cli` está no PATH (a primeira configuração instala). |
| **Techmeme** | A camada editorial de notícias tecnológicas, datada para seus 30 dias. Grátis, sem chave API . Ativada automaticamente quando `techmeme-pp-cli` está no PATH (a primeira execução instala). |
| **LinkedIn** | O sinal profissional. Posts e artigos, com artigos ponderados como sinal alto. |
| **StockTwits** | Sentimento do trader. Ativa-se automaticamente quando seu tema é um ticker ou criptomoeda. |
| **Threads** | A camada de texto pós-Twitter. Conversas de criadores e marcas. |
| **Pinterest** | Descoberta visual. Fixe, salve e comente sobre produtos e ideias. |
| **Xiaohongshu (RED)** | Sinais de estilo de vida, produto e criador chineses. Solicitado explicitamente com `--search xhs` quando um plugin ou serviço de `xiaohongshu-mcp` do navegador x-mcp logado está rodando localmente. |
| **Bluesky** | A camada social descentralizada. Postagens do Protocolo AT da migração pós-Twitter. |
| **Perplexity** | Síntese de Sonar no Solo, Busca API Linhas brutas e Pesquisa Profunda. |
| **Web** | A cobertura editorial, as comparações com blogs. Um sinal entre muitos, não o único. |

Colaboradores da comunidade continuam adicionando mais. Truth Social e outras fontes de nicho estão no motor com mais a caminho.

Um tópico Reddit com 1.500 votos positivos é um sinal mais forte do que um post de blog que ninguém leu. Um TikTok com 3,6 milhões de visualizações diz mais sobre o que é culturalmente relevante do que um comunicado à imprensa. Polymarket probabilidades apoiadas em $66 mil em volume são mais difíceis de contestar do que um palpite de um comentarista.

A síntese é classificada pelo que as pessoas reais realmente se envolveram. Relevância social, não SEO relevância.

## Para que as pessoas realmente usam

**Antes de uma reunião.** `/last30days Peter Steinberger` - juntou-se à equipe Codex da OpenAI, lutando contra a proibição da Anthropic de agentes terceiros, 23 PRs fundiu com 85% de taxa de fusão na GitHub, construindo LobsterOS para controle de agentes entre dispositivos. Código r/Claude: "Desde OpenClaw lançado, era amplamente conhecido que, se você rodasse por qualquer coisa que não fosse a API, acabaria sendo banido" (227 votos positivos). Isso não é culpa LinkedIn.

**Para ler sinais de contratação.** `/last30days Listen Labs --hiring-signals` - as páginas atuais de empregos e carreiras passam a ser evidências citadas para mudanças de foco: contratações para segurança empresarial, sucesso do cliente, infraestrutura ou expansão de produtos. O relatório diz o que a contratação parece sinalizar, não o que o roteiro irá trazer.

**Para encontrar o tema antes que ele atinja o auge.** Pergunte `/last30days what's exploding in AI agents?` e a habilidade muda para o modo descoberta: o motor varre Reddit listagens de categorias, Hacker News primeiras histórias/melhores, o feed AI 1000 da Digge X quando autenticado; seu agente avalia as indicações (nomes, filtragem de lixo, qualidade de conteúdo) e escreve podcast / X- ângulos de artigo; depois você recebe de 5 a 10 tópicos classificados por velocidade. Cada resultado inclui números de fontes cruzadas, um rótulo de momentum e um `/last30days "<topic>"` de acompanhamento pronto para rodar.

**Quando algo cai.** `/last30days Kanye West` - O Reino Unido bloqueou seu visto, o Festival Wireless cancelou, os patrocinadores fugiram. Mas BULLY estreou em #2 na Billboard. Fantano voltou do seu "Yay sabático" para resenhar (653 mil visualizações). O SoFi Homecoming trouxe Lauryn Hill e Travis Scott para 44 músicas. Polymarket: "Será que Kanye vai tuitar de novo?" 86% Sim. 23 tópicos Reddit , 17 vídeos YouTube , 86 mil votos positivos.

**Para comparar ferramentas.** `/last30days OpenClaw vs Hermes vs Paperclip` - "Esses não são concorrentes, são camadas." OpenClaw é o executor (351K GitHub estrelas, ao vivo), Hermes é o cérebro autoaperfeiçoador (31K estrelas), Paperclip é o organigrama (49K estrelas). Contagens de estrelas puxadas ao vivo do GitHub API, não posts de blog sem graça. Mesa lado a lado com arquitetura, memória, segurança, o melhor para. Por @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard."

**Para entender o mundo.** `/last30days Iran vs USA` - Dia 38 da guerra. O prazo de terça-feira de Trump para o Irã reabrir o Estreito de Ormuz. Dois aviões de guerra americanos abatidos. Petróleo a $126/barril. A IEA chamou isso de "a maior interrupção do fornecimento na história do mercado global de petróleo." Polymarket: cessar-fogo até 31 de dezembro com 74%. 27 X postagens, 10 vídeos YouTube , 20 mercados de previsão.

**Antes de uma viagem.** `/last30days Universal Epic Universe` - Expansão já em construção. Permissão "Projeto 680" solicitada. Show de fogos de artifício confirmado pela infraestrutura, mas sem aviso. Tempos de espera: Loucura de Carrinhos de Mina com média de 148 minutos. Ainda sem passe anual, e os moradores locais estão frustrados. Stardust Racers em reforma até 5 de abril.

**Para aprender algo rápido.** `/last30days Nano Banana Pro prompting` - JSONprompts estruturados estão substituindo o tag soup. O formato aninhado do @pictsbyaiimpede o "conceito de sangrar". O fluxo de trabalho editado primeiro vence regeneração. Depois, ele escreve um prompt de produção usando exatamente o que a comunidade disse que funciona.

## Novidades

Desde o anúncio da v3.3 em maio, a partir da v3.11.1 (julho de 2026): 175 PRs se fundiram – 122 deles de 52 colaboradores da comunidade – em 15 lançamentos. Foi isso que apareceu.

### Primeira classe no OpenAI Codex

/last30days agora é um plugin nativo de Codex com configuração guiada – não é uma porta, é um cidadão de primeira classe. Citações conscientes do renderizador fazem com que Codex saída seja lida como um brief em vez de um soup de URL (#694), e o mesmo motor roda em Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClawe 50+ hosts Agent Skills . Codex manifesto de plugin por [@rfoust](https://github.com/rfoust) (#686), Codex correção de autenticação por [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmemee Digg - grátis, sem chaves API

arXiv traz os jornais por trás do hype e Techmeme traz a camada editorial de notícias tecnológicas - grátis, zero chaves, e o setup de primeira execução instala seus CLIs para que sejam ativados automaticamente (#709). Clusters de histórias AI 1000 do Diggchegam sem X autenticação da mesma forma – setup instala o Digg CLI gratuito para você (#590). Trustpilot envia opt-in para pesquisa de marcas de consumo.

### O Reddit gratuito aumentou as pontuações reais e os principais comentários

Reddit.json API público morreu; o caminho gratuito voltou mais forte. RSS sem chave + scraping do shreddit (#457), descoberta de subreddit dedicado com contagens reais de votos positivos via arctic-shift (#696), e um piso de relevância para que um post viral fora do tema não possa sequestrar seu briefing (#488, obrigado [@rzachsmith](https://github.com/rzachsmith)). Sem chave API . Pontuações reais. Comentários principais incluídos.

### Os melhores comentários em cada briefing

Os comentários agora são uma camada padrão em várias fontes: comentários no Instagram com diversidade baseada em ranking, então cinco opiniões polémicas não vêm todas de um único post (#751), comentários YouTube mais um backup de transcrição ScrapeCreators para quando o YT-DLP falha (#637), e comentários votados pelo público ponderados em Best Takes para que as falas mais engraçadas da comunidade sobrevivam à pontuação (#592, #608).

### Comando de um médico

Peça um exame de saúde e o médico verifica todas as fontes, depois prescreve soluções exatas – qual chave está faltando, qual CLI está errada PATH, qual cookie expirou (#753). Chega de adivinhar por que X voltou magra.

### X busca, reconstruída

O pipeline X passou por uma reformulação de raiz: faixas FROM e ABOUT para que as próprias postagens e a conversa sobre elas sejam classificadas (#610), desambiguação de subquery consciente da pessoa (#611), aterramento de autoria de primeira parte com ranking de sinal de interação (#613), e uma única fonte X com failover automático de backend (#622). Além de um `--diagnose` honesto que realmente sonda a autenticação (#609).

### Mais fontes se juntaram

LinkedIn via ScrapeCreators, com artigos como high signal ([@ravstr](https://github.com/ravstr), #702). StockTwits ativa-se automaticamente para tópicos de ticker e cripto ([@wtiwana](https://github.com/wtiwana), #658). Perplexity cresceu diretamente em modos API e Deep Research assíncrono ([@sk-holmes](https://github.com/sk-holmes), #629).

### Endurecidos pela comunidade

A onda de segurança foi quase inteiramente trabalho comunitário: correções armazenadas em XSS no renderizador HTML ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), arquivos temporários de cookies bloqueados, CI reforçado na cadeia de suprimentos com OpenSSF Scorecard e atestação de proveniência de compilações ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), varreduras Semgrep e OSV-Scanner além de uma porta de revisão de dependência PR ([@23241a6749](https://github.com/23241a6749)), um piso de cobertura de testes introduzido a 60% e desde então elevado para 84% ([@gourab5139014](https://github.com/gourab5139014)), e uma varredura de segurança Hermes limpa de todas as descobertas CRÍTICAS (#768).

### Alcança mais longe

Hebraico e línguas não latinas ([@dudyme](https://github.com/dudyme)). Tokenização consciente de CJKpara fontes chinesas ([@An-idd](https://github.com/An-idd)). Uma onda de compatibilidade Windows . Extração de cookies em toda a família Chromium - Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) - além de fontes de credenciais macOS Keychain e Linux pass(1). `--as-of` retrospecto histórico ([@chiyi-creator](https://github.com/chiyi-creator)). Provisão automática Python 3.12 via UV ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` para leitura das páginas de empregos de uma empresa. Deltas da lista de observação entre as execuções.

### Ainda está na caixa da v3

As bases da v3 ainda estão todas aqui: o cérebro pré-pesquisa que resolve os handles, subreddits e hashtags corretos antes de uma única chamada API ser lançada (criado por [@j-sperling](https://github.com/j-sperling)); Best Takes pontuação para humor e viralidade junto com relevância; fusão de clusters entre fontes; comparações em passagem única ("CLI vs MCP" em 3 minutos, não 12); comparações `--competitors` descobertas automaticamente; modo pessoa GitHub (`--github-user=steipete`); modo ELI5 ("eli5 ligado" após qualquer execução); e briefs HTML compartilháveis e autônomos (`--emit=html`). Knobs de configuração vivem em [CONFIGURATION.md](CONFIGURATION.md).

## Instalação

| Superfície | Instalação | Atualizações |
|---------|---------|---------|
| **Claude Code**(recomendado) | `/plugin marketplace add mvanhorn/last30days-skill` | Atualização automática via marketplace ou `claude plugin update last30days@last30days-skill` |
| **Grok**(Build xAI CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` então `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLIou qualquer um dos 50+ [Agent Skills](https://agentskills.io) apresentadores** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai**(web) | [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) e envie via claude.ai > Personalizar > Habilidades > + > Criar habilidades > Enviar uma habilidade | Rebaixe e refaça o upload |
| **Claude Desktop** | [Download the `.mcpb` for your platform](https://github.com/mvanhorn/last30days-skill/releases/latest) e arraste para Configurações > Extensões | Baixe novamente e arraste o novo pacote |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recomendado)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Recomendado porque o marketplace Claude Code gerencia as atualizações para você — o cache do plugin é versionado e atualiza automaticamente quando uma nova versão é lançada. Execute `claude plugin update last30days@last30days-skill` para forçar uma verificação.

Se preferir usar o caminho de instalação de agentes-skills no Claude Code, isso também é suportado:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

O plugin nativo e a instalação `npx skills` podem coexistir. Note que Claude Code não desduplica entre métodos de instalação: se você tiver tanto o plugin do marketplace quanto a cópia `npx skills` ativados, `/last30days` mostrará duas entradas. Use um método de instalação por máquina.

### Grok (Build xAI CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) instala o last30days como um plugin nativo. A instalação direta acompanha o repositório:

```bash
grok plugin install mvanhorn/last30days-skill
```

Ou adicione esse repositório como uma fonte do marketplace e depois instale pelo nome do plugin:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Adicione `--trust` para pular a confirmação da instalação. Atualize com `grok plugin update last30days`. Grok também lê os manifestos Claude Code para compatibilidade; o par nativo de `.grok-plugin/` é a linha de primeira classe (e o que uma listagem oficial [xAI marketplace](https://github.com/xai-org/plugin-marketplace) indica). `npx skills add` permanece um recurso válido entre hosts.

### Codex, Cursor, Copilot, Gemini CLIe outros Agent Skills hospedeiros

Instale via [Agent Skills](https://agentskills.io) CLI aberto — suporta 50+ chicotes incluindo `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose`e mais (lista completa no [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

A flag `-g` (global) se instala no seu diretório de usuário, então a habilidade está disponível em todos os projetos. Sem `-g`, `npx skills` instala o projeto localmente no `./.skills/` (comprometido com o repositório). Para uma ferramenta de pesquisa no mundo, global é o que você quer.

Codex servidores desktop e outros hosts em modo pasta podem funcionar tanto em pastas comuns quanto em repositórios Git. Antes de pesquisar, peça ao agente host para executar o `scripts/last30days.py --preflight` incluído do diretório de skill carregado; em uma verificação de código-fonte, o comando equivalente é `python3 skills/last30days/scripts/last30days.py --preflight`. Ele mostra a fonte de configuração, plano de cookies do navegador, escritas planejadas, comandos opcionais e configuração ignorada do projeto sem ler cookies, gravar arquivos ou rodar pesquisas.

Por padrão, isso é instalado para o chicote que `npx skills` detectar. Para direcionar um específico (ou vários):

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Atualização depois com:

```bash
npx skills update last30days -g
```

Ou atualize tudo que você instalou globalmente via `npx skills`:

```bash
npx skills update -g
```

Liste e remova com `npx skills list -g` e `npx skills remove last30days -g`.

### claude.ai (web)

1. [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) do lançamento mais recente
2. Vá para [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Clique no botão `+` no painel de Habilidades > clique em `Create skill` > `Upload a skill` e navegue/coloque o arquivo em

Ative "Execução de código e criação de arquivo" primeiro em Capacidades — as habilidades não funcionam sem isso.

### Claude Desktop

Claude Desktop instala `/last30days` como um servidor MCP via um pacote `.mcpb` (um pacote Model Context Protocol de um clique).

1. Vá até o [latest release](https://github.com/mvanhorn/last30days-skill/releases/latest) e baixe o `.mcpb` para sua plataforma:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Abra Claude Desktop, vá em Configurações > Extensões e arraste o arquivo.
3. Quando solicitado, cole API chaves para as fontes que deseja ativar. Cada campo é opcional — o motor se degrada para modo apenas web se você pular todos. As chaves são armazenadas no chaveiro do seu sistema operacional.
4. Reinicie Claude Desktop. Peça para Claude "pesquisar Peter Steinberger" ou qualquer outro tema e ele chamará a ferramenta de `research` .

**Requisito de host:** Python 3.12+ no PATH. O pacote envia a fonte do motor, mas usa seu interpretador local de Python . Instale a partir do [python.org](https://www.python.org/downloads/) no Windows; macOS e a maioria das distros Linux enviam uma versão compatível.

**As chaves não sincronizam com a habilidade Code.** Claude Desktop e Claude Code mantêm armazenamentos de credenciais separados por design. Se você já configurou `~/.config/last30days/.env` para a habilidade Code, vai digitar as mesmas chaves aqui uma vez novamente.

Windows suporte é adiado até que os pontos de entrada do manifesto por plataforma sejam resolvidos; acompanhe uma edição de acompanhamento.

### OpenClaw

```bash
clawhub install last30days-official
```

Para Xfluxos de trabalho de ação no /Twitter fora da pesquisa `/last30days` , como postar
Tweets ou respostas, exportação de seguidores, gerenciamento de mídia, monitores e sorteios
Draws, use [TweetClaw](https://github.com/Xquik-dev/tweetclaw) como companheiro
OpenClaw plugin. TweetClaw é mantido pelo Xquik-dev e é listado apenas como um
Caminho de companheiro opcional, não uma dependência ou endosso de Last30days.

### Manual (desenvolvedor)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

O symlink mantém a instalação sincronizada com sua árvore de trabalho enquanto você edita — não é necessário re-copiar. Para `claude.ai`, construa o arquivo `.skill` a partir da fonte: `bash skills/last30days/scripts/build-skill.sh` produz `dist/last30days.skill`.

Reddit (com comentários), Hacker News, Polymarkete GitHub funcionam imediatamente. Configuração zero. Execute `/last30days` uma vez e o assistente de configuração desbloqueia mais fontes em 30 segundos, incluindo o arXiv gratuito e Techmeme CLIs.

## Traga suas próprias chaves

Essas plataformas não têm relações entre si. X não sabe o que Reddit pensa. YouTube não vê TikTok. Mas você pode trazer suas próprias chaves de API e tokens de navegador, e de repente tem acesso a todos ao mesmo tempo.

| Fontes | O que você precisa | Custo |
|---------|---------------|------|
| Reddit (com comentários) + HN + Polymarket + GitHub + StockTwits | Nada | Grátis |
| arXiv + Techmeme | Free CLIs, instalado automaticamente pela primeira vez | Grátis |
| X / Twitter | Faça login em x.com em qualquer navegador, ou configure `XQUIK_API_KEY` / `XAI_API_KEY` | Cookies do navegador são gratuitos; as chaves são específicas do provedor |
| YouTube | `brew install yt-dlp` | Grátis |
| Bluesky | Senha do aplicativo do bsky.app | Grátis |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube comentários | ScrapeCreators chave | 10.000 chamadas grátis, depois PAYG |
| Xiaohongshu (RED) | Execute um plugin de navegador x-mcp logado ou serviço `xiaohongshu-mcp` e opte por `--search xhs` por execução ou `INCLUDE_SOURCES=xiaohongshu` no `.env`; last30days auto-sonda `http://localhost:18060` depois `http://host.docker.internal:18060`, ou use `XIAOHONGSHU_API_BASE` para uma URL personalizada | Sem chave de API de últimos 30 dias; depende do seu serviço local de sessão de navegador |
| DripStack (boletins financeiros premium) | Opt-in: `--search dripstack` por tentativa, ou `INCLUDE_SOURCES=dripstack` em `.env` | Sem chave; busca pública gratuita API |
| Perplexity Sonar / API de busca / Pesquisa Profunda | Perplexity chave, ou chave OpenRouter como recurso de replio do Sonar | Pague conforme a vida |
| Web busca | Chave Brave Search | 2.000 consultas gratuitas/mês |

### macOS Keychain (opcional)

No macOS você pode armazenar chaves no Keychain do sistema em vez de um arquivo `.env` . A habilidade as detecta automaticamente como a fonte de menor prioridade — `.env` arquivos e o ambiente de processos ainda vencem em colisão.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Os itens são armazenados sob `last30days-<KEY>` de nome de serviço para o usuário atual. Em plataformas que não são Darwin, o loader é um no-op, então não há mudança de comportamento para usuários Linux/Windows .

Já tem chaves sob nomes de serviço Keychain diferentes? Defina o mapeamento de `LAST30DAYS_KEYCHAIN_ALIASES` não secreto descrito no [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) em vez de copiar segredos.

Veja [CONFIGURATION.md](CONFIGURATION.md) para a matriz completa de chaves por fonte, prioridade do provedor de raciocínio e prioridade backend de busca web.

## Configuração

Duas coisas que você provavelmente vai querer saber no primeiro dia:

**Onde os arquivos de pesquisa são salvos.** `LAST30DAYS_MEMORY_DIR` padrão é `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Sobrescreva definindo essa var ambientalmente para qualquer caminho no seu shell, ou `--save-dir <path>` por execução. Use `--output <file>` quando precisar do resultado renderizado em um caminho exato, usando o formato selecionado por `--emit`. Use `--save-suffix=<name>` para manter múltiplas variações do mesmo tópico separadas (por exemplo, por cliente). Cada execução `--save-dir` produz `<slug>-raw[-suffix].md`. Execute `python3 skills/last30days/scripts/last30days.py --preflight` para revisar as escritas planejadas antes de uma execução de pesquisa.

**Saída estruturada para agentes e fluxos de trabalho.** Peça `/last30days` JSON legíveis por máquina para receber o perfil estável e versionado do agente. Para uso direto do motor em scripts ou desenvolvimento, execute `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; adicione `--json-profile=raw` apenas quando precisar do despejo interno de `Report` sem versão. Veja o [JSON export field reference and versioning policy](docs/reference/json-export.md).

**Descoberta sem tópico.** Peça `/last30days what's trending in AI agents?` para obter um briefing de descoberta ranqueado em vez de pesquisar um tema que você já conhece – em um host agente, isso executa o protocolo de três comandos avaliados pelo host (o modelo nomeia tópicos, filtra lixo, avalia a dignidade e escreve os ângulos de conteúdo). Para uso direto do motor em scripts ou cron, execute `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (one-shot: nomes determinísticos de tópicos, sem ângulos); adicione `--emit=json` para o contrato de descoberta versionado. A descoberta é mutuamente exclusiva com um tópico posicional e `--drill`.

**Monitoramento de tendências entre execuções.** O modo padrão produz um novo snapshot de markdown por execução. Para acumular descobertas ao longo do tempo, adicione `--store` para persistir em um banco de dados SQLite, depois use [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) para execuções agendadas (com entrega opcional por Slack / webhook em novas descobertas) e [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) para digestos diários/semanais. O padrão completo de cadência está em [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Uma biblioteca de pesquisa para assinantes.** Peça `/last30days` para construir seu feed de biblioteca, ou use- `python3 skills/last30days/scripts/last30days.py library feed` diretamente para scripts e desenvolvimento. Ele transforma briefs salvos em `index.html`, um `feed.xml`local do Atom e páginas breves legíveis. Adicione `--publish` apenas quando quiser que o índice HTML e as páginas de resumos sejam hospedados; a publicação é explícita com opt-in e pública por padrão. Para tornar o feed do Atom assinável, hospede o diretório de saída gerado em um host estático, como GitHub Pages.

**Pesquise tudo o que você pesquisou.** Pergunte `/last30days search my library for MCP servers` ou `/last30days have I researched MCP servers before?`. Para uso direto do motor, execute `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. A busca é offline e determinística: ela indexa incrementalmente os mesmos briefs salvos usados pelo feed da biblioteca, mescla os avistamentos correspondentes a cada rodada da loja e agrupa os resultados por tópico e data. Execuções novas também apresentam uma seção compacta **Da sua biblioteca** quando pesquisas anteriores sobrepõem ao tema atual; configure `LAST30DAYS_LIBRARY_CONTEXT=off` para desabilitar esse contexto passivo.

Scripts wrapper por cliente, subreddits personalizados de categorias peer e o canal beta experimental para personalizações em andamento também são documentados em [CONFIGURATION.md](CONFIGURATION.md).

## Vitrine: feeds de pesquisa comunitária

Publicou uma atualização recorrente de IA, observação de mercado ou uma obsessão maravilhosamente restrita com os últimos 30 dias? Compartilhe a URL da biblioteca pública — ou a URL do Atom após hospedar `feed.xml` em um host estático — em [the community showcase thread](https://github.com/mvanhorn/last30days-skill/issues/532). Os feeds da comunidade estarão linkados aqui conforme seus proprietários os enviarem; o tópico é o ponto de coleta enquanto isso.

## Como funciona

1. **Você digita um tópico.** Pessoa, empresa, produto, tecnologia, "X vs Y." Qualquer coisa.
2. **O agente resolve quem importa.** Encontra X usuários (incluindo fundadores), repositórios GitHub , subreddits TikTok hashtags YouTube canais. Para "Kanye West", ele conhece r/hiphopheads, @kanyeweste "bully review" no YouTube. Para "OpenClaw", resolve openclaw/openclaw no GitHub e busca contagens de estrelas ao vivo.
3. **Todas as fontes buscadas em paralelo.** Expansão multi-consulta. Resultados avaliados por engajamento, relevância e frescura.
4. **A profundidade que ninguém mais tem.** Transcrições YouTube completas de vídeos de reação. Comentários Reddit principais com contagem de votos positivos. TikTok legendas. Polymarket probabilidades. Não só títulos e links.
5. **Mesma história, fundida.** O Wireless Festival anunciou em Reddit, discutido no X, preços dos ingressos em TikTok = um cluster, não três itens separados.
6. **Sintetizado em um único resumo.** Baseado em dados específicos. Citado por fonte. Classificado pelo que as pessoas realmente se envolvem. Não "aqui está o que eu encontrei." É "aqui está o que importa."
7. **Então ele se torna seu especialista.** Após uma única tentativa, sua sessão Claude sabe tudo o que a comunidade sabe. Faça perguntas de acompanhamento. Faça com que ele escreva prompts, relabore e-mails, planeje viagens, arquitetede sistemas – tudo baseado no que é real agora.

## O que as pessoas estão dizendo

> "Encontrei uma habilidade Claude Code que pesquisa qualquer tema em Reddit, X, YouTubee HN dos últimos 30 dias. Depois escreve os prompts para você. Tenho pesquisado manualmente Reddit e X pesquisas antes de cada conteúdo que escrevo. Aba por aba. Thread por thread. Essa é a parte que leva 90 minutos. Isso elimina isso." -@itsjasonai

> "Essa habilidade substituiu todo o meu fluxo de trabalho de pesquisa. Você dá um tema, ele raspa Reddit, X, e a web para encontrar o que as pessoas realmente estão falando. Não posts antigos de blog. Conversas reais dos últimos 30 dias." -@itswilsoncharles

> "5 dos 10 repositórios em alta no GitHub hoje são Claude ferramentas. #1: Mvanhorn/last30days- habilidade" -@yieldhunter95

## Código aberto

Licença do MIT. Sem rastreamento. Sem análises. Sua pesquisa fica na sua máquina. 2.700+ testes.

Construído com Python 3.12+, YT-DLP, Node.js (cliente Bird fornecido para X busca) e ScrapeCreators API. arquitetura do motor v3 pela [@j-sperling](https://github.com/j-sperling).

Veja [CONTRIBUTING.md](CONTRIBUTING.md) para abrir uma PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) para a lista completa de colaboradores da comunidade e [CHANGELOG.md](CHANGELOG.md) para o histórico de versões.

## História da Star

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
