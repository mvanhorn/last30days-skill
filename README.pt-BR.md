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

**Um mecanismo de pesquisa liderado por agentes de IA pontuado por votos positivos, curtidas e dinheiro real - não por editores.**

Este README rastreia o pipeline v3 atual. A especificação de habilidade de tempo de execução reside em [skills/last30days/SKILL.md](skills/last30days/SKILL.md), que é a fonte da verdade para o comando e comportamento de configuração mais recentes.

**Claude Code (recomendado — atualizações automáticas via marketplace):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI ou qualquer um dos mais de 50 [hosts do Agent Skills](https://agentskills.io):**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` é instalado globalmente para seu usuário, disponível em todos os projetos. Coloque-o no escopo por projeto.)

Mais opções de instalação (claude.ai web, OpenClaw, manual) na seção [Install](#instalação) abaixo.

Configuração zero. Reddit, HN, Polymarket e GitHub funcionam imediatamente. Execute-o uma vez e o assistente de configuração desbloqueia X, YouTube, TikTok, arXiv, Techmeme e muito mais em 30 segundos.

---

Votos positivos do Reddit. X gosta. Transcrições do YouTube. Engajamento do TikTok. Probabilidades do Polymarket apoiadas por dinheiro real e informações privilegiadas. São milhões de pessoas votando com sua atenção e suas carteiras todos os dias. /last30days pesquisa tudo em paralelo, pontua de acordo com o que as pessoas reais realmente interagem e um juiz agente de IA sintetiza tudo em um resumo.

O Google agrega editores. /last30days pesquisa pessoas.

Você não pode obter essa pesquisa em nenhum outro lugar porque nenhuma IA tem acesso a tudo isso. A pesquisa do Google não aborda comentários do Reddit ou postagens X. ChatGPT tem acordo com Reddit, mas não consegue pesquisar X ou TikTok. Gemini tem YouTube, mas não Reddit. Claude não tem nenhum deles nativamente. Cada plataforma é um jardim murado com sua própria API, seus próprios tokens, sua própria autenticação. Mas você pode trazer suas próprias chaves e sessões do navegador e, de repente, um agente de IA pode pesquisar todas elas de uma vez, compará-las entre si e dizer o que realmente importa.

Esse é o desbloqueio. Nenhum mecanismo de pesquisa melhor. Uma dúzia de plataformas desconectadas, interligadas por um agente.

```
/last30days Peter Steinberger
```

Você tem uma reunião amanhã. Você os pesquisa no Google. Você obtém o LinkedIn de 2023. /last30days mostra o que eles realmente estão fazendo este mês: juntou-se à OpenAI para trabalhar no Codex, lutando contra a proibição da Anthropic de agentes terceirizados, enviando 23 PRs com taxa de mesclagem de 85%, construindo "LobsterOS" para controle de agente entre dispositivos e r/ClaudeCode atingiu 569 votos positivos debatendo se ele é um herói ou "insuportável". Espalhados por postagens X, tópicos do Reddit, transcrições do YouTube e commits do GitHub. Nada disso estava no Google.

## Por que isso existe

Eu o construí para acompanhar a IA. Tudo muda todos os dias e os nerds do Reddit e do X estão sempre por dentro disso. Eu precisava de instruções melhores, e os dados de treinamento estavam sempre meses atrás do que a comunidade já havia descoberto.

Mas isso se transformou em algo maior. Agora eu o executo antes de uma ligação de vendas para saber a verdade dos últimos 30 dias sobre um negócio. Antes de uma reunião, para ler os tweets recentes e as transcrições de podcast de alguém. Antes de uma viagem à Disney World para saber quais atrações estão fechadas e o que a comunidade diz sobre o Genie+. Antes de construir qualquer coisa, quero saber quais problemas as pessoas estão realmente enfrentando.

Se você estiver se reunindo com um CEO, leu todos os tweets e transcrições do YouTube dos últimos 30 dias? Eu tenho.

## Fontes, pontuadas pelo povo

| Fonte | O que as pessoas dizem para você |
|--------|--------------------------|
| **Reddit** | A tomada não filtrada. Principais comentários com contagens reais de votos positivos, gratuitos e sem chave de API. As verdadeiras opiniões que o Google enterra. |
| **X/Twitter** | A tomada quente, o tópico especializado, a reação de ruptura. Primeiro a saber, primeiro a discutir. |
| **YouTube** | O mergulho profundo de 45 minutos. As transcrições completas procuraram as 5 frases citáveis ​​​​que importam. |
| **TikTok** | O criador alcançando 3,6 milhões de pessoas com um take que você nunca encontrará no Google. |
| **Instagram Reels** | A perspectiva do influenciador com transcrições de palavras faladas. O sinal da cultura visual. |
| **Notícias sobre hackers** | O consenso do desenvolvedor. 825 pontos, 899 comentários. Onde os técnicos realmente discutem. |
| **Polymarket** | Não opiniões. Chances. Apoiado em dinheiro real. 96% de confiança nas vendas de álbuns. 4% em uma aquisição. |
| **GitHub** | Para pessoas: velocidade de relações públicas, principais repositórios por estrelas, notas de lançamento. Para tópicos: questões e discussões. |
| **Digg** | Clusters de histórias selecionados da tabela de classificação AI 1000 do Digg (cerca de 1.000 contas de IA de alto sinal no X), com cotações inline atribuíveis (sem necessidade de autenticação X). Ativado automaticamente quando `digg-pp-cli` está em PATH. |
| **arXiv** | Os jornais por trás do hype. Nova pesquisa na janela, gratuita, sem chave de API. Ativado automaticamente quando `arxiv-pp-cli` está em PATH (a configuração na primeira execução o instala). |
| **Tecmeme** | A camada editorial de notícias de tecnologia, com janela de data de 30 dias. Gratuito, sem chave de API. Ativado automaticamente quando `techmeme-pp-cli` está em PATH (a configuração na primeira execução o instala). |
| **LinkedIn** | O sinal profissional. Postagens e artigos, com artigos considerados de alto sinal. |
| **StockTwits** | Sentimento do comerciante. Ativa automaticamente quando o seu tópico é um ticker ou criptografia. |
| **Tópicos** | A camada de texto pós-Twitter. Conversas de criadores e marcas. |
| **Pinterest** | Descoberta visual. Pins, salvamentos e comentários sobre produtos e ideias. |
| **Xiaohongshu (VERMELHO)** | Sinais de estilo de vida, produto e criador chineses. Solicitado explicitamente com `--search xhs` quando um plug-in de navegador x-mcp conectado ou serviço `xiaohongshu-mcp` está sendo executado localmente. |
| **Céu Azul** | A camada social descentralizada. Postagens do Protocolo AT da migração pós-Twitter. |
| **Perplexidade** | Síntese do Grounded Sonar, linhas brutas da API de pesquisa e pesquisa profunda. |
| **Web** | A cobertura editorial, as comparações do blog. Um sinal entre muitos, não o único. |

Os contribuidores da comunidade continuam adicionando mais. Truth Social e outras fontes de nicho estão no motor com mais a caminho.

Um tópico do Reddit com 1.500 votos positivos é um sinal mais forte do que uma postagem de blog que ninguém leu. Um TikTok com 3,6 milhões de visualizações conta mais sobre o que é culturalmente relevante do que um comunicado à imprensa. As probabilidades do Polymarket apoiadas por um volume de US$ 66 mil são mais difíceis de argumentar do que a suposição de um especialista.

A síntese classifica aquilo com que as pessoas reais realmente se engajaram. Relevância social, não relevância de SEO.

## Para que as pessoas realmente o usam

**Antes de uma reunião.** `/last30days Peter Steinberger` - juntou-se à equipe Codex da OpenAI, lutando contra a proibição da Anthropic de agentes terceirizados, 23 PRs se fundiram a uma taxa de mesclagem de 85% no GitHub, construindo o LobsterOS para controle de agente entre dispositivos. r/ClaudeCode: "Desde o lançamento do OpenClaw, era amplamente conhecido que se você executá-lo por meio de qualquer coisa que não fosse a API, você acabaria sendo banido" (227 votos positivos). Isso não está no LinkedIn.

**Para ler os sinais de contratação.** `/last30days Listen Labs --hiring-signals` - as páginas atuais de empregos e carreiras tornam-se evidências citadas de mudanças de foco: contratação para segurança empresarial, sucesso do cliente, infraestrutura ou expansão de produtos. O relatório diz o que a contratação parece sinalizar, e não o que o roteiro irá fornecer.

**Para encontrar o tópico antes que ele atinja o pico.** Pergunte a `/last30days what's exploding in AI agents?` e a habilidade muda para o modo de descoberta: o mecanismo varre as listagens de categorias do Reddit, as principais/melhores histórias do Hacker News, o feed AI 1000 do Digg e X quando autenticado; seu agente julga as nomeações (nomes, filtragem de lixo eletrônico, valor do conteúdo) e escreve ângulos de podcast/artigo X; então você obtém de 5 a 10 tópicos classificados por velocidade. Cada resultado inclui números de fontes cruzadas, um rótulo de impulso e um acompanhamento `/last30days "<topic>"` pronto para execução.

**Quando algo cai.** `/last30days Kanye West` - O Reino Unido bloqueou seu visto, o Wireless Festival foi cancelado, os patrocinadores fugiram. Mas BULLY estreou em segundo lugar na Billboard. Fantano voltou de seu "Yay sabático" para revisá-lo (653 mil visualizações). SoFi Homecoming trouxe Lauryn Hill e Travis Scott para 44 músicas. Polymarket: "Kanye vai twittar de novo?" 86% Sim. 23 tópicos do Reddit, 17 vídeos do YouTube, 86 mil votos positivos.

**Para comparar ferramentas.** `/last30days OpenClaw vs Hermes vs Paperclip` - "Estes não são concorrentes, são camadas." OpenClaw é o executor (351 mil estrelas do GitHub, ao vivo), Hermes é o cérebro que se aprimora (31 mil estrelas), Paperclip é o organograma (49 mil estrelas). Contagens de estrelas extraídas ao vivo da API do GitHub, e não de postagens de blog obsoletas. Tabela lado a lado com arquitetura, memória, segurança, best-for. Por @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard."

**Para entender o mundo.** `/last30days Iran vs USA` - Dia 38 da guerra. O prazo de terça-feira de Trump para o Irã reabrir o Estreito de Ormuz. Dois aviões de guerra dos EUA abatidos. Petróleo a US$ 126/barril. A AIE chamou-a de “a maior interrupção no fornecimento na história do mercado global de petróleo”. Polymarket: cessar-fogo até 31 de dezembro em 74%. 27 postagens X, 10 vídeos no YouTube, 20 mercados de previsão.

**Antes de uma viagem.** `/last30days Universal Epic Universe` - Ampliação já em construção. Licença do "Projeto 680" arquivada. Espetáculo de fogos de artifício confirmado pela infraestrutura, mas sem aviso prévio. Tempos de espera: Mine-Cart Madness com média de 148 minutos. Ainda não há passe anual e os moradores locais estão frustrados. Stardust Racers será reformado até 5 de abril.

**Para aprender algo rápido.** `/last30days Nano Banana Pro prompting` - Prompts estruturados em JSON estão substituindo a sopa de tags. O formato aninhado do @pictsbyai evita "sangramento de conceito". O fluxo de trabalho que prioriza a edição supera a regeneração. Em seguida, ele escreve um prompt de produção usando exatamente o que a comunidade disse que funciona.

## O que há de novo

Desde o anúncio da v3.3 em maio, até a v3.11.1 (julho de 2026): 175 PRs mesclados - 122 deles de 52 colaboradores da comunidade - em 15 versões. Isto é o que pousou.

### Primeira classe no OpenAI Codex

/last30days agora é um plugin nativo do Codex com configuração guiada - não uma porta, um cidadão de primeira classe. Citações com reconhecimento de renderizador significam que a saída do Codex é lida como um resumo em vez de uma sopa de URL (# 694), e o mesmo mecanismo é executado em hosts Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClaw e mais de 50 Agent Skills. Manifesto do plugin Codex por [@rfoust](https://github.com/rfoust) (#686), correção de autenticação do Codex por [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmeme e Digg - gratuitos, sem chaves de API

arXiv traz os documentos por trás do hype e Techmeme traz a camada editorial de notícias técnicas - grátis, sem chaves e com configuração de primeira execução que instala suas CLIs para que sejam ativadas automaticamente (# 709). Os clusters de histórias AI 1000 do Digg chegam sem autenticação X da mesma maneira - a configuração instala a CLI gratuita do Digg para você (#590). A Trustpilot oferece opt-in para pesquisas de marcas de consumo.

### Reddit gratuito aumentou pontuações reais e comentários principais

A API .json pública do Reddit morreu; o caminho livre voltou mais forte. Keyless RSS + scraping de Shreddit (#457), descoberta de subreddit dedicado com contagens reais de votos positivos via Arctic-Shift (#696) e um piso de relevância para que uma postagem viral fora do tópico não possa sequestrar seu briefing (#488, obrigado [@rzachsmith](https://github.com/rzachsmith)). Nenhuma chave de API. Pontuações reais. Principais comentários incluídos.

### Os melhores comentários em cada briefing

Os comentários agora são uma camada padrão em todas as fontes: comentários do Instagram com diversidade baseada em classificação para que cinco tomadas interessantes não venham todas de uma postagem (# 751), comentários do YouTube mais um backup de transcrição do ScrapeCreators para quando yt-dlp for eliminado (# 637) e comentários votados pelo público ponderados em Melhores tomadas para que as falas mais engraçadas da comunidade sobrevivam à pontuação (# 592, # 608).

### Um comando médico

Solicite uma verificação de integridade e o médico executará todas as fontes e, em seguida, prescreverá as correções exatas - qual chave está faltando, qual CLI está fora do PATH, qual cookie expirou (#753). Chega de adivinhar por que X voltou magro.

### Pesquisa X, reconstruída

O pipeline X passou por uma revisão completa: pistas FROM e ABOUT para que as próprias postagens de uma pessoa e a conversa sobre elas sejam classificadas (nº 610), desambiguação de subconsulta com reconhecimento de pessoa (nº 611), base de autoria primária com classificação de sinal de interação (nº 613) e uma única fonte X com failover de back-end automático (nº 622). Além de um `--diagnose` honesto que realmente testa a autenticação (# 609).

### Mais fontes unidas

LinkedIn via ScrapeCreators, com artigos de alto sinal ([@ravstr](https://github.com/ravstr), #702). StockTwits é ativado automaticamente para tópicos de ticker e criptografia ([@wtiwana](https://github.com/wtiwana), #658). A Perplexity aumentou os modos API diretos e Deep Research assíncrona ([@sk-holmes](https://github.com/sk-holmes), #629).

### Fortalecido pela comunidade

A onda de segurança foi quase inteiramente trabalho comunitário: correções de XSS armazenado no renderizador HTML ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), arquivos temporários de cookie bloqueados, CI reforçado para cadeia de suprimentos com OpenSSF Scorecard e atestado de proveniência de construção ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), verificações Semgrep e OSV-Scanner, além de uma porta de revisão de dependência de PR ([@23241a6749](https://github.com/23241a6749)), um piso de cobertura de teste introduzido em 60% e desde então aumentado para 84% ([@gourab5139014](https://github.com/gourab5139014)) e uma verificação de segurança Hermes limpa de todas as descobertas CRÍTICAS (#768).

### Alcança mais longe

Idiomas hebraico e não latino ([@dudyme](https://github.com/dudyme)). Tokenização compatível com CJK para fontes chinesas ([@An-idd](https://github.com/An-idd)). Uma onda de compatibilidade do Windows. Extração de cookies em toda a família Chromium - Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) - além de fontes de credenciais macOS Keychain e Linux pass(1). Lookback histórico de `--as-of` ([@chiyi-creator](https://github.com/chiyi-creator)). Python 3.12 provisionado automaticamente via uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` para leitura das páginas de empregos de uma empresa. Deltas da lista de observação entre execuções.

### Ainda na caixa da v3

As bases da v3 ainda estão aqui: o cérebro de pré-pesquisa que resolve os identificadores, subreddits e hashtags corretos antes que uma única chamada de API seja disparada (construída por [@j-sperling](https://github.com/j-sperling)); Best Takes pontua em humor e viralidade junto com relevância; fusão de cluster de origem cruzada; comparações de passagem única (“CLI vs MCP” em 3 minutos, não 12); comparações `--competitors` descobertas automaticamente; Modo pessoal do GitHub (`--github-user=steipete`); Modo ELI5 (“eli5 on” após qualquer execução); e resumos HTML independentes e compartilháveis ​​(`--emit=html`). Os botões de configuração ficam em [CONFIGURATION.md](CONFIGURATION.md).

## Instalar

| Superfície | Instalar | Atualizações |
|---------|---------|---------|
| **Claude Code** (recomendado) | `/plugin marketplace add mvanhorn/last30days-skill` | Automático via marketplace ou `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` e depois `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI ou qualquer um dos mais de 50 [hosts do Agent Skills](https://agentskills.io)** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [Baixe `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) e carregue via claude.ai > Personalizar > Habilidades > + > Criar habilidade > Carregar uma habilidade | Baixe novamente e carregue novamente |
| **Claude Desktop** | [Baixe o `.mcpb` para sua plataforma](https://github.com/mvanhorn/last30days-skill/releases/latest) e arraste para Configurações > Extensões | Baixe novamente e arraste o novo pacote para |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recomendado)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Recomendado porque o mercado Claude Code cuida das atualizações para você – o cache do plug-in é versionado e atualizado automaticamente quando uma nova versão é publicada. Execute `claude plugin update last30days@last30days-skill` para forçar uma verificação.

Se você preferir usar o caminho de instalação de habilidades do agente no Claude Code, isso também é compatível:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

O plugin nativo e a instalação do `npx skills` podem coexistir. Observe que o Claude Code não desduplica os métodos de instalação: se você tiver o plugin do Marketplace e a cópia `npx skills` ativos, `/last30days` mostrará duas entradas. Use um método de instalação por máquina.

### Grok (xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) instala last30days como um plugin nativo. A instalação direta rastreia o repositório:

```bash
grok plugin install mvanhorn/last30days-skill
```

Ou adicione este repositório como fonte do marketplace e instale pelo nome do plugin:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Adicione `--trust` para ignorar a confirmação de instalação. Atualize com `grok plugin update last30days`. Grok também lê os manifestos do Claude Code para compatibilidade; o par nativo `.grok-plugin/` é a faixa de primeira classe (e para onde aponta uma listagem oficial [xAI marketplace](https://github.com/xai-org/plugin-marketplace)). `npx skills add` continua sendo um substituto válido entre hosts.

### Codex, Cursor, Copilot, Gemini CLI e outros hosts de Agent Skills

Instale por meio da CLI aberta [Agent Skills](https://agentskills.io) – suporta mais de 50 chicotes, incluindo `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose` e mais (lista completa no [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

O sinalizador `-g` (global) é instalado em seu diretório de usuário para que a habilidade esteja disponível em todos os projetos. Sem `-g`, `npx skills` instala o projeto localmente em `./.skills/` (confirmado com o repositório). Para uma ferramenta de pesquisa do mundo, global é o que você deseja.

O desktop Codex e outros hosts em modo de pasta podem funcionar em pastas comuns, bem como em repositórios Git. Antes da primeira pesquisa, peça ao agente host para executar o `scripts/last30days.py --preflight` incluído no diretório de habilidades carregado; em uma verificação de origem, o comando equivalente é `python3 skills/last30days/scripts/last30days.py --preflight`. Ele mostra a fonte de configuração, o plano de cookies do navegador, gravações planejadas, comandos opcionais e configuração de projeto ignorado sem ler cookies, gravar arquivos ou executar pesquisas.

Por padrão, isso é instalado para qualquer chicote que o `npx skills` detectar. Para atingir um específico (ou vários):

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Atualize mais tarde com:

```bash
npx skills update last30days -g
```

Ou atualize tudo o que você instalou globalmente via `npx skills`:

```bash
npx skills update -g
```

Liste e remova com `npx skills list -g` e `npx skills remove last30days -g`.

### claude.ai (web)

1. [Baixe `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) da versão mais recente
2. Vá para [claude.ai > Personalizar > Skills](https://claude.ai/customize/skills)
3. Clique no botão `+` no painel Habilidades > clique em `Create skill` > `Upload a skill` e navegue/solte o arquivo

Habilite "Execução de código e criação de arquivo" em Recursos primeiro - as habilidades não serão executadas sem ele.

### Claude Desktop

Claude Desktop instala `/last30days` como um servidor MCP por meio de um pacote `.mcpb` (um pacote Model Context Protocol de um clique).

1. Vá para [lançamento mais recente](https://github.com/mvanhorn/last30days-skill/releases/latest) e baixe o `.mcpb` para sua plataforma:
- macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
- macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
-Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Abra o Claude Desktop, vá para Configurações > Extensões e arraste o arquivo.
3. Quando solicitado, cole as chaves de API das fontes que deseja ativar. Cada campo é opcional – o mecanismo passa para o modo somente web se você ignorar todos eles. As chaves são armazenadas nas chaves do seu sistema operacional.
4. Reinicie o Claude Desktop. Peça ao Claude para “pesquisar Peter Steinberger” ou qualquer assunto e ele chamará a ferramenta `research`.

**Requisito de host:** Python 3.12+ em PATH. O pacote inclui o código-fonte do mecanismo, mas usa seu interpretador Python local. Instale em [python.org](https://www.python.org/downloads/) no Windows; O macOS e a maioria das distribuições Linux fornecem uma versão compatível.

**As chaves não são sincronizadas com a habilidade Código.** Claude Desktop e Claude Code mantêm armazenamentos de credenciais separados por design. Se você já configurou `~/.config/last30days/.env` para a habilidade Código, você inserirá novamente as mesmas chaves aqui uma vez.

O suporte do Windows é adiado até que os pontos de entrada do manifesto por plataforma sejam resolvidos; acompanhar em uma edição de acompanhamento.

### OpenClaw

```bash
clawhub install last30days-official
```

Para fluxos de trabalho de ação X/Twitter fora da pesquisa `/last30days`, como postagem
tweets ou respostas, exportação de seguidores, manipulação de mídia, monitores e brindes
desenha, use [TweetClaw](https://github.com/Xquik-dev/tweetclaw) como companheiro
Plug-in OpenClaw. TweetClaw é mantido pela Xquik-dev e está listado apenas como um
caminho complementar opcional, não uma dependência ou endosso de últimos 30 dias.

### Manual (desenvolvedor)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

O link simbólico mantém a instalação sincronizada com sua árvore de trabalho enquanto você edita - não é necessária nova cópia. Para `claude.ai`, crie o arquivo `.skill` a partir da origem: `bash skills/last30days/scripts/build-skill.sh` produz `dist/last30days.skill`.

Reddit (com comentários), Hacker News, Polymarket e GitHub funcionam imediatamente. Configuração nula. Execute `/last30days` uma vez e o assistente de configuração desbloqueia mais fontes em 30 segundos, incluindo as CLIs gratuitas arXiv e Techmeme.

## Traga suas próprias chaves

Essas plataformas não se relacionam entre si. X não sabe o que o Reddit pensa. O YouTube não vê o TikTok. Mas você pode trazer suas próprias chaves de API e tokens de navegador e, de repente, terá acesso a todos eles de uma vez.

| Fontes | O que você precisa | Custo |
|---------|---------------|------|
| Reddit (com comentários) + HN + Polymarket + GitHub + StockTwits | Nada | Grátis |
| arXiv + Techmeme | CLIs gratuitas, instaladas automaticamente pela configuração inicial | Grátis |
| X/Twitter | Faça login em x.com em qualquer navegador ou defina `XQUIK_API_KEY` / `XAI_API_KEY` | Os cookies do navegador são gratuitos; as chaves são específicas do provedor |
| YouTube | `brew install yt-dlp` | Grátis |
| Céu Azul | Senha do aplicativo de bsky.app | Grátis |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + comentários no YouTube | Chave ScrapeCreators | 10.000 chamadas gratuitas e PAYG |
| Xiaohongshu (VERMELHO) | Execute um plug-in de navegador x-mcp conectado ou serviço `xiaohongshu-mcp` e opte por `--search xhs` por execução ou `INCLUDE_SOURCES=xiaohongshu` em `.env`; last30days sonda automaticamente `http://localhost:18060` e depois `http://host.docker.internal:18060` ou use `XIAOHONGSHU_API_BASE` para um URL personalizado | Nenhuma chave de API dos últimos 30 dias; depende do serviço de sessão do navegador local |
| DripStack (boletins financeiros premium) | Aceitação: `--search dripstack` por execução ou `INCLUDE_SOURCES=dripstack` em `.env` | Sem chave; API de pesquisa pública gratuita |
| Sonar Perplexity / API de pesquisa / Pesquisa profunda | Chave de perplexidade ou chave OpenRouter como substituto do Sonar | Pague conforme usar |
| Pesquisa na web | Chave de pesquisa corajosa | 2.000 consultas gratuitas/mês |

### Chaveiro macOS (opcional)

No macOS, você pode armazenar chaves nas chaves do sistema em vez de um arquivo `.env`. A habilidade os seleciona automaticamente como a fonte de menor prioridade – os arquivos `.env` e o ambiente do processo ainda vencem na colisão.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Os itens são armazenados sob o nome de serviço `last30days-<KEY>` para o usuário atual. Em plataformas não Darwin, o carregador não funciona, portanto não há mudança de comportamento para usuários de Linux/Windows.

Já possui chaves com nomes de serviço de chaveiro diferentes? Defina o mapeamento `LAST30DAYS_KEYCHAIN_ALIASES` não secreto descrito em [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) em vez de copiar segredos.

Consulte [CONFIGURATION.md](CONFIGURATION.md) para obter a matriz de chaves completa por fonte, prioridade do provedor de raciocínio e prioridade de back-end de pesquisa na web.

## Configuração

Duas coisas que você provavelmente vai querer saber no primeiro dia:

**Onde os arquivos de pesquisa são salvos.** O padrão de `LAST30DAYS_MEMORY_DIR` é `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Substitua definindo esse env var para qualquer caminho em seu shell ou `--save-dir <path>` por execução. Use `--output <file>` quando precisar do resultado renderizado em um caminho exato, usando o formato selecionado por `--emit`. Use `--save-suffix=<name>` para manter separadas diversas variações do mesmo tópico (por exemplo, por cliente). Cada execução de `--save-dir` produz `<slug>-raw[-suffix].md`. Execute `python3 skills/last30days/scripts/last30days.py --preflight` para revisar as gravações planejadas antes de uma execução de pesquisa.

**Saída estruturada para agentes e fluxos de trabalho.** Solicite ao `/last30days` JSON legível por máquina para receber o perfil de agente estável e com versão. Para uso direto do mecanismo em scripts ou desenvolvimento, execute `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; adicione `--json-profile=raw` somente quando precisar do dump `Report` interno não versionado. Consulte a [referência do campo de exportação JSON e política de controle de versão](docs/reference/json-export.md).

**Descoberta sem tópico.** Peça ao `/last30days what's trending in AI agents?` para obter um resumo de descoberta classificado em vez de pesquisar um tópico que você já conhece - em um host de agente, isso executa o protocolo julgado por host de três comandos (o modelo nomeia tópicos, filtra lixo, pontua valor e escreve os ângulos de conteúdo). Para uso direto do mecanismo em scripts ou cron, execute `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (one-shot: nomes de tópicos determinísticos, sem ângulos); adicione `--emit=json` para o contrato de descoberta versionado. A descoberta é mutuamente exclusiva com um tópico posicional e `--drill`.

**Monitoramento de tendências entre execuções.** O modo padrão produz um novo instantâneo de redução por execução. Para acumular descobertas ao longo do tempo, adicione `--store` para persistir em um banco de dados SQLite e, em seguida, use [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) para execuções agendadas (com entrega opcional de Slack/webhook em novas descobertas) e [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) para resumos diários/semanais. O padrão de cadência completo está em [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Uma biblioteca de pesquisa que pode ser assinada.** Peça ao `/last30days` para criar o feed da sua biblioteca ou use `python3 skills/last30days/scripts/last30days.py library feed` diretamente para scripts e desenvolvimento. Ele transforma resumos salvos em `index.html`, um Atom local `feed.xml` e páginas de resumos legíveis. Adicione `--publish` somente quando desejar que o índice HTML e as páginas resumidas sejam hospedados; a publicação é de aceitação explícita e pública por padrão. Para tornar o feed Atom subscrito, hospede o diretório de saída gerado em um host estático, como GitHub Pages.

**Pesquise tudo o que você pesquisou.** Pergunte a `/last30days search my library for MCP servers` ou `/last30days have I researched MCP servers before?`. Para uso direto do motor, execute `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. A pesquisa é off-line e determinística: ela indexa de forma incremental os mesmos resumos salvos usados ​​pelo feed da biblioteca, mescla as visualizações de lojas correspondentes por execução e agrupa os resultados por tópico e data. Novas versões também aparecem em uma seção compacta **Da sua biblioteca** quando pesquisas anteriores se sobrepõem ao tópico atual; configure `LAST30DAYS_LIBRARY_CONTEXT=off` para desabilitar esse contexto passivo.

Scripts de wrapper por cliente, subreddits de categoria personalizada e o canal beta experimental para personalizações em andamento também estão documentados em [CONFIGURATION.md](CONFIGURATION.md).

## Showcase: feeds de pesquisa da comunidade

Publicou uma atualização recorrente de IA, observação do mercado ou uma obsessão maravilhosamente restrita com os últimos 30 dias? Compartilhe o URL da biblioteca pública — ou o URL Atom após hospedar `feed.xml` em um host estático — no [thread de demonstração da comunidade](https://github.com/mvanhorn/last30days-skill/issues/532). Os feeds da comunidade serão vinculados aqui à medida que seus proprietários os enviarem; o tópico é o ponto de coleta enquanto isso.

## Como funciona

1. **Você digita um tópico.** Pessoa, empresa, produto, tecnologia, "X vs Y". Qualquer coisa.
2. **O agente decide quem é importante.** Encontra X identificadores (incluindo fundadores), repositórios GitHub, subreddits, hashtags TikTok, canais do YouTube. Para "Kanye West" conhece r/hiphopheads, @kanyewest e "bully review" no YouTube. Para "OpenClaw", ele resolve openclaw/openclaw no GitHub e busca contagens de estrelas ao vivo.
3. **Todas as fontes pesquisadas em paralelo.** Expansão multiconsulta. Resultados pontuados por engajamento, relevância, atualização.
4. **A profundidade que ninguém mais tem.** Transcrições completas do YouTube de vídeos de reação. Principais comentários do Reddit com contagens de votos positivos. Legendas do TikTok. Probabilidades de polimercado. Não apenas títulos e links.
5. **Mesma história, mesclada.** Wireless Festival anunciado no Reddit, discutido no X, preços dos ingressos no TikTok = um cluster, não três itens separados.
6. **Sintetizado em um resumo.** Baseado em dados específicos. Citado pela fonte. Classificado de acordo com o que as pessoas realmente interagem. Não "aqui está o que encontrei". É "aqui está o que importa".
7. **Então ele se torna seu especialista.** Após uma execução, sua sessão com Claude sabe tudo o que a comunidade sabe. Faça perguntas de acompanhamento. Faça-o escrever avisos, redigir e-mails, planejar viagens, arquitetar sistemas - tudo baseado no que é real no momento.

## O que as pessoas estão dizendo

> "Encontrei uma habilidade do Claude Code que pesquisa qualquer tópico no Reddit, X, YouTube e HN dos últimos 30 dias. Em seguida, escreve as instruções para você. Tenho pesquisado manualmente no Reddit e no X antes de cada conteúdo que escrevo. Guia por guia. Tópico por tópico. Essa é a parte que leva 90 minutos. Isso a elimina." -@itsjasonai

> "Essa habilidade substituiu todo o meu fluxo de trabalho de pesquisa. Você dá um tópico a ele, ele vasculha o Reddit, o X e a web para saber o que as pessoas estão realmente falando. Não são postagens antigas de blog. Conversas reais dos últimos 30 dias." -@itswilsoncharles

> "5 dos 10 repositórios populares no GitHub hoje são ferramentas Claude. #1: mvanhorn/last30days-skill" -@yieldhunter95

## Código aberto

Licença do MIT. Sem rastreamento. Sem análises. Sua pesquisa permanece em sua máquina. Mais de 2.700 testes.

Construído com Python 3.12+, yt-dlp, Node.js (cliente Bird vendido para pesquisa X) e API ScrapeCreators. arquitetura do mecanismo v3 por [@j-sperling](https://github.com/j-sperling).

Consulte [CONTRIBUTING.md](CONTRIBUTING.md) para abrir um PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) para obter a lista completa de contribuidores da comunidade e [CHANGELOG.md](CHANGELOG.md) para o histórico de versões.

## História da estrela

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
