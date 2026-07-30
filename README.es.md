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

**Un motor de búsqueda con IA dirigido por agentes puntuado por votos positivos, me gusta y dinero real, no por editores.**

Este archivo README rastrea la canalización v3 actual. La especificación de habilidad en tiempo de ejecución se encuentra en [skills/last30days/SKILL.md](skills/last30days/SKILL.md), que es la fuente de verdad para el comportamiento de configuración y comando más reciente.

**Claude Code (recomendado: actualizaciones automáticas a través del mercado):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI o cualquiera de los más de 50 [hosts de Agent Skills](https://agentskills.io):**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` se instala globalmente para su usuario, disponible en todos los proyectos. Colóquelo en el alcance por proyecto).

Más opciones de instalación (claude.ai web, OpenClaw, manual) en la sección [Install](#instalación) a continuación.

Configuración cero. Reddit, HN, Polymarket y GitHub funcionan de inmediato. Ejecútelo una vez y el asistente de configuración desbloqueará X, YouTube, TikTok, arXiv, Techmeme y más en 30 segundos.

---

Votos positivos de Reddit. A X le gusta. Transcripciones de YouTube. Compromiso de TikTok. Cuotas de polimercado respaldadas por dinero real e información privilegiada. Son millones de personas votando con su atención y sus billeteras todos los días. /last30days lo busca todo en paralelo, lo califica según lo que realmente interactúan las personas reales y un juez agente de IA lo sintetiza en un resumen.

Editores agregados de Google. /last30days busca personas.

No puede realizar esta búsqueda en ningún otro lugar porque ninguna IA tiene acceso a toda ella. La búsqueda de Google no toca los comentarios de Reddit ni las publicaciones X. ChatGPT tiene un acuerdo con Reddit pero no puede buscar X ni TikTok. Gemini tiene YouTube pero no Reddit. Claude no tiene ninguno de ellos de forma nativa. Cada plataforma es un jardín vallado con su propia API, sus propios tokens, su propia autenticación. Pero puedes traer tus propias claves y sesiones de navegador y, de repente, un agente de IA puede buscarlas todas a la vez, compararlas entre sí y decirte lo que realmente importa.

Ese es el desbloqueo. No hay un motor de búsqueda mejor. Una docena de plataformas desconectadas, unidas por un agente.

```
/last30days Peter Steinberger
```

Tienes una reunión mañana. Los buscas en Google. Obtendrá su LinkedIn a partir de 2023. /last30days le brinda lo que realmente están haciendo este mes: se unió a OpenAI para trabajar en Codex, luchó contra la prohibición de Anthropic sobre agentes externos, envió 23 PR con una tasa de fusión del 85%, creó "LobsterOS" para el control de agentes entre dispositivos y r/ClaudeCode alcanzó 569 votos a favor debatiendo si es un héroe o "insoportable". Distribuidos en X publicaciones, hilos de Reddit, transcripciones de YouTube y confirmaciones de GitHub. Nada de eso estaba en Google.

## Por qué existe esto

Lo construí para mantenerme al día con la IA. Todo cambia todos los días y los nerds de Reddit y X siempre están al tanto de ello. Necesitaba mejores indicaciones y los datos de capacitación siempre estaban meses por detrás de lo que la comunidad ya había descubierto.

Pero se convirtió en algo más grande. Ahora lo ejecuto antes de una llamada de ventas para conocer la verdad de los últimos 30 días sobre una empresa. Antes de una reunión para leer los tweets recientes y las transcripciones de podcasts de alguien. Antes de un viaje a Disney World, para saber qué atracciones están cerradas y qué dice la comunidad sobre Genie+. Antes de construir algo, debo saber qué problemas enfrenta realmente la gente.

Si se reúne con un director ejecutivo, ¿ha leído todos sus tweets y transcripciones de YouTube de los últimos 30 días? Tengo.

## Fuentes, puntuadas por la gente

| Fuente | Lo que te dice la gente |
|--------|--------------------------|
| **Reddit** | La toma sin filtrar. Comentarios principales con recuentos reales de votos a favor, gratis, sin clave API. Las opiniones reales que Google entierra. |
| **X/Twitter** | La toma caliente, el hilo experto, la reacción de ruptura. Primero en saber, primero en discutir. |
| **YouTube** | La inmersión profunda de 45 minutos. Se buscaron en las transcripciones completas las cinco frases citables que importan. |
| **TikTok** | El creador llega a 3,6 millones de personas con una versión que nunca encontrarás en Google. |
| **Instagram Reels** | La perspectiva del influencer con transcripciones de palabras habladas. La señal de la cultura visual. |
| **Hacker News** | El consenso de los desarrolladores. 825 puntos, 899 comentarios. Donde los técnicos realmente discuten. |
| **Polymarket** | No opiniones. Impares. Respaldado por dinero real. 96% de confianza en las ventas de álbumes. 4% en una adquisición. |
| **GitHub** | Para personas: velocidad de relaciones públicas, principales repositorios por estrellas, notas de la versión. Para temas: cuestiones y debates. |
| **Digg** | Grupos de historias seleccionados de la tabla de clasificación AI 1000 de Digg (~1000 cuentas de AI de alta señal en X), con citas en línea atribuibles (no se requiere autenticación X). Habilitado automáticamente cuando `digg-pp-cli` está en PATH. |
| **arXiv** | Los periódicos detrás del revuelo. Nueva investigación en la ventana, gratuita, sin clave API. Se habilita automáticamente cuando `arxiv-pp-cli` está en PATH (la configuración de primera ejecución lo instala). |
| **Tecmeme** | La capa editorial de noticias tecnológicas, con ventana de fecha a sus 30 días. Gratis, sin clave API. Se habilita automáticamente cuando `techmeme-pp-cli` está en PATH (la configuración de primera ejecución lo instala). |
| **LinkedIn** | La señal profesional. Publicaciones y artículos, con artículos ponderados como señal alta. |
| **StockTwits** | Sentimiento del comerciante. Se activa automáticamente cuando su tema es un ticker o una criptomoneda. |
| **Hilos** | La capa de texto posterior a Twitter. Conversaciones de creadores y marcas. |
| **Pinterest** | Descubrimiento visual. Fija, guarda y comenta productos e ideas. |
| **Xiaohongshu (ROJO)** | Señales de creadores, productos y estilos de vida chinos. Se solicita explícitamente con `--search xhs` cuando se ejecuta localmente un complemento de navegador x-mcp conectado o un servicio `xiaohongshu-mcp`. |
| **Cielo azul** | La capa social descentralizada. Publicaciones de AT Protocol de la migración posterior a Twitter. |
| **Perplejidad** | Síntesis de Grounded Sonar, filas de API de búsqueda sin procesar e investigación profunda. |
| **Web** | La cobertura editorial, las comparaciones de blogs. Una señal de muchas, no la única. |

Los contribuyentes de la comunidad siguen agregando más. Truth Social y otras fuentes de nicho están en el motor y hay más en camino.

Un hilo de Reddit con 1500 votos a favor es una señal más fuerte que una publicación de blog que nadie leyó. Un TikTok con 3,6 millones de visitas te dice más sobre lo que es culturalmente relevante que un comunicado de prensa. Las probabilidades de los polimercados respaldadas por un volumen de 66.000 dólares son más difíciles de discutir que las suposiciones de un experto.

La síntesis se clasifica según lo que realmente hizo la gente real. Relevancia social, no relevancia SEO.

## Para qué lo usa realmente la gente

**Antes de una reunión.** `/last30days Peter Steinberger`: se unió al equipo Codex de OpenAI, luchando contra la prohibición de Anthropic sobre agentes externos, 23 RP se fusionaron a una tasa de fusión del 85 % en GitHub, creando LobsterOS para el control de agentes entre dispositivos. r/ClaudeCode: "Desde que se lanzó OpenClaw, era ampliamente conocido que si lo ejecutabas a través de cualquier otra cosa que no fuera la API, eventualmente serías baneado" (227 votos a favor). Eso no está en LinkedIn.

**Para leer señales de contratación.** `/last30days Listen Labs --hiring-signals`: las páginas de empleos y carreras actuales se convierten en evidencia citada de cambios de enfoque: contratación en seguridad empresarial, éxito del cliente, infraestructura o expansión de productos. El informe dice lo que parece indicar la contratación, no lo que ofrecerá la hoja de ruta.

**Para encontrar el tema antes de que alcance su punto máximo.** Pregunte a `/last30days what's exploding in AI agents?` y la habilidad cambiará al modo de descubrimiento: el motor barre los listados de categorías de Reddit, las mejores historias de Hacker News, el feed AI 1000 de Digg y X cuando se autentica; su agente juzga las nominaciones (nombres, filtrado de basura, valor del contenido) y escribe ángulos de podcasts/artículos X; luego obtienes entre 5 y 10 temas clasificados por velocidad. Cada resultado incluye números de fuentes cruzadas, una etiqueta de impulso y un seguimiento `/last30days "<topic>"` listo para ejecutar.

**Cuando algo cae.** `/last30days Kanye West` - Reino Unido bloqueó su visa, Wireless Festival canceló, los patrocinadores huyeron. Pero BULLY debutó en el puesto número 2 en Billboard. Fantano regresó de su "año sabático" para revisarlo (653.000 visitas). SoFi Homecoming presentó a Lauryn Hill y Travis Scott para 44 canciones. Polymarket: "¿Kanye volverá a twittear?" 86% Sí. 23 hilos de Reddit, 17 vídeos de YouTube, 86.000 votos a favor.

**Para comparar herramientas.** `/last30days OpenClaw vs Hermes vs Paperclip`: "Estos no son competidores, son capas". OpenClaw es el ejecutor (351.000 estrellas de GitHub, en vivo), Hermes es el cerebro que se mejora a sí mismo (31.000 estrellas), Paperclip es el organigrama (49.000 estrellas). El conteo de estrellas se obtuvo en vivo de la API de GitHub, no de publicaciones de blog obsoletas. Mesa de lado a lado con arquitectura, memoria, seguridad, lo mejor para. Según @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard".

**Para entender el mundo.** `/last30days Iran vs USA` - Día 38 de la guerra. La fecha límite del martes de Trump para que Irán reabra el Estrecho de Ormuz. Dos aviones de combate estadounidenses derribados. Petróleo a 126 dólares el barril. La AIE lo calificó como "la mayor interrupción del suministro en la historia del mercado mundial del petróleo". Polymarket: alto el fuego antes del 31 de diciembre al 74%. 27 publicaciones X, 10 videos de YouTube, 20 mercados de predicción.

**Antes de un viaje.** `/last30days Universal Epic Universe` - Ampliación ya en construcción. Permiso "Proyecto 680" presentado. Espectáculo de fuegos artificiales confirmado por infraestructura pero sin previo aviso. Tiempos de espera: Mine-Cart Madness con un promedio de 148 minutos. Aún no hay un pase anual y los lugareños están frustrados. Stardust Racers estará en remodelación hasta el 5 de abril.

**Para aprender algo rápido.** `/last30days Nano Banana Pro prompting`: las indicaciones estructuradas en JSON están reemplazando la sopa de etiquetas. El formato anidado de @pictsbyai evita la "sangrado de conceptos". El flujo de trabajo que da prioridad a la edición supera a la regeneración. Luego le escribe un mensaje de producción utilizando exactamente lo que la comunidad dijo que funciona.

## ¿Qué hay de nuevo?

Desde el anuncio de la v3.3 en mayo, a partir de la v3.11.1 (julio de 2026): 175 RP fusionados (122 de ellos de 52 contribuyentes de la comunidad) en 15 versiones. Esto es lo que aterrizó.

### Primera clase en OpenAI Codex

/last30days es ahora un complemento nativo de Codex con configuración guiada: no un puerto, un ciudadano de primera clase. Las citas con reconocimiento de renderizador significan que la salida del Codex se lee como un resumen en lugar de una sopa de URL (#694), y el mismo motor se ejecuta en Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClaw y más de 50 hosts de Agent Skills. Manifiesto del complemento Codex por [@rfoust](https://github.com/rfoust) (#686), corrección de autenticación del Codex por [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmeme y Digg: gratis, sin claves API

arXiv trae los artículos detrás de la publicidad y Techmeme trae la capa editorial de noticias tecnológicas: gratuita, sin claves y la configuración de primera ejecución instala sus CLI para que se activen automáticamente (#709). Los grupos de historias AI 1000 de Digg llegan sin autenticación X de la misma manera: el programa de instalación instala la CLI gratuita de Digg (#590). Trustpilot ofrece la opción de participar en la investigación de marcas de consumo.

### Free Reddit aumentó las puntuaciones reales y los comentarios principales

La API pública .json de Reddit murió; el camino libre volvió con más fuerza. RSS sin llave + shreddit scraping (#457), descubrimiento de subreddit dedicado con conteos reales de votos positivos a través de arctic-shift (#696) y un piso de relevancia para que una publicación viral fuera de tema no pueda secuestrar su resumen (#488, gracias [@rzachsmith](https://github.com/rzachsmith)). Sin clave API. Puntuaciones reales. Comentarios principales incluidos.

### Los mejores comentarios en cada brief

Los comentarios ahora son una capa predeterminada en todas las fuentes: comentarios de Instagram con diversidad basada en clasificación, por lo que cinco tomas interesantes no provienen todas de una publicación (n.° 751), comentarios de YouTube más una copia de seguridad de la transcripción de ScrapeCreators para cuando yt-dlp se tache (n.° 637) y comentarios votados por la multitud ponderados en las mejores tomas para que las líneas más divertidas de la comunidad sobrevivan la puntuación (n.° 592, n.° 608).

### Un comando médico

Solicite una verificación de estado y el médico ejecutará todas las fuentes y luego prescribe las soluciones exactas: qué clave falta, qué CLI está fuera de RUTA, qué cookie expiró (#753). Ya no tendrás que adivinar por qué X volvió a adelgazar.

### Búsqueda X, reconstruida

El canal X recibió una revisión integral: carriles FROM y ACERCA de para que las publicaciones de una persona y la conversación sobre ellas se clasifiquen (n.° 610), desambiguación de subconsultas con reconocimiento de persona (n.° 611), base de autoría propia con clasificación de señales de interacción (n.° 613) y una única fuente X con conmutación automática por error de backend (n.° 622). Además de un `--diagnose` honesto que realmente prueba la autenticación (#609).

### Más fuentes se unieron

LinkedIn vía ScrapeCreators, con artículos de alta señal ([@ravstr](https://github.com/ravstr), #702). StockTwits se activa automáticamente para temas de ticker y cripto ([@wtiwana](https://github.com/wtiwana), #658). La perplejidad aumentó en los modos API directos y en la investigación profunda asíncrona ([@sk-holmes](https://github.com/sk-holmes), #629).

### Endurecido por la comunidad

La ola de seguridad fue casi en su totalidad trabajo comunitario: correcciones de XSS almacenado en el renderizador HTML ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), archivos temporales de cookies bloqueados, CI reforzado en la cadena de suministro con OpenSSF Scorecard y atestación de procedencia de compilación ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), escaneos de Semgrep y OSV-Scanner más una puerta de revisión de dependencia de relaciones públicas ([@23241a6749](https://github.com/23241a6749)), un piso de cobertura de prueba introducido al 60% y desde entonces elevado al 84% ([@gourab5139014](https://github.com/gourab5139014)) y un análisis de seguridad de Hermes eliminó todos los hallazgos CRÍTICOS (#768).

### Llega más lejos

Lenguas hebreas y no latinas ([@dudyme](https://github.com/dudyme)). Tokenización compatible con CJK para fuentes chinas ([@An-idd](https://github.com/An-idd)). Una ola de compatibilidad con Windows. Extracción de cookies en toda la familia Chromium: Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)), además de fuentes de credenciales macOS Keychain y Linux pass(1). Revisión histórica de `--as-of` ([@chiyi-creator](https://github.com/chiyi-creator)). Python 3.12 con aprovisionamiento automático a través de uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` para leer las páginas de empleo de una empresa. Deltas de la lista de seguimiento entre ejecuciones.

### Todavía en la caja de v3

Los fundamentos de la v3 todavía están aquí: el cerebro previo a la investigación que resuelve los identificadores, subreddits y hashtags correctos antes de que se active una única llamada API (creado por [@j-sperling](https://github.com/j-sperling)); Best Takes puntúa por humor y viralidad junto con relevancia; fusión de clústeres de fuentes cruzadas; comparaciones de un solo paso ("CLI vs MCP" en 3 minutos, no 12); comparaciones de `--competitors` descubiertas automáticamente; Modo persona de GitHub (`--github-user=steipete`); modo ELI5 ("eli5 activado" después de cualquier ejecución); y resúmenes HTML independientes y compartibles (`--emit=html`). Las perillas de configuración se encuentran en [CONFIGURATION.md](CONFIGURATION.md).

## Instalar

| Superficie | Instalar | Actualizaciones |
|---------|---------|---------|
| **Claude Code** (recomendado) | `/plugin marketplace add mvanhorn/last30days-skill` | Auto a través del mercado, o `claude plugin update last30days@last30days-skill` |
| **Grok** (CLI de compilación xAI) | `grok plugin marketplace add mvanhorn/last30days-skill` luego `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex, Cursor, Copilot, Gemini CLI o cualquiera de los más de 50 [hosts de Agent Skills](https://agentskills.io)** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [Descargue `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) y cárguelo a través de claude.ai > Personalizar > Habilidades > + > Crear habilidad > Cargar una habilidad | Volver a descargar y volver a subir |
| **Claude Desktop** | [Descargue el `.mcpb` para su plataforma ](https://github.com/mvanhorn/last30days-skill/releases/latest) y arrástrelo a Configuración > Extensiones | Vuelva a descargar y arrastre el nuevo paquete a |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recomendado)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Recomendado porque el mercado de Claude Code maneja las actualizaciones por usted: la caché del complemento tiene versiones y se actualiza automáticamente cuando se publica una nueva versión. Ejecute `claude plugin update last30days@last30days-skill` para forzar una verificación.

Si prefiere utilizar la ruta de instalación de habilidades del agente en Claude Code, también es compatible:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

El complemento nativo y la instalación `npx skills` pueden coexistir. Tenga en cuenta que Claude Code no realiza deduplicación entre métodos de instalación: si tiene activos tanto el complemento del mercado como la copia `npx skills`, `/last30days` mostrará dos entradas. Utilice un método de instalación por máquina.

### Grok (CLI de compilación xAI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) se instala los últimos 30 días como complemento nativo. La instalación directa rastrea el repositorio:

```bash
grok plugin install mvanhorn/last30days-skill
```

O agregue este repositorio como fuente del mercado, luego instálelo por el nombre del complemento:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Agregue `--trust` para omitir la confirmación de instalación. Actualización con `grok plugin update last30days`. Grok también lee los manifiestos del Claude Code para comprobar la compatibilidad; el par nativo `.grok-plugin/` es el carril de primera clase (y a qué apunta un listado oficial [xAI Marketplace](https://github.com/xai-org/plugin-marketplace)). `npx skills add` sigue siendo una alternativa válida entre hosts.

### Codex, Cursor, Copilot, Gemini CLI y otros hosts de Agent Skills

Instale a través de la CLI abierta [Agent Skills](https://agentskills.io): admite más de 50 arneses, incluidos `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose` y más (lista completa en el repositorio [vercel-labs/skills](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

El indicador `-g` (global) se instala en su directorio de usuarios para que la habilidad esté disponible en todos los proyectos. Sin `-g`, `npx skills` instala el proyecto localmente en `./.skills/` (comprometido con el repositorio). Para una herramienta de investigación del mundo, lo que desea es global.

El escritorio Codex y otros hosts en modo carpeta pueden funcionar en carpetas normales, así como en repositorios Git. Antes de la primera investigación, solicite al agente anfitrión que ejecute el `scripts/last30days.py --preflight` incluido desde el directorio de habilidades cargado; en un pago de origen, el comando equivalente es `python3 skills/last30days/scripts/last30days.py --preflight`. Muestra el origen de la configuración, el plan de cookies del navegador, las escrituras planificadas, los comandos opcionales y la configuración del proyecto ignorada sin leer cookies, escribir archivos ni realizar investigaciones.

De forma predeterminada, esto se instala para cualquier arnés que detecte `npx skills`. Para apuntar a uno específico (o varios):

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Actualiza más tarde con:

```bash
npx skills update last30days -g
```

O actualice todo lo que ha instalado globalmente a través de `npx skills`:

```bash
npx skills update -g
```

Listar y eliminar con `npx skills list -g` y `npx skills remove last30days -g`.

### claude.ai (web)

1. [Descargue `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) desde la última versión
2. Vaya a [claude.ai > Personalizar > Skills](https://claude.ai/customize/skills)
3. Haga clic en el botón `+` en el panel Habilidades > haga clic en `Create skill` > `Upload a skill` y busque/solte el archivo en

Habilite primero "Ejecución de código y creación de archivos" en Capacidades; las habilidades no se ejecutarán sin él.

### Claude Desktop

Claude Desktop instala `/last30days` como servidor MCP a través de un paquete `.mcpb` (un paquete de protocolo de contexto modelo con un solo clic).

1. Vaya a la [última versión](https://github.com/mvanhorn/last30days-skill/releases/latest) y descargue `.mcpb` para su plataforma:
- macOS Apple Silicio: `last30days-pp-mcp-darwin-arm64.mcpb`
-Mac OS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
-Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Abra Claude Desktop, vaya a Configuración > Extensiones y arrastre el archivo.
3. Cuando se le solicite, pegue las claves API para las fuentes que desea habilitar. Cada campo es opcional: el motor pasa al modo solo web si los omite todos. Las claves se almacenan en el llavero de su sistema operativo.
4. Reinicie Claude Desktop. Pídale a Claude que "investigue a Peter Steinberger" o cualquier tema y llamará a la herramienta `research`.

**Requisito de host:** Python 3.12+ en PATH. El paquete incluye el código fuente del motor pero utiliza su intérprete de Python local. Instalar desde [python.org](https://www.python.org/downloads/) en Windows; macOS y la mayoría de las distribuciones de Linux incluyen una versión compatible.

**Las claves no se sincronizan con la habilidad Código.** Claude Desktop y Claude Code mantienen almacenes de credenciales separados por diseño. Si ya configuró `~/.config/last30days/.env` para la habilidad Código, volverá a ingresar las mismas claves aquí una vez.

La compatibilidad con Windows se aplaza hasta que se resuelvan los puntos de entrada del manifiesto por plataforma; seguimiento en un número de seguimiento.

### Garra Abierta

```bash
clawhub install last30days-official
```

Para flujos de trabajo de acciones de X/Twitter fuera de la investigación de `/last30days`, como publicaciones
tweets o respuestas, exportación de seguidores, manejo de medios, monitores y obsequios
dibuja, usa [TweetClaw](https://github.com/Xquik-dev/tweetclaw) como compañero
Complemento OpenClaw. TweetClaw es mantenido por Xquik-dev y aparece solo como un
ruta complementaria opcional, no una dependencia o respaldo de los últimos 30 días.

### Manual (desarrollador)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

El enlace simbólico mantiene la instalación sincronizada con su árbol de trabajo mientras edita, sin necesidad de volver a copiarlo. Para `claude.ai`, cree el archivo `.skill` desde el origen: `bash skills/last30days/scripts/build-skill.sh` produce `dist/last30days.skill`.

Reddit (con comentarios), Hacker News, Polymarket y GitHub funcionan de inmediato. Configuración cero. Ejecute `/last30days` una vez y el asistente de configuración desbloqueará más fuentes en 30 segundos, incluidas las CLI gratuitas de arXiv y Techmeme.

## Trae tus propias llaves

Estas plataformas no tienen relaciones entre sí. X no sabe lo que piensa Reddit. YouTube no ve TikTok. Pero puedes traer tus propias claves API y tokens de navegador y, de repente, tendrás acceso a todos ellos a la vez.

| Fuentes | Lo que necesitas | Costo |
|---------|---------------|------|
| Reddit (con comentarios) + HN + Polymarket + GitHub + StockTwits | Nada | Gratis |
| arXiv + Techmeme | CLI gratuitos, autoinstalados mediante la configuración de primera ejecución | Gratis |
| X/Twitter | Inicie sesión en x.com en cualquier navegador o configure `XQUIK_API_KEY` / `XAI_API_KEY` | Las cookies del navegador son gratuitas; las claves son específicas del proveedor |
| Youtube | `brew install yt-dlp` | Gratis |
| Cielo azul | Contraseña de la aplicación de bsky.app | Gratis |
| TikTok + Instagram + Hilos + Pinterest + LinkedIn + Comentarios de YouTube | Clave ScrapeCreators | 10.000 llamadas gratis, luego PAYG |
| Xiaohongshu (ROJO) | Ejecute un complemento de navegador x-mcp conectado o un servicio `xiaohongshu-mcp` y opte por `--search xhs` por ejecución o `INCLUDE_SOURCES=xiaohongshu` en `.env`; Los últimos 30 días sondean automáticamente `http://localhost:18060` y luego `http://host.docker.internal:18060`, o use `XIAOHONGSHU_API_BASE` para una URL personalizada | Sin clave API de los últimos 30 días; depende del servicio de sesión de su navegador local |
| DripStack (boletines financieros premium) | Optar por: `--search dripstack` por ejecución, o `INCLUDE_SOURCES=dripstack` en `.env` | Sin llave; API de búsqueda pública gratuita |
| Sonar de perplejidad / API de búsqueda / Investigación profunda | Clave de perplejidad o clave de OpenRouter como respaldo de Sonar | Paga sobre la marcha |
| Búsqueda web | Tecla de búsqueda valiente | 2.000 consultas gratuitas/mes |

### Llavero macOS (opcional)

En macOS, puede almacenar claves en el llavero del sistema en lugar de en un archivo `.env`. La habilidad los selecciona automáticamente como la fuente de menor prioridad: los archivos `.env` y el entorno de proceso aún ganan en caso de colisión.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Los elementos se almacenan con el nombre de servicio `last30days-<KEY>` para el usuario actual. En plataformas que no son Darwin, el cargador no funciona, por lo que no hay ningún cambio de comportamiento para los usuarios de Linux/Windows.

¿Ya tienes claves con diferentes nombres de servicios de llavero? Configure la asignación `LAST30DAYS_KEYCHAIN_ALIASES` no secreta descrita en [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) en lugar de copiar secretos.

Consulte [CONFIGURATION.md](CONFIGURATION.md) para obtener la matriz de claves completa por fuente, la prioridad del proveedor de razonamiento y la prioridad del backend de búsqueda web.

## Configuración

Dos cosas que probablemente querrás saber el primer día:

**Donde se guardan los archivos de investigación.** `LAST30DAYS_MEMORY_DIR` tiene como valor predeterminado `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Anule estableciendo esa var env en cualquier ruta en su shell, o `--save-dir <path>` por ejecución. Utilice `--output <file>` cuando necesite el resultado renderizado en una ruta exacta, utilizando el formato seleccionado por `--emit`. Utilice `--save-suffix=<name>` para mantener separadas múltiples variaciones del mismo tema (por ejemplo, por cliente). Cada ejecución de `--save-dir` produce `<slug>-raw[-suffix].md`. Ejecute `python3 skills/last30days/scripts/last30days.py --preflight` para revisar las escrituras planificadas antes de realizar una investigación.

**Salida estructurada para agentes y flujos de trabajo.** Solicite a `/last30days` un JSON legible por máquina para recibir el perfil de agente versionado y estable. Para uso directo del motor en scripts o desarrollo, ejecute `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; agregue `--json-profile=raw` solo cuando necesite el volcado interno sin versión de `Report`. Consulte la [referencia del campo de exportación JSON y política de versiones](docs/reference/json-export.md).

**Descubrimiento sin temas.** Solicite a `/last30days what's trending in AI agents?` que obtenga un resumen de descubrimiento clasificado en lugar de investigar un tema que ya conoce; en un host de agente, esto ejecuta el protocolo de tres comandos evaluado por el host (el modelo nombra los temas, filtra la basura, califica el valor y escribe los ángulos del contenido). Para uso directo del motor en scripts o cron, ejecute `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (one-shot: nombres de temas deterministas, sin ángulos); agregue `--emit=json` para el contrato de descubrimiento versionado. Discovery es mutuamente excluyente con un tema posicional y `--drill`.

**Monitoreo de tendencias entre ejecuciones.** El modo predeterminado produce una nueva instantánea de rebajas por ejecución. Para acumular hallazgos a lo largo del tiempo, agregue `--store` para persistir en una base de datos SQLite, luego use [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) para ejecuciones programadas (con entrega opcional de Slack/webhook en nuevos hallazgos) y [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) para resúmenes diarios/semanales. El patrón de cadencia completo se encuentra en [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Una biblioteca de investigación a la que se puede suscribir.** Solicite a `/last30days` que cree el feed de su biblioteca o utilice `python3 skills/last30days/scripts/last30days.py library feed` directamente para secuencias de comandos y desarrollo. Convierte resúmenes guardados en `index.html`, un Atom `feed.xml` local y páginas breves legibles. Agregue `--publish` solo cuando desee alojar el índice HTML y las páginas breves; la publicación es explícita y pública de forma predeterminada. Para que se pueda suscribir la fuente Atom, aloje el directorio de salida generado en un host estático como GitHub Pages.

**Busca todo lo que has investigado.** Pregúntale a `/last30days search my library for MCP servers` o `/last30days have I researched MCP servers before?`. Para uso directo del motor, ejecute `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. La búsqueda está fuera de línea y es determinista: indexa incrementalmente los mismos resúmenes guardados utilizados por el feed de la biblioteca, fusiona avistamientos de tiendas coincidentes por ejecución y agrupa los resultados por tema y fecha. Las tiradas nuevas también aparecen en una sección compacta **De su biblioteca** cuando investigaciones anteriores se superponen con el tema actual; configure `LAST30DAYS_LIBRARY_CONTEXT=off` para deshabilitar ese contexto pasivo.

Los scripts de contenedor por cliente, los subreddits de pares de categorías personalizados y el canal beta experimental para personalizaciones en progreso también se documentan en [CONFIGURATION.md](CONFIGURATION.md).

## Escaparate: feeds de investigación de la comunidad

¿Publicó una actualización recurrente de IA, una observación del mercado o una obsesión maravillosamente estrecha con los últimos 30 días? Comparta la URL de la biblioteca pública (o la URL de Atom después de alojar `feed.xml` en un host estático) en [el hilo de presentación de la comunidad](https://github.com/mvanhorn/last30days-skill/issues/532). Los feeds de la comunidad se vincularán aquí a medida que sus propietarios los envíen; Mientras tanto, el hilo es el punto de recogida.

## Cómo funciona

1. **Escribes un tema.** Persona, empresa, producto, tecnología, "X vs Y". Cualquier cosa.
2. **El agente decide quién importa.** Encuentra X identificadores (incluidos los fundadores), repositorios de GitHub, subreddits, hashtags de TikTok y canales de YouTube. Para "Kanye West" conoce r/hiphopheads, @kanyewest y "bully review" en YouTube. Para "OpenClaw", resuelve openclaw/openclaw en GitHub y obtiene recuentos de estrellas en vivo.
3. **Todas las fuentes buscadas en paralelo.** Expansión de consultas múltiples. Resultados puntuados por compromiso, relevancia y frescura.
4. **La profundidad que nadie más tiene.** Transcripciones completas de YouTube de videos de reacciones. Principales comentarios de Reddit con recuentos de votos a favor. Subtítulos de TikTok. Cuotas de polimercado. No sólo títulos y enlaces.
5. **La misma historia, fusionada.** Wireless Festival anunciado en Reddit, discutido en X, precios de las entradas en TikTok = un grupo, no tres artículos separados.
6. **Sintetizado en un resumen.** Basado en datos específicos. Citado por fuente. Clasificados según lo que realmente interactúa con la gente. No "esto es lo que encontré". Es "esto es lo que importa".
7. **Entonces se convierte en tu experto.** Después de una ejecución, tu sesión de Claude sabe todo lo que sabe la comunidad. Haga preguntas de seguimiento. Pídale que escriba indicaciones, redacte correos electrónicos, planifique viajes, diseñe sistemas, todo ello basado en lo que es real en este momento.

## Lo que dice la gente

> "Encontré una habilidad de Claude Code que investiga cualquier tema en Reddit, X, YouTube y HN de los últimos 30 días. Luego escribe las indicaciones por ti. He estado buscando manualmente en Reddit y X para investigar antes de cada contenido que escribo. Pestaña por pestaña. Hilo por hilo. Esa es la parte que lleva 90 minutos. Esto lo elimina". -@itsjasonai

> "Esta habilidad reemplazó todo mi flujo de trabajo de investigación. Le das un tema, busca en Reddit, X y la web lo que la gente realmente está hablando. No publicaciones de blogs antiguas. Conversaciones reales de los últimos 30 días". -@itswilsoncharles

> "5 de los 10 repositorios de tendencia en GitHub hoy en día son herramientas de Claude. #1: mvanhorn/last30days-skill" -@yieldhunter95

## Código abierto

Licencia MIT. Sin seguimiento. Sin análisis. Su investigación permanece en su máquina. Más de 2700 pruebas.

Construido con Python 3.12+, yt-dlp, Node.js (cliente Bird suministrado para búsqueda X) y API ScrapeCreators. Arquitectura del motor v3 por [@j-sperling](https://github.com/j-sperling).

Consulte [CONTRIBUTING.md](CONTRIBUTING.md) para abrir un PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) para obtener la lista completa de contribuyentes de la comunidad y [CHANGELOG.md](CHANGELOG.md) para ver el historial de versiones.

## Historia de las estrellas

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
