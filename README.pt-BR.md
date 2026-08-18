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

**Um buscador conduzido por um agente de IA, que pontua por votos positivos, curtidas e dinheiro de verdade — não por redações.**

Este README descreve o pipeline v3 atual. A especificação de execução da skill fica em [skills/last30days/SKILL.md](skills/last30days/SKILL.md), que é a referência definitiva sobre o comportamento dos comandos e da configuração.

**Claude Code (recomendado — atualizações automáticas via marketplace):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI, ou qualquer um dos 50+ hosts do [Agent Skills](https://agentskills.io):**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` instala globalmente para o seu usuário, então fica disponível em todos os projetos. Omita essa flag se quiser limitar a instalação a um projeto.)

Outras formas de instalar (claude.ai web, OpenClaw, manual) estão na seção [Instalação](#instalação), mais abaixo.

Configuração zero. Reddit, HN, Polymarket e GitHub funcionam de imediato. Rode uma vez e o assistente de configuração libera X, YouTube, TikTok, arXiv, Techmeme e mais em 30 segundos.

---

Os votos positivos do Reddit. As curtidas do X. As transcrições do YouTube. O engajamento no TikTok. As probabilidades do Polymarket, lastreadas em dinheiro de verdade e em informação privilegiada. São milhões de pessoas votando todo dia com a atenção e com o bolso. O /last30days busca tudo isso em paralelo, pontua pelo que as pessoas realmente engajam e um agente de IA atua como juiz para sintetizar tudo em um único briefing.

O Google agrega redações. O /last30days busca pessoas.

Essa busca você não encontra em nenhum outro lugar, porque nenhuma IA sozinha tem acesso a tudo. O Google não alcança nem os comentários do Reddit nem as publicações do X. O ChatGPT tem acordo com o Reddit, mas não consegue buscar no X nem no TikTok. O Gemini tem o YouTube, mas não o Reddit. O Claude não tem nenhum deles de forma nativa. Cada plataforma é um jardim murado, com API, tokens e autenticação próprios. Mas você pode trazer suas próprias chaves e sessões de navegador e, de repente, um agente de IA busca em todas ao mesmo tempo, compara umas com as outras e diz o que realmente importa.

É esse o destravamento. Não é um buscador melhor: é uma dúzia de plataformas isoladas, conectadas por um agente.

```
/last30days Peter Steinberger
```

Você tem uma reunião amanhã. Procura a pessoa no Google. Aparece o LinkedIn dela de 2023. O /last30days entrega o que ela está fazendo de fato neste mês: entrou na OpenAI para trabalhar no Codex, enfrenta o veto da Anthropic a agentes de terceiros, entregou 23 PRs com 85 % de taxa de merge, constrói o "LobsterOS" para controlar agentes entre dispositivos, e uma thread no r/ClaudeCode chegou a 569 votos positivos discutindo se ele é um herói ou "insuportável". Tudo espalhado entre publicações no X, threads no Reddit, transcrições do YouTube e commits no GitHub. Nada disso estava no Google.

## Por que isso existe

Construí para acompanhar o ritmo da IA. Tudo muda todo dia, e o pessoal do Reddit e do X sempre sabe primeiro. Eu precisava de prompts melhores, e os dados de treinamento estavam sempre meses atrás do que a comunidade já tinha descoberto.

Mas virou algo maior. Hoje eu rodo antes de uma call de vendas, para saber a verdade dos últimos 30 dias sobre uma empresa. Antes de uma reunião, para ler os tweets recentes e as transcrições de podcast de quem vou encontrar. Antes de uma viagem à Disney World, para saber quais brinquedos estão fechados e o que a comunidade acha do Genie+. Antes de construir qualquer coisa, para saber em quais problemas as pessoas realmente estão esbarrando.

Se você vai se reunir com um CEO, já leu todos os tweets e todas as transcrições do YouTube dos últimos 30 dias? Eu li.

## Fontes, pontuadas pelas pessoas

| Fonte | O que as pessoas te contam |
|--------|--------------------------|
| **Reddit** | A opinião sem filtro. Os melhores comentários com a contagem real de votos positivos, de graça e sem chave de API. As opiniões de verdade que o Google enterra. |
| **X / Twitter** | A opinião quente, a thread do especialista, a primeira reação ao factual. Os primeiros a saber, os primeiros a discutir. |
| **YouTube** | A análise de 45 minutos. Transcrições completas, garimpadas atrás das 5 frases citáveis que importam. |
| **TikTok** | O criador que alcança 3,6 milhões de pessoas com uma leitura que você nunca vai achar no Google. |
| **Instagram Reels** | O olhar dos influenciadores, com a transcrição do que é falado. O sinal da cultura visual. |
| **Hacker News** | O consenso da turma de desenvolvimento. 825 pontos, 899 comentários. Onde o pessoal técnico discute de verdade. |
| **Polymarket** | Não são opiniões. São probabilidades. Lastreadas em dinheiro de verdade. 96 % de confiança em vendas de um álbum. 4 % em uma aquisição. |
| **GitHub** | Para pessoas: ritmo de PRs, principais repositórios por estrelas, notas de versão. Para assuntos: issues e discussions. |
| **Digg** | Agrupamentos de histórias curados a partir do ranking AI 1000 do Digg (cerca de 1000 contas de IA com alto sinal no X), com citações atribuíveis embutidas e sem exigir autenticação no X. Ativa sozinho quando `digg-pp-cli` está no PATH. |
| **arXiv** | Os artigos científicos por trás do hype. Pesquisa nova dentro da janela, de graça e sem chave de API. Ativa sozinho quando `arxiv-pp-cli` está no PATH (a configuração inicial instala). |
| **Techmeme** | A camada editorial do noticiário de tecnologia, limitada à sua janela de 30 dias. De graça e sem chave de API. Ativa sozinho quando `techmeme-pp-cli` está no PATH (a configuração inicial instala). |
| **LinkedIn** | O sinal profissional. Publicações e artigos, com os artigos ponderados como sinal forte. |
| **StockTwits** | O humor dos traders. Ativa automaticamente quando seu assunto é um ticker ou uma cripto. |
| **Threads** | A camada de texto do pós-Twitter. Conversas de criadores e marcas. |
| **Pinterest** | Descoberta visual. Pins, itens salvos e comentários sobre produtos e ideias. |
| **Xiaohongshu (RED)** | Sinais chineses sobre estilo de vida, produtos e criadores. É pedido explicitamente com `--search xhs` quando há um plugin de navegador x-mcp logado ou um serviço `xiaohongshu-mcp` rodando localmente. |
| **Bluesky** | A camada social descentralizada. Publicações do AT Protocol vindas da migração pós-Twitter. |
| **Perplexity** | Síntese controlada da Agent API, alternativa Sonar via OpenRouter, resultados brutos da Search API e Deep Research explícito. |
| **Web** | A cobertura editorial, as comparações de blog. Um sinal entre muitos, não o único. |

A comunidade não para de acrescentar fontes. Truth Social e outras fontes de nicho já estão no motor, e vêm mais por aí.

Uma thread do Reddit com 1.500 votos positivos é um sinal mais forte do que um post de blog que ninguém leu. Um TikTok com 3,6 milhões de visualizações diz mais sobre o que é culturalmente relevante do que qualquer release de imprensa. Probabilidades do Polymarket lastreadas em US$ 66 mil de volume são bem mais difíceis de contestar do que o palpite de um comentarista.

A síntese ordena pelo que as pessoas de verdade realmente engajaram. Relevância social, não relevância de SEO.

## Para que as pessoas realmente usam

**Antes de uma reunião.** `/last30days Peter Steinberger` — entrou no time do Codex na OpenAI, enfrenta o veto da Anthropic a agentes de terceiros, 23 PRs mergeados com 85 % de taxa de merge no GitHub, constrói o LobsterOS para controlar agentes entre dispositivos. r/ClaudeCode: "Desde que o OpenClaw saiu, todo mundo já sabia que, se você rodasse por qualquer coisa que não fosse a API, uma hora ia ser banido" (227 votos positivos). Isso não está no LinkedIn.

**Para ler sinais de contratação.** `/last30days Listen Labs --hiring-signals` — as vagas e páginas de carreira atuais viram evidência citada de mudança de prioridade: contratação em segurança para empresas, customer success, infraestrutura ou expansão de produto. O relatório diz o que a contratação parece sinalizar, não o que o roadmap vai entregar.

**Para achar o assunto antes do pico.** Pergunte `/last30days what's exploding in AI agents?` e a skill muda para o modo descoberta: o motor varre as listagens por categoria do Reddit, a capa e as melhores histórias do Hacker News, o feed AI 1000 do Digg e o X quando você está autenticado; seu agente avalia as indicações (nomes, filtragem de ruído, se rende conteúdo) e escreve ângulos para podcast ou para um artigo no X; depois você recebe de 5 a 10 assuntos ordenados por velocidade. Cada resultado traz números de várias fontes, um rótulo de momentum e um comando `/last30days "<topic>"` pronto para rodar.

**Quando alguma coisa é lançada.** `/last30days Kanye West` — o Reino Unido bloqueou o visto dele, o Wireless Festival foi cancelado, os patrocinadores fugiram. Mas BULLY estreou em 2º na Billboard. Fantano voltou do "Yay sabbatical" para resenhar o disco (653 mil visualizações). No SoFi Homecoming, ele levou Lauryn Hill e Travis Scott ao palco para 44 músicas. Polymarket: "Kanye vai tuitar de novo?" 86 % sim. 23 threads no Reddit, 17 vídeos no YouTube, 86 mil votos positivos.

**Para comparar ferramentas.** `/last30days OpenClaw vs Hermes vs Paperclip` — "Não são concorrentes, são camadas." O OpenClaw é a camada de execução (351 mil estrelas no GitHub, em produção), o Hermes é o cérebro que se aprimora sozinho (31 mil estrelas), o Paperclip é o organograma (49 mil estrelas). As contagens de estrelas vêm ao vivo da API do GitHub, não de posts de blog desatualizados. Tabela lado a lado com arquitetura, memória, segurança e melhor caso de uso. Segundo @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard."

**Para entender o mundo.** `/last30days Iran vs USA` — dia 38 da guerra. O ultimato de Trump, com prazo até terça, para o Irã reabrir o Estreito de Ormuz. Dois caças americanos abatidos. Petróleo a US$ 126 o barril. A AIE chamou o episódio de "a maior interrupção de fornecimento da história do mercado global de petróleo". Polymarket: cessar-fogo até 31 de dezembro a 74 %. 27 publicações no X, 10 vídeos no YouTube, 20 mercados de previsão.

**Antes de uma viagem.** `/last30days Universal Epic Universe` — a expansão já está em obras. Alvará do "Project 680" protocolado. Show de fogos confirmado pela infraestrutura, mas ainda não anunciado. Tempo de espera: Mine-Cart Madness com média de 148 minutos. Ainda sem passe anual, e os moradores estão irritados. Stardust Racers fechada para reforma até 5 de abril.

**Para aprender algo rápido.** `/last30days Nano Banana Pro prompting` — prompts estruturados em JSON estão substituindo a sopa de tags. O formato aninhado do @pictsbyai evita o "concept bleeding". Editar ganha de regerar. E depois a skill escreve um prompt de produção usando exatamente o que a comunidade disse que funciona.

## Novidades

Desde o anúncio da v3.3 em maio e até a v3.11.1 (julho de 2026): 175 PRs mergeados — 122 deles de 52 pessoas da comunidade — distribuídos em 15 versões. Foi isso que entrou.

### Cidadão de primeira classe no OpenAI Codex

O /last30days agora é um plugin nativo do Codex com configuração guiada: não é um port, é cidadão de primeira classe. As citações levam o renderizador em conta, então a saída no Codex se lê como um briefing e não como uma sopa de URLs (#694), e o mesmo motor roda no Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClaw e em 50+ hosts do Agent Skills. Manifesto do plugin do Codex por [@rfoust](https://github.com/rfoust) (#686), correção de autenticação no Codex por [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmeme e Digg — de graça, sem chaves de API

O arXiv traz os artigos científicos por trás do hype e o Techmeme traz a camada editorial do noticiário de tecnologia — de graça, sem nenhuma chave, e a configuração inicial instala as CLIs deles para que ativem sozinhos (#709). Os agrupamentos de histórias AI 1000 do Digg chegam do mesmo jeito, sem autenticação no X: a configuração instala a CLI gratuita do Digg para você (#590). O Trustpilot está disponível como opção para pesquisa de marcas de consumo.

### Reddit gratuito, com pontuações reais e melhores comentários

A API pública .json do Reddit morreu; o caminho gratuito voltou mais forte. RSS sem chave e scraping do shreddit (#457), descoberta de subreddits específicos com contagem real de votos positivos via arctic-shift (#696) e um piso de relevância para que um post viral fora do tema não sequestre seu briefing (#488, valeu [@rzachsmith](https://github.com/rzachsmith)). Sem chave de API. Pontuações reais. Melhores comentários incluídos.

### Os melhores comentários em cada briefing

Os comentários agora são uma camada ligada por padrão em todas as fontes: comentários do Instagram com diversidade baseada em ranking, para que cinco opiniões fortes não venham todas do mesmo post (#751), comentários do YouTube mais um backup de transcrição via ScrapeCreators para quando o yt-dlp falha (#637), e comentários votados pela comunidade entrando com peso no Best Takes, para que as melhores tiradas sobrevivam à pontuação (#592, #608).

### Um único comando doctor

Peça um diagnóstico: o doctor testa cada fonte e receita as correções exatas — qual chave está faltando, qual CLI não está no PATH, qual cookie expirou (#753). Chega de adivinhar por que o X voltou fraco.

### A busca no X, reconstruída

O pipeline do X foi refeito do zero: faixas FROM e ABOUT para que tanto as publicações da própria pessoa quanto a conversa sobre ela sejam ranqueadas (#610), desambiguação de subconsultas conforme a pessoa buscada (#611), verificação de autoria de primeira mão com ranqueamento por sinais de interação (#613) e uma única fonte X com failover automático entre backends (#622). Além de um `--diagnose` honesto, que testa a autenticação de verdade (#609).

### Mais fontes entraram

LinkedIn via ScrapeCreators, com artigos como sinal forte ([@ravstr](https://github.com/ravstr), #702). O StockTwits ativa sozinho em assuntos de ticker e cripto ([@wtiwana](https://github.com/wtiwana), #658). O Perplexity ganhou modos de API diretos e Deep Research assíncrono ([@sk-holmes](https://github.com/sk-holmes), #629).

### Endurecido pela comunidade

A onda de segurança foi quase toda trabalho da comunidade: correções de XSS armazenado no renderizador HTML ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), arquivos temporários de cookie protegidos, CI endurecida contra ataques à cadeia de suprimentos com OpenSSF Scorecard e atestação de proveniência de build ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), varreduras com Semgrep e OSV-Scanner mais um portão de revisão de dependências em cada PR ([@23241a6749](https://github.com/23241a6749)), um piso de cobertura de testes criado em 60 % e desde então elevado para 84 % ([@gourab5139014](https://github.com/gourab5139014)), e uma varredura de segurança do Hermes que hoje não tem nenhum achado CRITICAL (#768).

### Alcança mais longe

Hebraico e outras línguas não latinas ([@dudyme](https://github.com/dudyme)). Tokenização adaptada a CJK para fontes chinesas ([@An-idd](https://github.com/An-idd)). Uma onda de compatibilidade com Windows. Extração de cookies em toda a família Chromium — Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) — além do Keychain do macOS e do pass(1) no Linux como origens de credenciais. Consulta retroativa com `--as-of` ([@chiyi-creator](https://github.com/chiyi-creator)). Provisionamento automático do Python 3.12 via uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` para ler as páginas de vagas de uma empresa. Deltas de watchlist entre execuções.

### O que já vinha de fábrica desde a v3

As bases da v3 continuam todas aqui: o cérebro de pré-pesquisa, que identifica os handles, subreddits e hashtags certos antes de disparar uma única chamada de API (construído por [@j-sperling](https://github.com/j-sperling)); a pontuação Best Takes, que considera humor e viralidade além de relevância; a fusão de clusters entre fontes; as comparações em uma única passada ("CLI vs MCP" em 3 minutos, não em 12); as comparações `--competitors` descobertas automaticamente; o modo pessoa do GitHub (`--github-user=steipete`); o modo ELI5 ("eli5 on" depois de qualquer execução); e os briefings HTML autocontidos e compartilháveis (`--emit=html`). Os ajustes de configuração estão em [CONFIGURATION.md](CONFIGURATION.md).

## Instalação

| Ambiente | Instalação | Atualizações |
|---------|---------|---------|
| **Claude Code** (recomendado) | `/plugin marketplace add mvanhorn/last30days-skill` | Automáticas via marketplace, ou `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` e depois `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI, ou qualquer um dos 50+ hosts do [Agent Skills](https://agentskills.io)** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [Baixe `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) e envie por claude.ai > Customize > Skills > + > Create skill > Upload a skill | Baixar de novo e enviar de novo |
| **Claude Desktop** | [Baixe o `.mcpb` da sua plataforma](https://github.com/mvanhorn/last30days-skill/releases/latest) e arraste para Settings > Extensions | Baixar de novo e arrastar o novo pacote |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recomendado)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Recomendado porque o marketplace do Claude Code cuida das atualizações por você: o cache do plugin é versionado e se atualiza sozinho quando sai uma versão nova. Rode `claude plugin update last30days@last30days-skill` para forçar uma verificação.

Se preferir usar o caminho de instalação do Agent Skills no Claude Code, ele também é suportado:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

O plugin nativo e a instalação com `npx skills` podem conviver. Só atenção: o Claude Code não deduplica entre métodos de instalação. Se você tiver ativos ao mesmo tempo o plugin do marketplace e a cópia do `npx skills`, o `/last30days` vai aparecer duas vezes. Use um método de instalação por máquina.

### Grok (xAI Build CLI)

O [Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) instala o last30days como plugin nativo. A instalação direta acompanha o repositório:

```bash
grok plugin install mvanhorn/last30days-skill
```

Ou adicione este repositório como fonte de marketplace e depois instale pelo nome do plugin:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Acrescente `--trust` para pular a confirmação de instalação. Atualize com `grok plugin update last30days`. O Grok também lê os manifestos do Claude Code por compatibilidade; o par nativo `.grok-plugin/` é o caminho principal — e é para ele que aponta um registro oficial no [marketplace da xAI](https://github.com/xai-org/plugin-marketplace). O `npx skills add` continua sendo uma alternativa válida em qualquer host.

### Codex, Cursor, Copilot, Gemini CLI e outros hosts do Agent Skills

Instale pela CLI aberta do [Agent Skills](https://agentskills.io) — ela suporta 50+ hosts, entre eles `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose` e outros (lista completa no [repositório vercel-labs/skills](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

A flag `-g` (global) instala no seu diretório de usuário, então a skill fica disponível em todos os projetos. Sem `-g`, o `npx skills` instala só no projeto, dentro de `./.skills/` (e vai versionado junto com o repositório). Para uma ferramenta feita para pesquisar o mundo inteiro, o que você quer é a instalação global.

O Codex desktop e outros hosts que trabalham no nível de pasta funcionam tanto em pastas comuns quanto em repositórios Git. Antes da primeira pesquisa, peça ao agente host que rode o `scripts/last30days.py --preflight` que acompanha a skill, a partir do diretório da skill carregada; em um clone do código-fonte, o comando equivalente é `python3 skills/last30days/scripts/last30days.py --preflight`. Ele mostra de onde vem a configuração, quais cookies do navegador seriam lidos, quais arquivos seriam escritos, quais comandos opcionais existem e qual configuração de projeto está sendo ignorada — sem ler cookies, sem escrever arquivos e sem rodar pesquisa nenhuma.

Por padrão, a instalação vale para o host que o `npx skills` detectar. Para mirar em um específico (ou em vários):

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Para atualizar depois:

```bash
npx skills update last30days -g
```

Ou atualize tudo que você instalou globalmente pelo `npx skills`:

```bash
npx skills update -g
```

Dá para listar e remover com `npx skills list -g` e `npx skills remove last30days -g`.

### claude.ai (web)

1. [Baixe `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) da versão mais recente
2. Vá em [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Clique no botão `+` no painel de Skills, depois em `Create skill` > `Upload a skill`, e escolha ou arraste o arquivo

Ative antes "Code execution and file creation" em Capabilities — sem isso, as skills não rodam.

### Claude Desktop

O Claude Desktop instala o `/last30days` como servidor MCP por meio de um pacote `.mcpb` (um pacote Model Context Protocol de um clique).

1. Vá até a [versão mais recente](https://github.com/mvanhorn/last30days-skill/releases/latest) e baixe o `.mcpb` da sua plataforma:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Abra o Claude Desktop, vá em Settings > Extensions e arraste o arquivo para lá.
3. Quando for solicitado, cole as chaves de API das fontes que quiser ativar. Todo campo é opcional — se pular todos, o motor cai para o modo só web. As chaves ficam guardadas no chaveiro do seu sistema operacional.
4. Reinicie o Claude Desktop. Peça ao Claude para "pesquisar sobre Peter Steinberger", ou sobre qualquer outro assunto, e ele vai chamar a ferramenta `research`.

**Requisito do host:** Python 3.12+ no PATH. O pacote traz o código do motor, mas usa o seu interpretador Python local. No Windows, instale a partir do [python.org](https://www.python.org/downloads/); o macOS e a maioria das distribuições Linux já vêm com uma versão compatível.

**As chaves não são compartilhadas com a skill do Claude Code.** O Claude Desktop e o Claude Code mantêm armazenamentos de credenciais separados de propósito. Se você já configurou o `~/.config/last30days/.env` para a skill do Claude Code, vai precisar digitar essas mesmas chaves aqui uma vez.

O suporte a Windows está adiado até que os pontos de entrada por plataforma no manifesto sejam resolvidos; o acompanhamento fica em uma issue à parte.

### OpenClaw

```bash
clawhub install last30days-official
```

Para fluxos de ação no X/Twitter fora da pesquisa do `/last30days` — publicar
tweets ou respostas, exportar seguidores, cuidar de mídia, monitorar contas e
apurar sorteios — use o [TweetClaw](https://github.com/Xquik-dev/tweetclaw) como
plugin complementar do OpenClaw. O TweetClaw é mantido pelo Xquik-dev e aparece
aqui apenas como opção complementar: não é dependência nem recomendação do
last30days.

### Manual (para quem desenvolve)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

O symlink mantém a instalação em sincronia com sua árvore de trabalho conforme você edita — não precisa copiar de novo. Para o `claude.ai`, compile o arquivo `.skill` a partir do código-fonte: `bash skills/last30days/scripts/build-skill.sh` gera `dist/last30days.skill`.

Reddit (com comentários), Hacker News, Polymarket e GitHub funcionam de imediato. Configuração zero. Rode `/last30days` uma vez e o assistente de configuração libera mais fontes em 30 segundos, incluindo as CLIs gratuitas do arXiv e do Techmeme.

## Traga suas próprias chaves

Essas plataformas não têm relação nenhuma entre si. O X não sabe o que o Reddit pensa. O YouTube não enxerga o TikTok. Mas você pode trazer suas próprias chaves de API e tokens de navegador e, de repente, tem acesso a todas ao mesmo tempo.

| Fontes | O que você precisa | Custo |
|---------|---------------|------|
| Reddit (com comentários) + HN + Polymarket + GitHub + StockTwits | Nada | De graça |
| arXiv + Techmeme | CLIs gratuitas, instaladas automaticamente pela configuração inicial | De graça |
| X / Twitter | Faça login em x.com em qualquer navegador, ou defina `XQUIK_API_KEY` / `XAI_API_KEY` | Os cookies do navegador são gratuitos; as chaves dependem do provedor |
| YouTube | `brew install yt-dlp` | De graça |
| Bluesky | Uma senha de aplicativo do bsky.app | De graça |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + comentários do YouTube | Uma chave do ScrapeCreators | 10.000 chamadas gratuitas e depois pagamento por uso |
| Xiaohongshu (RED) | Deixe rodando um plugin de navegador x-mcp logado ou um serviço `xiaohongshu-mcp` e habilite a fonte com `--search xhs` por execução ou com `INCLUDE_SOURCES=xiaohongshu` no `.env`; o last30days testa automaticamente `http://localhost:18060` e depois `http://host.docker.internal:18060`, ou use `XIAOHONGSHU_API_BASE` para uma URL própria | Não precisa de chave de API do last30days; depende do seu serviço local de sessão de navegador |
| DripStack (newsletters financeiras premium) | Opcional: `--search dripstack` por execução, ou `INCLUDE_SOURCES=dripstack` no `.env` | Sem chave; API de busca pública e gratuita |
| Perplexity Agent API / Search API / Deep Research | Uma chave do Perplexity, ou uma chave do OpenRouter como alternativa para o Sonar | Pagamento por uso; uma chave direta ativa a Agent API e o Deep Research em segundo plano |
| Busca na web | Uma chave do Brave Search | 2.000 consultas gratuitas por mês |

### Keychain do macOS (opcional)

No macOS você pode guardar as chaves no Keychain do sistema em vez de em um arquivo `.env`. A skill as encontra automaticamente como a origem de menor prioridade — em caso de conflito, os arquivos `.env` e o ambiente do processo continuam ganhando.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Os itens ficam guardados sob o nome de serviço `last30days-<KEY>` para o usuário atual. Em plataformas que não são Darwin o carregador não faz nada, então não há mudança de comportamento para quem usa Linux ou Windows.

Já tem chaves guardadas com outros nomes de serviço no Keychain? Defina o mapeamento não secreto `LAST30DAYS_KEYCHAIN_ALIASES` descrito em [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items), em vez de copiar segredos.

Veja [CONFIGURATION.md](CONFIGURATION.md) para a matriz completa de chaves por fonte, a ordem de prioridade dos provedores de raciocínio e a dos backends de busca web.

## Configuração

Duas coisas que você provavelmente vai querer saber no primeiro dia:

**Onde os arquivos de pesquisa são salvos.** O `LAST30DAYS_MEMORY_DIR` aponta por padrão para `~/Documents/Last30Days/` (no Windows: `C:\Users\<you>\Documents\Last30Days\`). Para mudar, defina essa variável de ambiente no seu shell com o caminho que quiser, ou use `--save-dir <path>` em uma execução específica. Use `--output <file>` quando precisar do resultado renderizado em um caminho exato, no formato escolhido por `--emit`. Use `--save-suffix=<name>` para manter separadas várias variações do mesmo assunto (por cliente, por exemplo). Cada execução com `--save-dir` gera `<slug>-raw[-suffix].md`. Rode `python3 skills/last30days/scripts/last30days.py --preflight` para conferir o que será escrito antes de disparar uma pesquisa.

**Saída estruturada para agentes e fluxos de trabalho.** Peça ao `/last30days` um JSON legível por máquina e você recebe o perfil de agente estável e versionado. Para usar o motor direto em scripts ou no desenvolvimento, rode `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; use `--json-profile=raw` só quando precisar do dump interno não versionado do `Report`. Veja a [referência de campos da exportação JSON e a política de versionamento](docs/reference/json-export.md).

**Descoberta sem assunto definido.** Pergunte `/last30days what's trending in AI agents?` para receber um briefing de descoberta ordenado, em vez de pesquisar um assunto que você já conhece. Em um host com agente, isso executa o protocolo de três comandos arbitrado pelo host (o modelo nomeia os assuntos, filtra ruído, avalia o que vale a pena e escreve os ângulos de conteúdo). Para usar o motor direto em scripts ou no cron, rode `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (passada única: nomes de assunto determinísticos, sem ângulos); acrescente `--emit=json` para o contrato de descoberta versionado. A descoberta é mutuamente exclusiva com um assunto posicional e com `--drill`.

**Monitoramento de tendências entre execuções.** O modo padrão gera um snapshot Markdown novo a cada execução. Para acumular achados ao longo do tempo, acrescente `--store` e eles ficam guardados em um banco SQLite; depois use [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) para execuções agendadas (com envio opcional por Slack ou webhook quando surgirem achados novos) e [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) para resumos diários ou semanais. O padrão de cadência completo está em [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Uma biblioteca de pesquisa que dá para assinar.** Peça ao `/last30days` que monte o feed da sua biblioteca, ou use direto `python3 skills/last30days/scripts/last30days.py library feed` para scripts e desenvolvimento. Ele transforma os briefings salvos em um `index.html`, um `feed.xml` Atom local e páginas de briefing legíveis. Acrescente `--publish` só quando quiser hospedar o índice HTML e as páginas de briefing; publicar é uma decisão explícita e, por padrão, é público. Para o feed Atom ficar realmente assinável, hospede o diretório de saída gerado em um serviço estático como o GitHub Pages.

**Busque em tudo que você já pesquisou.** Pergunte `/last30days search my library for MCP servers` ou `/last30days have I researched MCP servers before?`. Para usar o motor direto, rode `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. A busca é offline e determinística: ela indexa aos poucos os mesmos briefings salvos que o feed da biblioteca usa, junta as ocorrências correspondentes registradas no store a cada execução e agrupa os resultados por assunto e data. Execuções novas também exibem uma seção compacta **From your library** ("da sua biblioteca") quando pesquisas anteriores se sobrepõem ao assunto atual; defina `LAST30DAYS_LIBRARY_CONTEXT=off` para desativar esse contexto passivo.

Scripts wrapper por cliente, subreddits de categoria personalizados e o canal beta experimental para personalizações em andamento também estão documentados em [CONFIGURATION.md](CONFIGURATION.md).

## Vitrine: feeds de pesquisa da comunidade

Publicou com o last30days um panorama recorrente de IA, um acompanhamento de mercado ou uma obsessão maravilhosamente específica? Compartilhe a URL da sua biblioteca pública — ou a URL do Atom, depois de hospedar o `feed.xml` em um serviço estático — na [thread de vitrine da comunidade](https://github.com/mvanhorn/last30days-skill/issues/532). Os feeds da comunidade serão linkados aqui conforme as pessoas os enviarem; enquanto isso, a thread é o ponto de coleta.

## Como funciona

1. **Você digita um assunto.** Pessoa, empresa, produto, tecnologia, "X vs Y". Qualquer coisa.
2. **O agente descobre quem importa.** Ele encontra os perfis do X (inclusive de fundadores), os repositórios do GitHub, os subreddits, as hashtags do TikTok e os canais do YouTube. Para "Kanye West" ele sabe que o caminho é r/hiphopheads, @kanyewest e "bully review" no YouTube. Para "OpenClaw" ele resolve openclaw/openclaw no GitHub e busca a contagem de estrelas ao vivo.
3. **Todas as fontes buscadas em paralelo.** Expansão com várias consultas. Resultados pontuados por engajamento, relevância e frescor.
4. **A profundidade que ninguém mais tem.** Transcrições completas do YouTube de vídeos de reação. Os melhores comentários do Reddit com a contagem de votos positivos. As legendas dos TikToks. As probabilidades do Polymarket. Não só títulos e links.
5. **Mesma história, unificada.** O Wireless Festival anunciado no Reddit, discutido no X e com preço de ingresso no TikTok vira um cluster só, não três itens separados.
6. **Sintetizado em um único briefing.** Ancorado em dados específicos. Citado por fonte. Ordenado pelo que as pessoas realmente engajam. Não é "olha o que eu encontrei", é "olha o que importa".
7. **E então ele vira o seu especialista.** Depois de uma única execução, sua sessão do Claude sabe tudo o que a comunidade sabe. Faça perguntas de acompanhamento. Peça para escrever prompts, redigir e-mails, planejar viagens, desenhar arquiteturas — tudo ancorado no que é real agora.

## O que as pessoas estão dizendo

> "Achei uma skill do Claude Code que pesquisa qualquer assunto no Reddit, X, YouTube e HN dos últimos 30 dias. E ainda escreve os prompts pra você. Antes de cada conteúdo que eu escrevo, eu fazia essa busca na mão no Reddit e no X. Aba por aba. Thread por thread. É justamente essa a parte que leva 90 minutos. Isso elimina ela." — @itsjasonai

> "Essa skill sozinha substituiu todo o meu fluxo de pesquisa. Você dá um assunto e ela raspa Reddit, X e a web atrás do que as pessoas estão falando de verdade. Nada de post de blog velho. Conversas reais dos últimos 30 dias." — @itswilsoncharles

> "5 dos 10 repositórios em alta no GitHub hoje são ferramentas do Claude. O nº 1: mvanhorn/last30days-skill" — @yieldhunter95

## Código aberto

Licença MIT. Sem rastreamento. Sem analytics. Sua pesquisa fica na sua máquina. Mais de 2.700 testes.

Construído com Python 3.12+, yt-dlp, Node.js (cliente Bird embarcado para a busca no X) e a API do ScrapeCreators. Arquitetura do motor v3 por [@j-sperling](https://github.com/j-sperling).

Veja [CONTRIBUTING.md](CONTRIBUTING.md) para abrir um PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) para a lista completa de quem contribuiu e [CHANGELOG.md](CHANGELOG.md) para o histórico de versões.

## Histórico de estrelas

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
