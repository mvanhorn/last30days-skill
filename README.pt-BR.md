# obsidian2date

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | Português (Brasil) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

**Pesquise qualquer janela recente. Mantenha o útil no Obsidian.**

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Tests](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml/badge.svg)](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml)

O `obsidian2date` pesquisa o que as pessoas realmente dizem sobre um tema no
Reddit, X, YouTube, HN, GitHub, Polymarket e na web — sobre a janela que você
pedir (semana passada, últimos 7 dias, últimos 90 dias; 30 dias é só o
padrão) — e transforma cada execução em notas de Obsidian duráveis e
interligadas.

Cada execução produz:

- uma **nota de execução** respaldada por fontes
- um **briefing** compacto
- `[[wikilinks]]` para execuções relacionadas
- um **Índice** e um **Dashboard** atualizados

Sem rastreamento. MIT. Fork público de
[last30days-skill](https://github.com/mvanhorn/last30days-skill); o motor de
pesquisa upstream permanece mesclável. Requer Python 3.12+ e um vault do
Obsidian; fontes e chaves de API são opcionais — veja
[CONFIGURATION.md](CONFIGURATION.md).

## Use como comando de barra (caminho principal)

O `obsidian2date` é um Agent Skill: instale o repositório uma vez e depois
digite `/obsidian2date <tema>` no seu agente. O skill roda o motor de
pesquisa, resolve o seu vault, escreve as notas e informa os caminhos. Sem
flags para memorizar — diga "semana passada" ou "nos últimos 90 dias" no
pedido e o skill traduz para as flags corretas do motor.

| Host | Instalação | Depois |
| --- | --- | --- |
| Claude Code | `npx skills add pauleschwarz/obsidian2date -g -y` (ou adicione este repo como `.claude-plugin`) | `/obsidian2date <tema>` |
| Codex | o repositório inclui `.codex-plugin/plugin.json` | `/obsidian2date <tema>` |
| Grok | `grok plugin marketplace add pauleschwarz/obsidian2date` | `/obsidian2date <tema>` |
| Gemini CLI | o repositório inclui `gemini-extension.json` | `/obsidian2date <tema>` |
| OpenClaw / hosts agents.md | o repositório inclui o manifesto `.agents/` | `/obsidian2date <tema>` |
| pi / qualquer agente com skills | crie um symlink ou copie `skills/obsidian2date/` para o diretório de skills do agente | `/obsidian2date <tema>` |

O que o skill faz em cada execução (veja
[`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md) — a
especificação de runtime canônica que o modelo lê):

1. resolver o seu vault (perguntar uma vez, lembrar pela sessão)
2. derivar a janela do seu pedido (padrão: 30 dias)
3. rodar o motor de pesquisa com `--emit=obsidian`
4. informar com honestidade o caminho do briefing, da nota de execução e quaisquer fontes parciais ou indisponíveis

## Início rápido (fallback CLI)

Para scripts, cron ou testes do motor em desenvolvimento, chame a CLI
diretamente. Este é o caminho de reserva, não o principal — o comando de
barra acima é o produto.

```bash
git clone https://github.com/pauleschwarz/obsidian2date.git
cd obsidian2date

python3 skills/last30days/scripts/last30days.py \
  "local LLM agent frameworks" \
  --emit=obsidian \
  --obsidian-vault /caminho/para/seu/vault
```

Ou configure o vault uma vez:

```bash
export OBSIDIAN2DATE_VAULT=/caminho/para/seu/vault
python3 skills/last30days/scripts/last30days.py "topic" --emit=obsidian
```

### Janela de tempo

`30` dias é só o padrão. Peça o que quiser:

```bash
python3 skills/last30days/scripts/last30days.py "AI video tools" --emit=obsidian --days 7    # semana passada
python3 skills/last30days/scripts/last30days.py "rust async runtimes" --emit=obsidian --days 90  # varredura de um trimestre
python3 skills/last30days/scripts/last30days.py "election odds" --emit=obsidian --days 14 --as-of 2026-08-15
```

No comando de barra, é só dizer: "pesquise os últimos 7 dias de AI video
tools".

### Resolução do vault

O destino de exportação é resolvido nesta ordem:

1. `--obsidian-vault PATH` (um caminho explícito inexistente é criado para a exportação)
2. `OBSIDIAN2DATE_VAULT`
3. `LAST30DAYS_OBSIDIAN_VAULT`
4. um `~/Desktop/brain-paul` existente

Os candidatos de ambiente e desktop já devem ser diretórios. Um valor de
ambiente de vault presente porém vazio ou só com espaços desativa
deliberadamente os fallbacks implícitos. Se nada resolver, o comando para
com:

```text
No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT.
```

Use `~/...` ou caminho absoluto em arquivos `.env`; `$HOME` não é expandido
lá. Notas existentes nunca são sobrescritas; colisões de nome de arquivo
ganham um sufixo numérico.

## O que é escrito

Layout padrão sob a raiz do vault:

```text
90_Quellen/obsidian2date/
  runs/YYYY-MM-DD-<slug>.md
  briefings/YYYY-MM-DD-<slug>-briefing.md
  Index.md
  Dashboard.md
```

Notas nunca são sobrescritas. Colisões no mesmo dia ganham sufixos
numéricos. Execuções anteriores relacionadas são ligadas via `[[wikilinks]]`
do Obsidian quando sobreposição de tokens é detectada.

## Fontes e chaves

Mesmo piso do upstream:

- **Sem chave por padrão:** Reddit, Hacker News, Polymarket, GitHub, Web
- **Opcionais:** X (cookies do navegador / backends), YouTube (`yt-dlp`),
  TikTok/IG (ScrapeCreators), além de outros backends pagos/opt-in

Veja [`CONFIGURATION.md`](CONFIGURATION.md) para a matriz completa e a
configuração de chaves.

## Diagnóstico seguro

Rode uma verificação só de permissões antes de pesquisar:

```text
$ python3 skills/last30days/scripts/last30days.py --preflight
last30days preflight
Status: Ready to research with safe defaults.
...
Local writes:
- none planned
```

O `--preflight` é seguro: roda **sem ler cookies, escrever arquivos ou
iniciar a pesquisa**. Para diagnosticar fontes ou backends instalados, use
em vez disso a checagem de saúde:

```bash
python3 skills/last30days/scripts/last30days.py doctor
```

## Os modos do upstream continuam funcionando

```bash
# envelope compacto de síntese original
python3 skills/last30days/scripts/last30days.py "topic" --emit=compact

# JSON para agentes
python3 skills/last30days/scripts/last30days.py "topic" --emit=json

# brief de produção
python3 skills/last30days/scripts/last30days.py "topic" --emit=brief
```

## Relação com o upstream

| Aspecto | Política |
| --- | --- |
| Motor de pesquisa | Permanecer mesclável com `upstream/main` |
| Exportação para Obsidian | Módulo aditivo: `lib/obsidian_export.py` |
| Marca / skill | `obsidian2date` |
| Licença | MIT; manter os avisos de copyright do upstream |

```bash
git remote add upstream https://github.com/mvanhorn/last30days-skill.git
git fetch upstream
git merge upstream/main
```

## Créditos

- Motor de pesquisa upstream: [Matt Van Horn / last30days](https://github.com/mvanhorn/last30days-skill)
- Caminho de exportação para Obsidian + empacotamento do fork público: [pauleschwarz](https://github.com/pauleschwarz)

## Licença

MIT. Veja [LICENSE](LICENSE).
