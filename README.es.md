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

**Un motor de búsqueda liderado por agentes de IA, puntuado por votos positivos, me gusta y dinero real, no por editores.**

Este README rastrea la pipeline v3 actual. La especificación de habilidad en tiempo de ejecución reside en [skills/last30days/SKILL.md](skills/last30days/SKILL.md), que es la fuente de verdad para el comportamiento más reciente de comandos y configuración.

**Claude Code (recomendado — actualizaciones automáticas vía marketplace):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI, o cualquiera de los 50+ [Agent Skills](https://agentskills.io) presentadores:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` se instala globalmente para tu usuario, disponible en todos los proyectos. Hazlo por proyecto por ámbito.)

Más opciones de instalación (claude.ai web, OpenClaw, manual) en la sección de [Install](#instalación) abajo.

Cero configuración. Reddit, HN, Polymarkety GitHub funcionan inmediatamente. Ejecuta una vez y el asistente de configuración desbloquea X, YouTube, TikTok, arXiv, Techmemey más en 30 segundos.

---

Reddit votos positivos. X me gusta. YouTube transcripciones. TikTok interacción. Polymarket probabilidades respaldadas por dinero real e información privilegiada. Eso son millones de personas votando con su atención y su cartera cada día. /last30days lo busca todo en paralelo, lo puntua según lo que realmente interactúan las personas reales, y un juez agente de IA lo sintetiza en un solo informe.

Google agrega editores. /last30days busca personas.

No puedes encontrar esta búsqueda en ningún otro sitio porque ninguna IA tiene acceso a todas. Google búsqueda no toca Reddit comentarios ni X publicaciones. ChatGPT tiene un acuerdo con Reddit pero no puede buscar X ni TikTok. Gemini tiene YouTube pero no Reddit. Claude no tiene ninguno de ellos de forma nativa. Cada plataforma es un jardín amurallado con su propio API, sus propios tokens, su propia autenticación. Pero puedes traer tus propias claves y sesiones de navegador, y de repente un agente de IA puede buscarlas todas a la vez, puntuarlas entre sí y decirte qué es lo que realmente importa.

Ese es el desbloqueo. No hay un motor de búsqueda mejor. Una docena de plataformas desconectadas, conectadas por un agente.

```
/last30days Peter Steinberger
```

Tienes una reunión mañana. Les Google a ellos. Obtienes su LinkedIn de 2023. /last30days te da lo que realmente están haciendo este mes: se unió a OpenAI para trabajar en Codex, luchar contra la prohibición de Anthropic sobre agentes externos, enviar 23 PRs con un 85% de tasa de fusión, construir "LobsterOS" para control de agentes entre dispositivos, y r/ClaudeCode alcanzó 569 votos positivos debatiendo si es un héroe o "insoportable". Dispersos por X publicaciones, hilos de Reddit , transcripciones de YouTube y GitHub commits. Nada de eso estaba en Google.

## Por qué existe esto

Lo construí para mantenerme al día en IA. Todo cambia cada día y los frikis de Reddit y X siempre están al pendiente primero. Necesitaba mejores indicaciones, y los datos de entrenamiento siempre iban meses por detrás de lo que la comunidad ya había descubierto.

Pero se convirtió en algo más grande. Ahora lo hago antes de una llamada de ventas para saber la verdad de los últimos 30 días sobre un negocio. Antes de una reunión para leer los tuits recientes y las transcripciones de podcasts de alguien. Antes de un viaje Disney World para saber qué atracciones están cerradas y qué dice la comunidad sobre Genie+. Antes de construir nada para saber qué problemas están enfrentando realmente las personas.

Si te reúnes con un CEO, ¿has leído todos sus tuits y transcripciones de YouTube de los últimos 30 días? Sí.

## Fuentes, puntuadas por el pueblo

| Fuente | Lo que te dice la gente |
|--------|--------------------------|
| **Reddit** | La opinión sin filtros. Mejores comentarios con recuentos reales de votos, gratis, sin API clave. Las opiniones reales que Google entierra. |
| **X / Twitter** | La opinión polémica, el hilo de expertos, la reacción de rompimiento. Primero en saber, primero en discutir. |
| **YouTube** | La inmersión profunda de 45 minutos. En las transcripciones completas buscaron las 5 frases memorables que importan. |
| **TikTok** | El creador llega a 3,6 millones de personas con una opinión que nunca encontrarás en Google. |
| **Instagram Reels** | La perspectiva del influencer con transcripciones habladas. La señal cultural visual. |
| **Hacker News** | El consenso de los desarrolladores. 825 puntos, 899 comentarios. Donde los técnicos realmente discuten. |
| **Polymarket** | No opiniones. Probabilidades. Respaldado por dinero real. 96% de confianza en las ventas del álbum. 4% en una adquisición. |
| **GitHub** | Para la gente: PR Velocity, repositorios principales por estrellas, notas de lanzamiento. Para temas: temas y debates. |
| **Digg** | Agrupaciones de historias seleccionadas de la tabla de clasificación AI 1000 de Digg(~1000 cuentas de IA de alta señal en X), con comillas en línea atribuibles (sin necesidad de autenticación X ). Autoactivado cuando `digg-pp-cli` está en PATH. |
| **arXiv** | Los papeles detrás del bombo. Nueva investigación en la ventana, gratis, sin API clave. Autoactivado cuando `arxiv-pp-cli` está en PATH (la primera configuración lo instala). |
| **Techmeme** | La capa editorial de noticias tecnológicas, con fecha de 30 días. Gratis, sin API clave. Activado automáticamente cuando `techmeme-pp-cli` está en PATH (la primera ejecución lo instala). |
| **LinkedIn** | La señal profesional. Publicaciones y artículos, con los artículos ponderados como alta señal. |
| **StockTwits** | Sentimiento del trader. Se activa automáticamente cuando tu tema es un ticker o una criptomoneda. |
| **Threads** | La capa de texto post-Twitter. Conversaciones de creadores y marcas. |
| **Pinterest** | Descubrimiento visual. Pines, guarda y comenta productos e ideas. |
| **Xiaohongshu (RED)** | Señales de estilo de vida, producto y creador chino. Solicitado explícitamente con `--search xhs` cuando un plugin de navegador x-mcp o servicio de `xiaohongshu-mcp` iniciado sesión está ejecutándose localmente. |
| **Bluesky** | La capa social descentralizada. Publicaciones del Protocolo AT desde la migración posterior a Twitter. |
| **Perplexity** | Síntesis de sonar en tierra, filas de búsqueda API puro e investigación profunda. |
| **Web** | La cobertura editorial, las comparaciones en blogs. Una señal entre muchas, no la única. |

Los colaboradores de la comunidad siguen añadiendo más. Truth Social y otras fuentes de nicho están en el motor con más en camino.

Un hilo Reddit con 1.500 votos positivos es una señal más fuerte que una entrada de blog que nadie ha leído. Un TikTok con 3,6 millones de visualizaciones te dice más sobre lo que es culturalmente relevante que una nota de prensa. Polymarket cuotas respaldadas por 66.000 dólares en volumen son más difíciles de discutir que la suposición de un experto.

La síntesis se clasifica según lo que realmente se relacionó la gente real. Relevancia social, no SEO relevancia.

## Para qué la gente realmente lo usa

**Antes de una reunión.** `/last30days Peter Steinberger` - se unió al equipo Codex de OpenAI, luchando contra la prohibición de agentes externos de Anthropic, 23 PRs fusionados con un 85% de tasa de fusión en GitHub, construyendo LobsterOS para el control de agentes entre dispositivos. Código r/Claude: "Desde que OpenClaw se lanzó, era bien sabido que si lo pasabas por cualquier cosa que no fuera la API, acabarían siendo baneados" (227 votos positivos). Eso no es culpa LinkedIn.

**Para leer señales de contratación.** `/last30days Listen Labs --hiring-signals` - las páginas actuales de empleos y carreras se citan como evidencias de cambios de enfoque: contratación hacia seguridad empresarial, éxito del cliente, infraestructura o expansión de productos. El informe dice lo que parece señalar la contratación, no lo que la hoja de ruta presentará.

**Para encontrar el tema antes de que alcance su punto máximo.** Pregúntale `/last30days what's exploding in AI agents?` y la habilidad cambia a modo descubrimiento: el motor barre Reddit listados de categorías, Hacker News historias principales/mejores, el feed AI 1000 de Diggy X cuando está autenticado; tu agente juzga las nominaciones (nombres, filtrado basura, calidad de contenido) y escribe podcast / X-ángulos de artículo; luego obtienes de 5 a 10 temas clasificados por velocidad. Cada resultado incluye números de fuentes cruzadas, una etiqueta de impulso y un seguimiento `/last30days "<topic>"` listo para publicarse.

**Cuando algo cae.** `/last30days Kanye West` - Reino Unido bloqueó su visado, el Wireless Festival canceló, los patrocinadores huyeron. Pero BULLY debutó en el puesto #2 en Billboard. Fantano volvió de su "Yay sabático" para reseñarlo (653.000 visualizaciones). SoFi Homecoming trajo a Lauryn Hill y Travis Scott para 44 canciones. Polymarket: «¿Volverá a tuitear Kanye?» 86% Sí. 23 hilos Reddit , 17 vídeos YouTube , 86.000 votos positivos.

**Para comparar herramientas.** `/last30days OpenClaw vs Hermes vs Paperclip` - "Estos no son competidores, son capas." OpenClaw es el albacea (351K GitHub estrellas, en directo), Hermes es el cerebro que se auto-mejora (31K estrellas), Paperclip es el organigrama (49K estrellas). Recuento de estrellas extraído en directo del GitHub API, no entradas de blog obsoletas. Mesa lado a lado con arquitectura, memoria, seguridad, lo mejor para. Por @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard."

**Para entender el mundo.** `/last30days Iran vs USA` - Día 38 de la guerra. La fecha límite del martes de Trump para que Irán reabra el Estrecho de Ormuz. Dos aviones de guerra estadounidenses derribados. Petróleo a 126 dólares por barril. La AIE lo calificó como "la mayor interrupción del suministro en la historia del mercado global del petróleo." Polymarket: alto el fuego para el 31 de diciembre al 74%. 27 publicaciones X , 10 vídeos YouTube , 20 mercados de predicción.

**Antes de un viaje.** `/last30days Universal Epic Universe` - Expansión ya en construcción. Permiso "Proyecto 680" presentado. Espectáculo de fuegos artificiales confirmado por infraestructura pero sin avisar. Tiempos de espera: Locura de vagonetas de mina de media 148 minutos. Aún no hay pase anual y los locales están frustrados. Stardust Racers en reformas hasta el 5 de abril.

**Para aprender algo rápido.** `/last30days Nano Banana Pro prompting` - Los prompts estructurados JSONestán reemplazando a la sopa de etiquetas. El formato anidado de @pictsbyaievita el "sangrado conceptual". El flujo de trabajo de edición primero vence a la regeneración. Luego te escribe un prompt de producción usando exactamente lo que la comunidad dijo que funciona.

## Qué hay de nuevo

Desde el anuncio de la v3.3 en mayo, a partir de la v3.11.1 (julio de 2026): 175 se fusionaron PRs —122 de 52 colaboradores de la comunidad— en 15 lanzamientos. Esto fue lo que me consiguió.

### Primera clase en OpenAI Codex

/last30days ahora es un plugin nativo de Codex con configuración guiada: no es un puerto, es un ciudadano de primera clase. Las citas conscientes del renderizador hacen que Codex salida se lea como un brief en lugar de una sopa de URL (#694), y el mismo motor funciona en Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClawy 50+ hosts Agent Skills . Codex manifiesto del plugin por [@rfoust](https://github.com/rfoust) (#686), Codex corrección de autenticación por [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmemey Digg - gratis, sin llaves API

arXiv trae a los periódicos detrás del bombo y Techmeme trae la capa editorial de noticias tecnológicas - gratis, cero llaves, y la configuración de primera ejecución instala sus CLIpara que se activen automáticamente (#709). Los clusters de historias AI 1000 de Diggllegan sin X autenticación de la misma manera: configuración instala el Digg CLI gratuito para ti (#590). Trustpilot se lanza opt-in para investigación de marca de consumo.

### Free Reddit ha hecho crecer puntuaciones reales y comentarios destacados

Reddit.json API pública murió; el camino gratuito volvió más fuerte. RSS sin clave + scraping de shreddit (#457), descubrimiento de subreddit dedicado con conteos reales de votos positivos vía arctic-shift (#696), y un piso de relevancia para que una publicación viral fuera de tema no pueda secuestrar tu brief (#488, gracias [@rzachsmith](https://github.com/rzachsmith)). Sin clave API . Puntuaciones reales. Comentarios principales incluidos.

### Los mejores comentarios en cada informe

Los comentarios ahora son una capa predeterminada en todas las fuentes: comentarios de Instagram con diversidad basada en el ranking, así que cinco opiniones polémicas no provienen todas de una sola publicación (#751), YouTube comentarios más una copia de seguridad ScrapeCreators de transcripción para cuando YT-DLP se pone fuera (#637), y comentarios votados por el público ponderados en Best Takes para que las líneas más divertidas de la comunidad sobrevivan a la puntuación (#592, #608).

### Orden de un médico

Pide un chequeo médico y el médico revisa todas las fuentes, luego prescribe soluciones exactas: qué clave falta, cuál CLI está desajustada PATH, qué cookie caducó (#753). No más adivinanzas por qué X salió floja.

### X búsqueda, reconstruida

La X pipeline recibió una renovación completa: carriles FROM y ABOUT, así que las publicaciones propias de una persona y la conversación sobre ambas se posicionan (#610), desambiguación de subconsultas conscientes de la persona (#611), puesta a tierra de autoría de primera mano con ranking de señal de interacción (#613), y una única fuente X con falla automática de backend (#622). Además, un `--diagnose` honesto que realmente sondea la autenticación (#609).

### Se sumaron más fuentes

LinkedIn vía ScrapeCreators, con artículos como high signal ([@ravstr](https://github.com/ravstr), #702). StockTwits se activa automáticamente para temas de tickers y cripto ([@wtiwana](https://github.com/wtiwana), #658). Perplexity creció directamente en modos API y async Deep Research ([@sk-holmes](https://github.com/sk-holmes), #629).

### Endurecidos por la comunidad

La ola de seguridad consistía casi en su totalidad en trabajo comunitario: correcciones XSS almacenadas en el renderizador de HTML ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), archivos temporales de cookies bloqueados, CI reforzado en cadena de suministro con OpenSSF Scorecard y atestación de procedencia de compilación ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), escaneos Semgrep y OSV-Scanner más una PR puerta de revisión de dependencias ([@23241a6749](https://github.com/23241a6749)), un piso de cobertura de pruebas introducido al 60% y desde entonces elevado al 84% ([@gourab5139014](https://github.com/gourab5139014)), y un escaneo de seguridad Hermes eliminado de todos los hallazgos CRÍTICOS (#768).

### Llega más lejos

Hebreo y lenguas no latinas ([@dudyme](https://github.com/dudyme)). Tokenización consciente de CJKpara fuentes chinas ([@An-idd](https://github.com/An-idd)). Una ola de compatibilidad Windows . Extracción de cookies a lo largo de toda la familia Chromium - Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) - además de fuentes de credenciales macOS Keychain y Linux pass(1). `--as-of` revisión histórica ([@chiyi-creator](https://github.com/chiyi-creator)). Provisionado automáticamente Python 3.12 vía uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` para leer las páginas de empleo de una empresa. Deltas de la lista de seguimiento entre ejecuciones.

### Sigue en la caja de la v3

Las bases de la v3 siguen aquí: el cerebro previo a la investigación que resuelve los handles, subreddits y hashtags correctos antes de que se active una sola llamada de API (creado por [@j-sperling](https://github.com/j-sperling)); Best Takes puntuación para humor y viralidad junto con relevancia; fusión de clústeres entre fuentes; comparaciones de un solo pase ("CLI vs MCP" en 3 minutos, no en 12); comparaciones `--competitors` descubiertas automáticamente; GitHub modo persona (`--github-user=steipete`); modo ELI5 ("eli5 activado" tras cualquier partida); y HTML briefs compartidos y autocontenidos (`--emit=html`). Los mandos de configuración están en [CONFIGURATION.md](CONFIGURATION.md).

## Instalación

| Superficie | Instalación | Actualizaciones |
|---------|---------|---------|
| **Claude Code**(recomendado) | `/plugin marketplace add mvanhorn/last30days-skill` | Auto a través del marketplace, o `claude plugin update last30days@last30days-skill` |
| **Grok**( CLIde construcción xAI ) | `grok plugin marketplace add mvanhorn/last30days-skill` entonces `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLIo cualquiera de los 50+ [Agent Skills](https://agentskills.io) presentadores** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai**(web) | [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) y sube a través de claude.ai > Personalizar > habilidades > + > Crear habilidades > Subir una habilidad | Volver a descargar y volver a subir |
| **Claude Desktop** | [Download the `.mcpb` for your platform](https://github.com/mvanhorn/last30days-skill/releases/latest) y arrastra a Configuración > Extensiones | Vuelve a descargar y arrastra el nuevo paquete |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recomendado)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Recomendado porque el marketplace de Claude Code se encarga de las actualizaciones por ti: la caché del plugin está versionada y se actualiza automáticamente cuando se publica una nueva versión. Ejecuta `claude plugin update last30days@last30days-skill` para forzar una comprobación.

Si prefieres usar la ruta de instalación de agent-skills en Claude Code, eso también está soportado:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

El plugin nativo y la instalación `npx skills` pueden coexistir. Ten en cuenta que Claude Code no se desduplica entre métodos de instalación: si tienes activas tanto el plugin del marketplace como la copia `npx skills` , `/last30days` mostrará dos entradas. Usa un método de instalación por máquina.

### Grok ( CLIde la construcción xAI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) se instala en dur30days como un plugin nativo. La instalación directa rastrea el repositorio:

```bash
grok plugin install mvanhorn/last30days-skill
```

O añadir este repositorio como fuente del marketplace y luego instalar por nombre del plugin:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Añade `--trust` para saltarse la confirmación de instalación. Actualizar con `grok plugin update last30days`. Grok también lee los manifiestos de Claude Code para garantizar la compatibilidad; el par nativo de `.grok-plugin/` es el carril de primera clase (y lo que indica un listado oficial de [xAI marketplace](https://github.com/xai-org/plugin-marketplace) ). `npx skills add` sigue siendo un respaldo válido entre hosts.

### Codex, Cursor, Copilot, Gemini CLIy otros anfitriones Agent Skills

Instala a través del [Agent Skills](https://agentskills.io) CLI abierto — soporta 50+ arneses incluyendo `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose`y más (lista completa en el [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

La bandera `-g` (global) se instala en tu directorio de usuario, por lo que la habilidad está disponible en todos los proyectos. Sin `-g`, `npx skills` instala el proyecto localmente en `./.skills/` (comprometido con el repositorio). Para una herramienta de investigación del mundo, lo que quieres es global.

Codex escritorio y otros hosts en modo carpeta pueden funcionar tanto en carpetas ordinarias como en repositorios Git. Antes de investigar primero, pide al agente host que ejecute el `scripts/last30days.py --preflight` incluido desde el directorio skill cargado; en una comprobación de código, el comando equivalente es `python3 skills/last30days/scripts/last30days.py --preflight`. Muestra el código fuente de configuración, el plan de cookies del navegador, las escrituras planificadas, los comandos opcionales y la configuración del proyecto ignorada sin leer cookies, escribir archivos ni ejecutar investigación.

Por defecto, esto se instala para el arnés que `npx skills` detecte. Para apuntar a uno específico (o varios):

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Actualización más adelante con:

```bash
npx skills update last30days -g
```

O actualiza todo lo que hayas instalado globalmente a través de `npx skills`:

```bash
npx skills update -g
```

Lista y elimina con `npx skills list -g` y `npx skills remove last30days -g`.

### claude.ai (web)

1. [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) de la última edición
2. Ve a [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Haz clic en el botón `+` en el panel de Habilidades > haz clic en `Create skill` > `Upload a skill` y navega/suelta el archivo

Activa primero "Ejecución de código y creación de archivos" en Capacidades — las habilidades no funcionarán sin ella.

### Claude Desktop

Claude Desktop instala `/last30days` como servidor MCP mediante un paquete `.mcpb` (un paquete Model Context Protocol de un solo clic).

1. Ve a la [latest release](https://github.com/mvanhorn/last30days-skill/releases/latest) y descarga el `.mcpb` para tu plataforma:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Abre Claude Desktop, ve a Configuración > Extensiones y arrastra el archivo.
3. Cuando te lo pidan, pega API claves para las fuentes que quieres activar. Cada campo es opcional: el motor se degrada a modo solo web si los saltas todos. Las claves se almacenan en el llavero de tu sistema operativo.
4. Reinicia Claude Desktop. Pídele a Claude que "investigue a Peter Steinberger" o cualquier tema y llamará a la herramienta `research` .

**Requisito de host:** Python 3.12+ en PATH. El paquete incluye el código fuente del motor pero utiliza tu intérprete de Python local. Instala desde [python.org](https://www.python.org/downloads/) en Windows; macOS y la mayoría de Linux distribuciones envían una versión compatible.

**Las teclas no se sincronizan con la habilidad Código.** Claude Desktop y Claude Code mantienen almacenes de credenciales separados por diseño. Si ya configuraste `~/.config/last30days/.env` para la habilidad Código, volverás a introducir las mismas teclas aquí una vez.

Windows soporte se pospone hasta que se resuelvan los puntos de entrada del manifiesto por plataforma; se sigue en un número de seguimiento.

### OpenClaw

```bash
clawhub install last30days-official
```

Para X/Twitter acciones de trabajo fuera de la investigación `/last30days` , como publicar
tuits o respuestas, exportación de seguidores, gestión de medios, monitores y sorteo
Draws, usa a [TweetClaw](https://github.com/Xquik-dev/tweetclaw) como compañero
OpenClaw plugin. TweetClaw es mantenido por Xquik-dev y aparece solo como un
Camino de compañero opcional, no una dependencia o endoso de Last30Days.

### Manual (desarrollador)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

El enlace simbólico mantiene la instalación sincronizada con tu árbol de trabajo mientras editas — no es necesario recopiar. Para `claude.ai`, compila el archivo `.skill` desde el código fuente: `bash skills/last30days/scripts/build-skill.sh` produce `dist/last30days.skill`.

Reddit (con comentarios), Hacker News, Polymarkety GitHub funcionan inmediatamente. Cero configuración. Ejecuta `/last30days` una vez y el asistente de configuración desbloquea más fuentes en 30 segundos, incluyendo la arXiv gratuita y la Techmeme CLIs.

## Trae tus propias llaves

Estas plataformas no tienen relaciones entre sí. X no sabe lo que Reddit piensa. YouTube no ve TikTok. Pero puedes traer tus propias claves de API y tokens de navegador, y de repente tienes acceso a todos a la vez.

| Fuentes | Lo que necesitas | Coste |
|---------|---------------|------|
| Reddit (con comentarios) + HN + Polymarket + GitHub + StockTwits | Nada | Gratis |
| arXiv + Techmeme | Free CLIs, instalado automáticamente por la primera ejecución | Gratis |
| X / Twitter | Inicia sesión en x.com en cualquier navegador, o configura `XQUIK_API_KEY` / `XAI_API_KEY` | Las cookies del navegador son gratuitas; las claves son específicas de cada proveedor |
| YouTube | `brew install yt-dlp` | Gratis |
| Bluesky | Contraseña de la app de bsky.app | Gratis |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube comentarios | ScrapeCreators clave | 10.000 llamadas gratis, luego PAYG |
| Xiaohongshu (RED) | Ejecuta un plugin de navegador x-mcp o servicio `xiaohongshu-mcp` iniciado sesión y opta por `--search xhs` por partida o `INCLUDE_SOURCES=xiaohongshu` en `.env`; last30days auto-probes `http://localhost:18060` luego `http://host.docker.internal:18060`, o usa `XIAOHONGSHU_API_BASE` para una URL personalizada | No hay clave de API de últimos 30 días; depende del servicio local de sesiones de navegador |
| DripStack (boletines financieros premium) | Opt-in: `--search dripstack` por partida, o `INCLUDE_SOURCES=dripstack` en `.env` | Sin clave; búsqueda pública gratuita API |
| Perplexity Sonar / API de búsqueda / Investigación profunda | Perplexity tecla, o clave OpenRouter como respaldo para Sonar | Paga según la acción |
| Web búsqueda | Clave de búsqueda Brave | 2.000 consultas gratuitas al mes |

### macOS Keychain (opcional)

En macOS puedes almacenar claves en el Keychain del sistema en lugar de un archivo `.env` . La habilidad las detecta automáticamente como la fuente de menor prioridad — `.env` archivos y entorno de procesos siguen ganando en la colisión.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Los elementos se almacenan bajo `last30days-<KEY>` de nombre de servicio para el usuario actual. En plataformas no Darwin, el cargador es no-op, por lo que no hay cambio de comportamiento para usuarios de Linux/Windows .

¿Ya tienes claves bajo diferentes nombres de servicio Keychain ? Configura el mapeo de `LAST30DAYS_KEYCHAIN_ALIASES` no secreto descrito en [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) en lugar de copiar secretos.

Consulta [CONFIGURATION.md](CONFIGURATION.md) para la matriz completa de claves por fuente, prioridad del proveedor de razonamiento y prioridad del backend de búsqueda web.

## Configuración

Dos cosas que probablemente querrás saber el primer día:

**Donde se guardan los archivos de investigación.** `LAST30DAYS_MEMORY_DIR` por defecto es `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Anula configurando esa variable ambiental a cualquier ruta de tu shell, o `--save-dir <path>` por partida. Usa `--output <file>` cuando necesites el resultado renderizado en una ruta exacta, usando el formato seleccionado por `--emit`. Usa `--save-suffix=<name>` para mantener separadas varias variaciones del mismo tema (por ejemplo, por cliente). Cada `--save-dir` ejecución produce `<slug>-raw[-suffix].md`. Ejecuta `python3 skills/last30days/scripts/last30days.py --preflight` para revisar las escrituras planificadas antes de una ejecución de investigación.

**Salida estructurada para agentes y flujos de trabajo.** Pide `/last30days` JSON legibles por máquina para recibir el perfil estable y versionado del agente. Para uso directo del motor en scripts o desarrollo, ejecuta `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; añade `--json-profile=raw` solo cuando necesites el volcado interno de `Report` sin versiones. Consulta el [JSON export field reference and versioning policy](docs/reference/json-export.md).

**Descubrimiento sin tema.** Pide a `/last30days what's trending in AI agents?` que consigas un resumen de descubrimiento clasificado en lugar de investigar un tema que ya conoces; en un agente anfitrión esto ejecuta el protocolo de tres comandos host-judged (el modelo nombra temas, filtra basura, puntua la dignidad y escribe los ángulos de contenido). Para uso directo del motor en scripts o cron, ejecuta `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (one-shot: nombres deterministas de temas, sin ángulos); añade `--emit=json` para el contrato de descubrimiento versionado. El descubrimiento es mutuamente excluyente con un tema posicional y `--drill`.

**Monitorización de tendencias entre ejecuciones.** El modo predeterminado produce una instantánea de descuento nueva por ejecución. Para acumular hallazgos a lo largo del tiempo, añade `--store` para que persistan en una base de datos SQLite, luego usa [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) para ejecuciones programadas (con entrega opcional de Slack / webhook en nuevos hallazgos) y [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) para resúmenes diarios o semanales. El patrón completo de cadencia está en [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Una biblioteca de investigación suscribible.** Pídete `/last30days` que construya tu feed de biblioteca, o úsala `python3 skills/last30days/scripts/last30days.py library feed` directamente para scripting y desarrollo. Convierte los briefs guardados en `index.html`, un `feed.xml`local de Atom y páginas breves legibles. Añade `--publish` solo cuando quieras que el índice de HTML y las páginas breves estén alojadas; la publicación es explícita opt-in y pública por defecto. Para que el feed de Atom sea suscribible, aloja el directorio de salida generado en un host estático como GitHub Pages.

**Busca todo lo que has investigado.** Pregunta `/last30days search my library for MCP servers` o `/last30days have I researched MCP servers before?`. Para uso directo del motor, ejecuta `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. La búsqueda es offline y determinista: indexa de forma incremental los mismos briefs guardados que usa el feed de la biblioteca, fusiona los avistamientos de la tienda correspondientes por ejecución y agrupa los resultados por tema y fecha. Las ejecuciones nuevas también muestran una sección compacta **Desde tu biblioteca** cuando investigaciones previas se solapan con el tema actual; configura `LAST30DAYS_LIBRARY_CONTEXT=off` para desactivar ese contexto pasivo.

Los scripts wrapper por cliente, subreddits personalizados de categoría y el canal beta experimental para personalizaciones en proceso también están documentados en [CONFIGURATION.md](CONFIGURATION.md).

## Showcase: feeds de investigación comunitaria

¿Has publicado una actualización recurrente de IA, un seguimiento de mercado o una obsesión maravillosamente limitada con los últimos 30 días? Comparte la URL de la biblioteca pública—o la URL de Atom después de alojarla `feed.xml` en un host estático—en [the community showcase thread](https://github.com/mvanhorn/last30days-skill/issues/532). Los feeds comunitarios estarán enlazados aquí a medida que sus propietarios los envíen; el hilo es el punto de recogida mientras tanto.

## Cómo funciona

1. **Escribes un tema.** Persona, empresa, producto, tecnología, "X vs Y." Cualquier cosa.
2. **El agente decide quién importa.** Encuentra X cuentas (incluidos fundadores), repositorios de GitHub , subreddits TikTok hashtags YouTube canales. Para "Kanye West" conoce r/hiphopheads, @kanyewesty "bully review" en YouTube. Para "OpenClaw" resuelve openclaw/openclaw en GitHub y recoge recuentos de estrellas en directo.
3. **Todas las fuentes buscadas en paralelo.** Expansión multiconsulta. Resultados puntuados por interacción, relevancia y frescura.
4. **La profundidad que nadie más tiene.** Transcripciones completas YouTube de vídeos de reacción. Comentarios de Reddit con el recuento de votos positivos. TikTok pies de foto. Polymarket probabilidades. No solo títulos y enlaces.
5. **Misma historia, fusionada.** El Festival Wireless anunciado el Reddit, se discutió en X, los precios de las entradas en TikTok = un grupo, no tres artículos separados.
6. **Sintetizado en un solo informe.** Basado en datos específicos. Citado por fuente. Clasificado según lo que la gente realmente interactúa. No "esto es lo que encontré". Es "esto es lo que importa."
7. **Entonces se convierte en tu experto.** Tras una sola partida, tu sesión Claude sabe todo lo que sabe la comunidad. Haz preguntas de seguimiento. Haz que escriba prompts, redacte correos electrónicos, planifique viajes, diseñe sistemas, todo basado en lo que es real ahora mismo.

## Lo que dice la gente

> "He encontrado una habilidad Claude Code que investiga cualquier tema en Reddit, X, YouTubey HN de los últimos 30 días. Luego escribe los prompts para ti. He estado buscando manualmente Reddit y X investigación antes de cada contenido que escribo. Pestaña por pestaña. Hilo por hilo. Esa es la parte que tarda 90 minutos. Esto lo elimina." -@itsjasonai

> "Esta habilidad reemplazó todo mi flujo de trabajo de investigación. Le das un tema, raspa Reddit, Xy la web para encontrar de qué la gente realmente habla. No de viejas entradas de blog. Conversaciones reales de los últimos 30 días." -@itswilsoncharles

> "5 de los 10 repositorios más populares en GitHub hoy son herramientas Claude . #1: Mvanhorn/last30days-habilidad" -@yieldhunter95

## Código abierto

Licencia del MIT. Sin rastreo. Sin análisis. Tu investigación permanece en tu máquina. 2.700+ pruebas.

Construido con Python 3.12+, yt-dlp, Node.js (cliente Bird para X búsqueda) y ScrapeCreators API. arquitectura del motor v3 por [@j-sperling](https://github.com/j-sperling).

Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para abrir una PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) para la lista completa de colaboradores de la comunidad y [CHANGELOG.md](CHANGELOG.md) para el historial de versiones.

## Historia de Star

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
