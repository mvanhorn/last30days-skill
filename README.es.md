# obsidian2date

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | Español | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

**Investiga cualquier ventana reciente. Guarda lo útil en Obsidian.**

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Tests](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml/badge.svg)](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml)

`obsidian2date` investiga lo que la gente dice realmente sobre un tema en
Reddit, X, YouTube, HN, GitHub, Polymarket y la web — sobre la ventana que
pidas (la semana pasada, los últimos 7 días, los últimos 90 días; 30 días es
solo el valor por defecto) — y convierte cada ejecución en notas de Obsidian
duraderas y enlazadas.

Cada ejecución produce:

- una **nota de ejecución** respaldada por fuentes
- un **briefing** compacto
- `[[wikilinks]]` a ejecuciones relacionadas
- un **Índice** y un **Dashboard** actualizados

Sin seguimiento. MIT. Fork público de
[last30days-skill](https://github.com/mvanhorn/last30days-skill); el motor de
investigación ascendente sigue siendo fusionable. Requiere Python 3.12+ y un
vault de Obsidian; las fuentes y las claves de API son opcionales — ver
[CONFIGURATION.md](CONFIGURATION.md).

## Úsalo como comando de barra (camino principal)

`obsidian2date` es un Agent Skill: instala el repositorio una vez y escribe
`/obsidian2date <tema>` en tu agente. El skill ejecuta el motor de
investigación, resuelve tu vault, escribe las notas e informa de las rutas.
Sin flags que memorizar — di "la semana pasada" o "los últimos 90 días" en la
petición y el skill lo traduce a los flags correctos del motor.

| Host | Instalación | Después |
| --- | --- | --- |
| Claude Code | `npx skills add pauleschwarz/obsidian2date -g -y` (o añade este repo como `.claude-plugin`) | `/obsidian2date <tema>` |
| Codex | el repo incluye `.codex-plugin/plugin.json` | `/obsidian2date <tema>` |
| Grok | `grok plugin marketplace add pauleschwarz/obsidian2date` | `/obsidian2date <tema>` |
| Gemini CLI | el repo incluye `gemini-extension.json` | `/obsidian2date <tema>` |
| OpenClaw / hosts agents.md | el repo incluye el manifiesto `.agents/` | `/obsidian2date <tema>` |
| pi / cualquier agente con skills | enlaza o copia `skills/obsidian2date/` en el directorio de skills del agente | `/obsidian2date <tema>` |

Qué hace el skill en cada ejecución (ver
[`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md) — la
especificación canónica de ejecución que lee el modelo):

1. resolver tu vault (preguntar una vez, recordarlo para la sesión)
2. derivar la ventana de tu petición (por defecto 30 días)
3. ejecutar el motor de investigación con `--emit=obsidian`
4. informar del briefing, la nota de ejecución y las fuentes parciales o no disponibles con honestidad

## Inicio rápido (fallback CLI)

Para scripting, cron o pruebas del motor en desarrollo, llama a la CLI
directamente. Este es el camino de respaldo, no el principal — el comando de
barra de arriba es el producto.

```bash
git clone https://github.com/pauleschwarz/obsidian2date.git
cd obsidian2date

python3 skills/last30days/scripts/last30days.py \
  "local LLM agent frameworks" \
  --emit=obsidian \
  --obsidian-vault /ruta/a/tu/vault
```

O configura el vault una vez:

```bash
export OBSIDIAN2DATE_VAULT=/ruta/a/tu/vault
python3 skills/last30days/scripts/last30days.py "topic" --emit=obsidian
```

### Ventana temporal

`30` días es solo el valor por defecto. Pide lo que quieras:

```bash
python3 skills/last30days/scripts/last30days.py "AI video tools" --emit=obsidian --days 7    # la semana pasada
python3 skills/last30days/scripts/last30days.py "rust async runtimes" --emit=obsidian --days 90  # barrido trimestral
python3 skills/last30days/scripts/last30days.py "election odds" --emit=obsidian --days 14 --as-of 2026-08-15
```

En el comando de barra, solo dilo: "investiga los últimos 7 días de AI video
tools".

### Resolución del vault

El destino de exportación se resuelve en este orden:

1. `--obsidian-vault PATH` (una ruta explícita inexistente se crea para la exportación)
2. `OBSIDIAN2DATE_VAULT`
3. `LAST30DAYS_OBSIDIAN_VAULT`
4. un `~/Desktop/brain-paul` existente

Los candidatos de entorno y escritorio deben ser directorios ya existentes.
Un valor de entorno de vault presente pero vacío o solo con espacios
desactiva intencionadamente los fallbacks implícitos. Si nada se resuelve,
el comando se detiene con:

```text
No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT.
```

Usa `~/...` o una ruta absoluta en archivos `.env`; `$HOME` no se expande
allí. Las notas existentes nunca se sobrescriben; las colisiones de nombre
de archivo reciben un sufijo numérico.

## Qué se escribe

Disposición por defecto bajo la raíz del vault:

```text
90_Quellen/obsidian2date/
  runs/YYYY-MM-DD-<slug>.md
  briefings/YYYY-MM-DD-<slug>-briefing.md
  Index.md
  Dashboard.md
```

Las notas nunca se sobrescriben. Las colisiones del mismo día reciben
sufijos numéricos. Las ejecuciones anteriores relacionadas se enlazan vía
`[[wikilinks]]` de Obsidian cuando se detecta superposición de tokens.

## Fuentes y claves

El mismo suelo que upstream:

- **Sin claves por defecto:** Reddit, Hacker News, Polymarket, GitHub, Web
- **Opcionales:** X (cookies de navegador / backends), YouTube (`yt-dlp`),
  TikTok/IG (ScrapeCreators), además de otros backends de pago/opt-in

Ver [`CONFIGURATION.md`](CONFIGURATION.md) para la matriz completa y la
configuración de claves.

## Diagnóstico seguro

Ejecuta una comprobación de solo permisos antes de investigar:

```text
$ python3 skills/last30days/scripts/last30days.py --preflight
last30days preflight
Status: Ready to research with safe defaults.
...
Local writes:
- none planned
```

`--preflight` es seguro: se ejecuta **sin leer cookies, escribir archivos ni
iniciar la investigación**. Para depurar fuentes o backends instalados, usa
en su lugar la comprobación de salud:

```bash
python3 skills/last30days/scripts/last30days.py doctor
```

## Los modos de upstream siguen funcionando

```bash
# envoltorio de síntesis compacta original
python3 skills/last30days/scripts/last30days.py "topic" --emit=compact

# JSON para agentes
python3 skills/last30days/scripts/last30days.py "topic" --emit=json

# brief de producción
python3 skills/last30days/scripts/last30days.py "topic" --emit=brief
```

## Relación con upstream

| Aspecto | Política |
| --- | --- |
| Motor de investigación | Mantenerlo fusionable con `upstream/main` |
| Exportación a Obsidian | Módulo aditivo: `lib/obsidian_export.py` |
| Marca / skill | `obsidian2date` |
| Licencia | MIT; conservar los avisos de copyright de upstream |

```bash
git remote add upstream https://github.com/mvanhorn/last30days-skill.git
git fetch upstream
git merge upstream/main
```

## Créditos

- Motor de investigación upstream: [Matt Van Horn / last30days](https://github.com/mvanhorn/last30days-skill)
- Ruta de exportación a Obsidian + empaquetado del fork público: [pauleschwarz](https://github.com/pauleschwarz)

## Licencia

MIT. Ver [LICENSE](LICENSE).
