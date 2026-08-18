# /last30days

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | Español | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

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

**Un buscador dirigido por un agente de IA que puntúa por votos positivos, likes y dinero real, no por redacciones.**

Este README documenta el pipeline v3 actual. La especificación de ejecución de la skill vive en [skills/last30days/SKILL.md](skills/last30days/SKILL.md), que es la referencia definitiva sobre el comportamiento de los comandos y la configuración.

**Claude Code (recomendado — actualizaciones automáticas vía marketplace):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI, o cualquiera de los 50+ hosts de [Agent Skills](https://agentskills.io):**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` instala de forma global para tu usuario, así que la tienes disponible en todos tus proyectos. Omite ese flag si prefieres limitar la instalación a un proyecto.)

Más formas de instalarlo (claude.ai web, OpenClaw, manual) en la sección [Instalación](#instalación) de más abajo.

Cero configuración. Reddit, HN, Polymarket y GitHub funcionan de inmediato. Ejecútalo una vez y el asistente de configuración desbloquea X, YouTube, TikTok, arXiv, Techmeme y más en 30 segundos.

---

Los votos positivos de Reddit. Los likes de X. Las transcripciones de YouTube. La interacción en TikTok. Las cuotas de Polymarket, respaldadas por dinero real y por información privilegiada. Eso son millones de personas votando cada día con su atención y su cartera. /last30days lo busca todo en paralelo, lo puntúa según aquello con lo que la gente interactúa de verdad, y un agente de IA hace de juez para sintetizarlo en un único informe.

Google agrega redacciones. /last30days busca personas.

Esta búsqueda no la consigues en ningún otro sitio, porque ninguna IA tiene acceso a todo. Google no toca los comentarios de Reddit ni las publicaciones de X. ChatGPT tiene un acuerdo con Reddit, pero no puede buscar en X ni en TikTok. Gemini tiene YouTube, pero no Reddit. Claude no tiene ninguno de forma nativa. Cada plataforma es un jardín amurallado con su propia API, sus propios tokens y su propia autenticación. Pero tú puedes aportar tus claves y tus sesiones de navegador y, de golpe, un agente de IA las consulta todas a la vez, las compara entre sí y te dice qué importa de verdad.

Ese es el desbloqueo. No se trata de un buscador mejor, sino de una docena de plataformas incomunicadas que un agente conecta entre sí.

```
/last30days Peter Steinberger
```

Mañana tienes una reunión. Buscas a la persona en Google. Te sale su LinkedIn de 2023. /last30days te da lo que está haciendo de verdad este mes: se ha incorporado a OpenAI para trabajar en Codex, pelea contra el veto de Anthropic a los agentes de terceros, ha entregado 23 PR con un 85 % de tasa de merge, construye «LobsterOS» para controlar agentes entre dispositivos, y un hilo de r/ClaudeCode llegó a 569 votos positivos debatiendo si es un héroe o un «insoportable». Todo repartido entre publicaciones de X, hilos de Reddit, transcripciones de YouTube y commits de GitHub. Nada de eso estaba en Google.

## Por qué existe esto

Lo construí para no quedarme atrás en IA. Todo cambia cada día y los frikis de Reddit y de X siempre se enteran primero. Necesitaba mejores prompts, y los datos de entrenamiento siempre iban meses por detrás de lo que la comunidad ya había averiguado.

Pero acabó siendo algo más grande. Ahora lo lanzo antes de una llamada comercial, para conocer la verdad de los últimos 30 días sobre una empresa. Antes de una reunión, para leer los tuits recientes y las transcripciones de podcasts de la otra persona. Antes de un viaje a Disney World, para saber qué atracciones están cerradas y qué opina la comunidad sobre Genie+. Antes de construir nada, para saber con qué problemas se está encontrando la gente de verdad.

Si te vas a reunir con un CEO, ¿te has leído todos sus tuits y todas sus transcripciones de YouTube de los últimos 30 días? Yo sí.

## Fuentes, puntuadas por la gente

| Fuente | Lo que te dice la gente |
|--------|--------------------------|
| **Reddit** | La opinión sin filtros. Los mejores comentarios con su recuento real de votos positivos, gratis y sin clave de API. Las opiniones reales que Google entierra. |
| **X / Twitter** | La reacción en caliente, el hilo del experto, la primera respuesta a una noticia de última hora. Los primeros en enterarse, los primeros en discutir. |
| **YouTube** | El análisis a fondo de 45 minutos. Transcripciones completas, rastreadas para sacar las 5 frases citables que importan. |
| **TikTok** | El creador que llega a 3,6 millones de personas con una lectura que nunca encontrarás en Google. |
| **Instagram Reels** | La mirada de los influencers, con transcripción de lo que dicen. La señal de la cultura visual. |
| **Hacker News** | El consenso de los desarrolladores. 825 puntos, 899 comentarios. Donde la gente técnica discute de verdad. |
| **Polymarket** | No son opiniones. Son cuotas. Respaldadas por dinero real. 96 % de probabilidad en ventas de un álbum. 4 % en una adquisición. |
| **GitHub** | Para personas: ritmo de PR, mejores repositorios por estrellas, notas de versión. Para temas: issues y discusiones. |
| **Digg** | Grupos de noticias seleccionados del ranking AI 1000 de Digg (unas 1000 cuentas de IA con mucha señal en X), con citas atribuibles integradas y sin necesidad de autenticarte en X. Se activa solo cuando `digg-pp-cli` está en el PATH. |
| **arXiv** | Los artículos científicos que hay detrás del ruido. Investigación nueva dentro de la ventana, gratis y sin clave de API. Se activa solo cuando `arxiv-pp-cli` está en el PATH (la configuración inicial lo instala). |
| **Techmeme** | La capa editorial de la actualidad tecnológica, acotada a tu ventana de 30 días. Gratis y sin clave de API. Se activa solo cuando `techmeme-pp-cli` está en el PATH (la configuración inicial lo instala). |
| **LinkedIn** | La señal profesional. Publicaciones y artículos, con los artículos ponderados como señal fuerte. |
| **StockTwits** | El sentimiento de los traders. Se activa automáticamente cuando tu tema es un ticker o una criptomoneda. |
| **Threads** | La capa de texto posterior a Twitter. Conversaciones de creadores y marcas. |
| **Pinterest** | Descubrimiento visual. Pines, guardados y comentarios sobre productos e ideas. |
| **Xiaohongshu (RED)** | Señales chinas sobre estilo de vida, productos y creadores. Se pide de forma explícita con `--search xhs` cuando tienes corriendo en local un plugin de navegador x-mcp con sesión iniciada o un servicio `xiaohongshu-mcp`. |
| **Bluesky** | La capa social descentralizada. Publicaciones de AT Protocol surgidas de la migración posterior a Twitter. |
| **Perplexity** | Síntesis controlada con la Agent API, alternativa Sonar mediante OpenRouter, resultados en bruto de la Search API y Deep Research explícita. |
| **Web** | La cobertura editorial, las comparativas de los blogs. Una señal entre muchas, no la única. |

La comunidad no para de sumar fuentes. Truth Social y otras fuentes de nicho ya están en el motor, y vienen más.

Un hilo de Reddit con 1.500 votos positivos es una señal más fuerte que una entrada de blog que no leyó nadie. Un TikTok con 3,6 millones de visualizaciones dice más sobre lo que es culturalmente relevante que cualquier nota de prensa. Unas cuotas de Polymarket respaldadas por 66.000 dólares de volumen son más difíciles de rebatir que la corazonada de un tertuliano.

La síntesis ordena según aquello con lo que la gente real ha interactuado de verdad. Relevancia social, no relevancia SEO.

## Para qué lo usa la gente en realidad

**Antes de una reunión.** `/last30days Peter Steinberger` — se ha incorporado al equipo de Codex de OpenAI, pelea contra el veto de Anthropic a los agentes de terceros, 23 PR mergeadas con un 85 % de tasa de merge en GitHub, construye LobsterOS para controlar agentes entre dispositivos. r/ClaudeCode: «Desde que salió OpenClaw, todo el mundo sabía que, si lo pasabas por algo que no fuera la API, acabarías baneado» (227 votos positivos). Eso no está en LinkedIn.

**Para leer señales de contratación.** `/last30days Listen Labs --hiring-signals` — las ofertas de empleo y las páginas de carreras actuales se convierten en pruebas citadas de un cambio de prioridades: contratación en seguridad para empresa, customer success, infraestructura o expansión de producto. El informe dice lo que la contratación parece señalar, no lo que la hoja de ruta va a entregar.

**Para encontrar el tema antes de su pico.** Pregunta `/last30days what's exploding in AI agents?` y la skill cambia a modo descubrimiento: el motor barre los listados por categoría de Reddit, la portada y las mejores historias de Hacker News, el feed AI 1000 de Digg y X si estás autenticado; tu agente evalúa las candidaturas (nombres, filtrado de ruido, interés real) y escribe enfoques para pódcast o para un artículo en X; después obtienes entre 5 y 10 temas ordenados por velocidad. Cada resultado incluye cifras de varias fuentes, una etiqueta de impulso y un comando `/last30days "<topic>"` listo para lanzar.

**Cuando sale algo nuevo.** `/last30days Kanye West` — el Reino Unido le bloqueó el visado, el Wireless Festival se canceló, los patrocinadores huyeron. Pero BULLY debutó en el número 2 del Billboard. Fantano volvió de su «Yay sabbatical» para reseñarlo (653.000 visualizaciones). En el SoFi Homecoming sacó al escenario a Lauryn Hill y a Travis Scott para 44 canciones. Polymarket: «¿Volverá Kanye a tuitear?» 86 % sí. 23 hilos de Reddit, 17 vídeos de YouTube, 86.000 votos positivos.

**Para comparar herramientas.** `/last30days OpenClaw vs Hermes vs Paperclip` — «No son competidores, son capas.» OpenClaw es la capa de ejecución (351.000 estrellas en GitHub, en producción), Hermes es el cerebro que se mejora a sí mismo (31.000 estrellas), Paperclip es el organigrama (49.000 estrellas). El número de estrellas se saca en directo de la API de GitHub, no de entradas de blog caducadas. Tabla comparativa con arquitectura, memoria, seguridad y caso de uso ideal. Según @IMJustinBrooke: «OpenClaw = Charmander, Hermes = Charizard.»

**Para entender el mundo.** `/last30days Iran vs USA` — día 38 de la guerra. El ultimátum de Trump, con plazo hasta el martes, para que Irán reabra el estrecho de Ormuz. Dos aviones de combate estadounidenses derribados. El petróleo a 126 dólares el barril. La AIE lo calificó como «la mayor interrupción de suministro de la historia del mercado mundial del petróleo». Polymarket: alto el fuego antes del 31 de diciembre al 74 %. 27 publicaciones de X, 10 vídeos de YouTube, 20 mercados de predicción.

**Antes de un viaje.** `/last30days Universal Epic Universe` — la ampliación ya está en obras. Licencia «Project 680» presentada. El espectáculo de fuegos artificiales está confirmado por la infraestructura, pero sin anunciar. Tiempos de espera: Mine-Cart Madness promedia 148 minutos. Todavía no hay pase anual, y los vecinos están hartos. Stardust Racers cerrada por reforma hasta el 5 de abril.

**Para aprender algo rápido.** `/last30days Nano Banana Pro prompting` — los prompts estructurados en JSON están sustituyendo al amontonamiento de etiquetas. El formato anidado de @pictsbyai evita el «concept bleeding». Editar gana a regenerar. Y después te escribe un prompt de producción aplicando exactamente lo que la comunidad ha dicho que funciona.

## Novedades

Desde el anuncio de la v3.3 en mayo y hasta la v3.11.1 (julio de 2026): 175 PR mergeadas —122 de ellas de 52 colaboradores de la comunidad— repartidas en 15 versiones. Esto es lo que ha entrado.

### Ciudadano de primera en OpenAI Codex

/last30days ya es un plugin nativo de Codex con configuración guiada: no es un port, es un ciudadano de primera. Las citas tienen en cuenta el renderizador, así que la salida en Codex se lee como un informe y no como una sopa de URL (#694), y el mismo motor funciona en Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClaw y 50+ hosts de Agent Skills. Manifiesto del plugin de Codex por [@rfoust](https://github.com/rfoust) (#686), corrección de autenticación en Codex por [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmeme y Digg: gratis y sin claves de API

arXiv aporta los artículos científicos que hay detrás del ruido y Techmeme la capa editorial de la actualidad tecnológica: gratis, sin una sola clave, y la configuración inicial instala sus CLI para que se activen solas (#709). Los grupos de noticias AI 1000 de Digg llegan igual, sin autenticarte en X: la configuración instala por ti la CLI gratuita de Digg (#590). Trustpilot está disponible como opción para investigar marcas de consumo.

### Reddit gratis, con puntuaciones reales y mejores comentarios

La API pública .json de Reddit desapareció; la vía gratuita volvió más fuerte. RSS sin clave y scraping de shreddit (#457), descubrimiento de subreddits específicos con recuentos reales de votos positivos vía arctic-shift (#696), y un umbral de relevancia para que una publicación viral fuera de tema no secuestre tu informe (#488, gracias [@rzachsmith](https://github.com/rzachsmith)). Sin clave de API. Puntuaciones reales. Con los mejores comentarios incluidos.

### Los mejores comentarios en cada informe

Los comentarios son ya una capa activada por defecto en todas las fuentes: comentarios de Instagram con diversidad basada en el ranking, para que cinco opiniones rotundas no salgan todas de la misma publicación (#751), comentarios de YouTube más un respaldo de transcripción vía ScrapeCreators para cuando yt-dlp falla (#637), y comentarios votados por la comunidad ponderados dentro de Best Takes, para que las mejores frases sobrevivan a la puntuación (#592, #608).

### Un único comando doctor

Pide una revisión y doctor comprueba todas las fuentes y receta los arreglos exactos: qué clave falta, qué CLI no está en el PATH, qué cookie ha caducado (#753). Se acabó adivinar por qué X ha devuelto tan poco.

### La búsqueda en X, reconstruida

El pipeline de X se rehízo de arriba abajo: carriles FROM y ABOUT para que se posicionen tanto las publicaciones de una persona como la conversación sobre ella (#610), desambiguación de subconsultas según la persona buscada (#611), verificación de la autoría de primera mano con ranking por señales de interacción (#613), y una única fuente X con conmutación automática entre backends (#622). Además, un `--diagnose` honesto que comprueba de verdad la autenticación (#609).

### Se han sumado más fuentes

LinkedIn vía ScrapeCreators, con los artículos como señal fuerte ([@ravstr](https://github.com/ravstr), #702). StockTwits se activa automáticamente en temas de tickers y cripto ([@wtiwana](https://github.com/wtiwana), #658). Perplexity ha ganado modos de API directos y Deep Research asíncrono ([@sk-holmes](https://github.com/sk-holmes), #629).

### Endurecido por la comunidad

La oleada de seguridad fue casi por completo trabajo de la comunidad: correcciones de XSS almacenado en el renderizador HTML ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), archivos temporales de cookies blindados, CI endurecida frente a ataques a la cadena de suministro con OpenSSF Scorecard y atestación de procedencia de las builds ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), análisis con Semgrep y OSV-Scanner más un control de revisión de dependencias en cada PR ([@23241a6749](https://github.com/23241a6749)), un mínimo de cobertura de pruebas fijado al 60 % y elevado desde entonces al 84 % ([@gourab5139014](https://github.com/gourab5139014)), y un análisis de seguridad de Hermes que ya no arroja ningún hallazgo CRITICAL (#768).

### Llega más lejos

Hebreo y otros idiomas no latinos ([@dudyme](https://github.com/dudyme)). Tokenización adaptada a CJK para las fuentes chinas ([@An-idd](https://github.com/An-idd)). Una oleada de compatibilidad con Windows. Extracción de cookies en toda la familia Chromium —Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov))— además del llavero de macOS y pass(1) en Linux como orígenes de credenciales. Consulta histórica hacia atrás con `--as-of` ([@chiyi-creator](https://github.com/chiyi-creator)). Instalación automática de Python 3.12 mediante uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` para leer las páginas de empleo de una empresa. Diferencias de la lista de seguimiento entre ejecuciones.

### Lo que ya venía de serie desde la v3

Los cimientos de la v3 siguen todos aquí: el cerebro previo a la investigación, que identifica las cuentas, subreddits y hashtags correctos antes de que salga una sola llamada a la API (obra de [@j-sperling](https://github.com/j-sperling)); la puntuación Best Takes, que valora el humor y la viralidad además de la relevancia; la fusión de clústeres entre fuentes; las comparativas en una sola pasada («CLI vs MCP» en 3 minutos, no en 12); las comparativas `--competitors` descubiertas de forma automática; el modo persona de GitHub (`--github-user=steipete`); el modo ELI5 («eli5 on» después de cualquier ejecución); y los informes HTML autocontenidos y compartibles (`--emit=html`). Los ajustes de configuración están en [CONFIGURATION.md](CONFIGURATION.md).

## Instalación

| Entorno | Instalación | Actualizaciones |
|---------|---------|---------|
| **Claude Code** (recomendado) | `/plugin marketplace add mvanhorn/last30days-skill` | Automáticas vía marketplace, o `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` y después `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI, o cualquiera de los 50+ hosts de [Agent Skills](https://agentskills.io)** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [Descarga `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) y súbelo desde claude.ai > Customize > Skills > + > Create skill > Upload a skill | Volver a descargar y volver a subir |
| **Claude Desktop** | [Descarga el `.mcpb` de tu plataforma](https://github.com/mvanhorn/last30days-skill/releases/latest) y arrástralo a Settings > Extensions | Volver a descargar y arrastrar el nuevo paquete |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recomendado)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Es la opción recomendada porque el marketplace de Claude Code se encarga de las actualizaciones por ti: la caché del plugin está versionada y se refresca sola cuando se publica una versión nueva. Ejecuta `claude plugin update last30days@last30days-skill` para forzar una comprobación.

Si prefieres usar la vía de instalación de Agent Skills en Claude Code, también está soportada:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

El plugin nativo y la instalación con `npx skills` pueden convivir. Ojo: Claude Code no deduplica entre métodos de instalación. Si tienes activos a la vez el plugin del marketplace y la copia de `npx skills`, `/last30days` aparecerá dos veces. Usa un solo método de instalación por máquina.

### Grok (xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) instala last30days como plugin nativo. La instalación directa sigue el repositorio:

```bash
grok plugin install mvanhorn/last30days-skill
```

O añade este repositorio como fuente de marketplace y luego instálalo por nombre de plugin:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Añade `--trust` para saltarte la confirmación de instalación. Actualiza con `grok plugin update last30days`. Grok también lee los manifiestos de Claude Code por compatibilidad; el par nativo `.grok-plugin/` es la vía principal, y es a lo que apunta una entrada oficial en el [marketplace de xAI](https://github.com/xai-org/plugin-marketplace). `npx skills add` sigue siendo una alternativa válida para cualquier host.

### Codex, Cursor, Copilot, Gemini CLI y otros hosts de Agent Skills

Instálalo con la CLI abierta de [Agent Skills](https://agentskills.io): soporta 50+ hosts, entre ellos `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose` y más (lista completa en el [repositorio vercel-labs/skills](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

El flag `-g` (global) instala en tu directorio de usuario, de modo que la skill queda disponible en todos los proyectos. Sin `-g`, `npx skills` instala solo en el proyecto, dentro de `./.skills/` (y se versiona con el repositorio). Para una herramienta que sirve para investigar el mundo entero, lo que quieres es la instalación global.

Codex de escritorio y otros hosts que trabajan a nivel de carpeta funcionan tanto en carpetas normales como en repositorios Git. Antes de la primera investigación, pídele al agente anfitrión que ejecute el `scripts/last30days.py --preflight` incluido desde el directorio de la skill cargada; en un clon del código fuente, el comando equivalente es `python3 skills/last30days/scripts/last30days.py --preflight`. Te muestra de dónde sale la configuración, qué cookies del navegador se leerían, qué archivos se escribirían, qué comandos opcionales hay y qué configuración de proyecto se ignora, todo ello sin leer cookies, sin escribir archivos y sin lanzar ninguna investigación.

Por defecto se instala para el host que detecte `npx skills`. Para apuntar a uno concreto (o a varios):

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Para actualizar más adelante:

```bash
npx skills update last30days -g
```

O actualiza todo lo que hayas instalado globalmente con `npx skills`:

```bash
npx skills update -g
```

Puedes listarlo y desinstalarlo con `npx skills list -g` y `npx skills remove last30days -g`.

### claude.ai (web)

1. [Descarga `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) de la última versión publicada
2. Entra en [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Pulsa el botón `+` del panel de Skills, luego `Create skill` > `Upload a skill`, y busca o arrastra el archivo

Activa antes «Code execution and file creation» en Capabilities: sin eso, las skills no se ejecutan.

### Claude Desktop

Claude Desktop instala `/last30days` como servidor MCP mediante un paquete `.mcpb` (un paquete de Model Context Protocol de un solo clic).

1. Entra en la [última versión publicada](https://github.com/mvanhorn/last30days-skill/releases/latest) y descarga el `.mcpb` de tu plataforma:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Abre Claude Desktop, ve a Settings > Extensions y arrastra el archivo ahí.
3. Cuando te las pida, pega las claves de API de las fuentes que quieras activar. Todos los campos son opcionales: si los saltas todos, el motor se queda en modo solo web. Las claves se guardan en el llavero de tu sistema operativo.
4. Reinicia Claude Desktop. Pídele a Claude que «investigue a Peter Steinberger», o cualquier otro tema, y llamará a la herramienta `research`.

**Requisito del anfitrión:** Python 3.12+ en el PATH. El paquete incluye el código del motor, pero usa tu intérprete de Python local. En Windows, instálalo desde [python.org](https://www.python.org/downloads/); macOS y la mayoría de distribuciones de Linux ya traen una versión compatible.

**Las claves no se comparten con la skill de Claude Code.** Claude Desktop y Claude Code mantienen almacenes de credenciales separados a propósito. Si ya configuraste `~/.config/last30days/.env` para la skill de Claude Code, aquí tendrás que introducir esas mismas claves una vez.

La compatibilidad con Windows queda aplazada hasta resolver los puntos de entrada por plataforma del manifiesto; el seguimiento se hace en una incidencia aparte.

### OpenClaw

```bash
clawhub install last30days-official
```

Para flujos de acción en X/Twitter fuera de la investigación de `/last30days` —publicar
tuits o respuestas, exportar seguidores, gestionar medios, monitorizar cuentas y
resolver sorteos— usa [TweetClaw](https://github.com/Xquik-dev/tweetclaw) como
plugin complementario de OpenClaw. TweetClaw lo mantiene Xquik-dev y aparece aquí
únicamente como opción complementaria: no es una dependencia ni una recomendación
de last30days.

### Manual (para desarrolladores)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

El enlace simbólico mantiene la instalación sincronizada con tu copia de trabajo a medida que editas, sin necesidad de volver a copiar nada. Para `claude.ai`, compila el archivo `.skill` desde el código fuente: `bash skills/last30days/scripts/build-skill.sh` genera `dist/last30days.skill`.

Reddit (con comentarios), Hacker News, Polymarket y GitHub funcionan de inmediato. Cero configuración. Ejecuta `/last30days` una vez y el asistente de configuración desbloquea más fuentes en 30 segundos, incluidas las CLI gratuitas de arXiv y Techmeme.

## Aporta tus propias claves

Estas plataformas no tienen ninguna relación entre sí. X no sabe lo que piensa Reddit. YouTube no ve TikTok. Pero tú puedes aportar tus claves de API y tus tokens de navegador y, de golpe, tienes acceso a todas a la vez.

| Fuentes | Lo que necesitas | Coste |
|---------|---------------|------|
| Reddit (con comentarios) + HN + Polymarket + GitHub + StockTwits | Nada | Gratis |
| arXiv + Techmeme | CLI gratuitas, instaladas automáticamente por la configuración inicial | Gratis |
| X / Twitter | Inicia sesión en x.com en cualquier navegador, o define `XQUIK_API_KEY` / `XAI_API_KEY` | Las cookies del navegador son gratis; las claves dependen del proveedor |
| YouTube | `brew install yt-dlp` | Gratis |
| Bluesky | Una contraseña de aplicación de bsky.app | Gratis |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + comentarios de YouTube | Una clave de ScrapeCreators | 10.000 llamadas gratis y luego pago por uso |
| Xiaohongshu (RED) | Ten corriendo un plugin de navegador x-mcp con sesión iniciada o un servicio `xiaohongshu-mcp`, y activa la fuente con `--search xhs` por ejecución o con `INCLUDE_SOURCES=xiaohongshu` en `.env`; last30days prueba automáticamente `http://localhost:18060` y después `http://host.docker.internal:18060`, o usa `XIAOHONGSHU_API_BASE` para una URL propia | No hace falta clave de API de last30days; depende de tu servicio local de sesión de navegador |
| DripStack (boletines financieros premium) | Opcional: `--search dripstack` por ejecución, o `INCLUDE_SOURCES=dripstack` en `.env` | Sin clave; API de búsqueda pública y gratuita |
| Perplexity Agent API / Search API / Deep Research | Una clave de Perplexity, o una clave de OpenRouter como alternativa para Sonar | Pago por uso; una clave directa habilita la Agent API y Deep Research en segundo plano |
| Búsqueda web | Una clave de Brave Search | 2.000 consultas gratis al mes |

### Llavero de macOS (opcional)

En macOS puedes guardar las claves en el llavero del sistema en lugar de en un archivo `.env`. La skill las recoge automáticamente como la fuente de menor prioridad: si hay conflicto, siguen ganando los archivos `.env` y el entorno del proceso.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Las entradas se guardan con el nombre de servicio `last30days-<KEY>` para el usuario actual. En plataformas que no son Darwin el cargador no hace nada, así que para quienes usan Linux o Windows no cambia el comportamiento.

¿Ya tienes claves guardadas con otros nombres de servicio en el llavero? Define el mapeo no secreto `LAST30DAYS_KEYCHAIN_ALIASES` que se describe en [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items), en lugar de copiar secretos.

Consulta [CONFIGURATION.md](CONFIGURATION.md) para ver la matriz completa de claves por fuente, el orden de prioridad de los proveedores de razonamiento y el de los backends de búsqueda web.

## Configuración

Dos cosas que seguramente querrás saber desde el primer día:

**Dónde se guardan los archivos de investigación.** `LAST30DAYS_MEMORY_DIR` apunta por defecto a `~/Documents/Last30Days/` (en Windows: `C:\Users\<you>\Documents\Last30Days\`). Puedes cambiarlo definiendo esa variable de entorno en tu shell con la ruta que quieras, o con `--save-dir <path>` en una ejecución concreta. Usa `--output <file>` cuando necesites el resultado renderizado en una ruta exacta, con el formato que elijas en `--emit`. Usa `--save-suffix=<name>` para mantener separadas varias variantes del mismo tema (por cliente, por ejemplo). Cada ejecución con `--save-dir` genera `<slug>-raw[-suffix].md`. Ejecuta `python3 skills/last30days/scripts/last30days.py --preflight` para revisar qué se va a escribir antes de lanzar una investigación.

**Salida estructurada para agentes y flujos de trabajo.** Pídele a `/last30days` JSON legible por máquina y obtendrás el perfil de agente estable y versionado. Para usar el motor directamente en scripts o en desarrollo, ejecuta `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; añade `--json-profile=raw` solo si necesitas el volcado interno sin versionar de `Report`. Consulta la [referencia de campos de la exportación JSON y la política de versionado](docs/reference/json-export.md).

**Descubrimiento sin tema.** Pregunta `/last30days what's trending in AI agents?` para obtener un informe de descubrimiento ordenado, en lugar de investigar un tema que ya conoces. En un host con agente esto ejecuta el protocolo de tres comandos arbitrado por el host (el modelo propone los temas, filtra el ruido, puntúa lo que merece la pena y escribe los enfoques de contenido). Para usar el motor directamente en scripts o en cron, ejecuta `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (una sola pasada: nombres de tema deterministas, sin enfoques); añade `--emit=json` para el contrato de descubrimiento versionado. El descubrimiento es incompatible con un tema posicional y con `--drill`.

**Seguimiento de tendencias entre ejecuciones.** El modo por defecto genera una instantánea Markdown nueva en cada ejecución. Para ir acumulando hallazgos con el tiempo, añade `--store` y se guardarán en una base de datos SQLite; después usa [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) para ejecuciones programadas (con envío opcional por Slack o webhook cuando aparezcan hallazgos nuevos) y [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) para resúmenes diarios o semanales. El patrón de cadencia completo está en [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Una biblioteca de investigación a la que suscribirse.** Pídele a `/last30days` que genere el feed de tu biblioteca, o usa directamente `python3 skills/last30days/scripts/last30days.py library feed` para scripting y desarrollo. Convierte los informes guardados en un `index.html`, un `feed.xml` Atom local y páginas de informe legibles. Añade `--publish` solo cuando quieras alojar el índice HTML y las páginas de informe; publicar es una decisión explícita y por defecto es público. Para que el feed Atom se pueda seguir de verdad, aloja el directorio de salida generado en un alojamiento estático como GitHub Pages.

**Busca en todo lo que ya has investigado.** Pregunta `/last30days search my library for MCP servers` o `/last30days have I researched MCP servers before?`. Para usar el motor directamente, ejecuta `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. La búsqueda es offline y determinista: indexa de forma incremental los mismos informes guardados que usa el feed de la biblioteca, fusiona las coincidencias registradas en el almacén de cada ejecución y agrupa los resultados por tema y fecha. Las ejecuciones nuevas muestran además una sección compacta **From your library** («desde tu biblioteca») cuando una investigación anterior se solapa con el tema actual; define `LAST30DAYS_LIBRARY_CONTEXT=off` para desactivar ese contexto pasivo.

Los scripts envoltorio por cliente, los subreddits de categoría personalizados y el canal beta experimental para personalizaciones en curso también están documentados en [CONFIGURATION.md](CONFIGURATION.md).

## Escaparate: feeds de investigación de la comunidad

¿Has publicado con last30days una actualización periódica sobre IA, un seguimiento de mercado o una obsesión maravillosamente específica? Comparte la URL de tu biblioteca pública —o la URL de Atom, una vez alojado `feed.xml` en un alojamiento estático— en [el hilo de escaparate de la comunidad](https://github.com/mvanhorn/last30days-skill/issues/532). Los feeds de la comunidad se irán enlazando aquí a medida que sus autores los envíen; mientras tanto, el hilo es el punto de recogida.

## Cómo funciona

1. **Escribes un tema.** Una persona, una empresa, un producto, una tecnología, «X vs Y». Lo que sea.
2. **El agente averigua quién importa.** Encuentra las cuentas de X (incluidas las de fundadores), los repositorios de GitHub, los subreddits, los hashtags de TikTok y los canales de YouTube. Para «Kanye West» sabe que hay que mirar r/hiphopheads, @kanyewest y «bully review» en YouTube. Para «OpenClaw» resuelve openclaw/openclaw en GitHub y trae el número de estrellas en directo.
3. **Todas las fuentes se consultan en paralelo.** Expansión con varias consultas. Resultados puntuados por interacción, relevancia y frescura.
4. **La profundidad que no tiene nadie más.** Transcripciones completas de YouTube de vídeos de reacción. Los mejores comentarios de Reddit con su recuento de votos positivos. Los textos de los TikTok. Las cuotas de Polymarket. No solo títulos y enlaces.
5. **La misma historia, fusionada.** El Wireless Festival anunciado en Reddit, comentado en X y con los precios de las entradas en TikTok: un solo clúster, no tres entradas distintas.
6. **Sintetizado en un único informe.** Anclado en datos concretos. Citado por fuente. Ordenado según aquello con lo que la gente interactúa de verdad. No es «esto es lo que he encontrado», es «esto es lo que importa».
7. **Y después se convierte en tu experto.** Tras una sola ejecución, tu sesión de Claude sabe todo lo que sabe la comunidad. Haz preguntas de seguimiento. Pídele que escriba prompts, redacte correos, planifique viajes o diseñe arquitecturas, todo anclado en lo que es real ahora mismo.

## Lo que dice la gente

> «He encontrado una skill de Claude Code que investiga cualquier tema en Reddit, X, YouTube y HN de los últimos 30 días. Y luego te escribe los prompts. Antes de cada contenido que escribo, hacía esa búsqueda a mano en Reddit y X. Pestaña a pestaña. Hilo a hilo. Esa es la parte que se lleva 90 minutos. Esto la elimina.» —@itsjasonai

> «Esta única skill ha sustituido todo mi flujo de investigación. Le das un tema y rastrea Reddit, X y la web para sacar de qué está hablando la gente de verdad. Nada de entradas de blog viejas. Conversaciones reales de los últimos 30 días.» —@itswilsoncharles

> «5 de los 10 repos en tendencia hoy en GitHub son herramientas de Claude. El número 1: mvanhorn/last30days-skill» —@yieldhunter95

## Código abierto

Licencia MIT. Sin rastreo. Sin analíticas. Tu investigación se queda en tu máquina. Más de 2.700 pruebas.

Construido con Python 3.12+, yt-dlp, Node.js (cliente Bird incorporado para la búsqueda en X) y la API de ScrapeCreators. Arquitectura del motor v3 de [@j-sperling](https://github.com/j-sperling).

Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para abrir un PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) para la lista completa de colaboradores de la comunidad y [CHANGELOG.md](CHANGELOG.md) para el historial de versiones.

## Evolución de las estrellas

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
