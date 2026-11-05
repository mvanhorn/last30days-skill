# /last30days

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | العربية

<p align="center">
  <img src="media/pr-assets/last30days-ad.gif" width="720" alt="last30days: البحث في أحاديث الناس وتفاعلهم باستخدام وكيل ذكاء اصطناعي" />
</p>

<p align="center">
  <a href="https://github.com/mvanhorn/last30days-skill">
    <img src="https://img.shields.io/badge/%231-Repository%20Of%20The%20Day-6f42c1?style=for-the-badge&logo=github&label=GITHUB%20TRENDING" alt="GitHub Trending #1 Repository Of The Day" />
  </a>
  <br/>
  <a href="https://trendshift.io/repositories/21997" target="_blank">
    <img src="https://trendshift.io/api/badge/repositories/21997" alt="mvanhorn/last30days-skill | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
  </a>
  <br/>
  <a href="https://github.com/mvanhorn/last30days-skill/actions/workflows/validate.yml">
    <img src="https://github.com/mvanhorn/last30days-skill/actions/workflows/validate.yml/badge.svg" alt="حالة التحقق من المشروع" />
  </a>
</p>

**محرك بحث يقوده وكيل ذكاء اصطناعي، ويستند ترتيب نتائجه إلى تصويت الناس وتفاعلهم، وحتى الأموال التي يضعونها وراء توقعاتهم**

تشرح هذه النسخة مسار العمل الحالي في الإصدار الثالث `v3`، أما المرجع المعتمد للأوامر وطريقة الإعداد الأحدث فهو [ملف المهارة](skills/last30days/SKILL.md)

**التثبيت على Claude Code (الخيار الموصى به، مع تحديثات عبر Marketplace):**

```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**التثبيت على Codex أو Cursor أو Copilot أو Gemini CLI، وأكثر من 50 بيئة تدعم [Agent Skills](https://agentskills.io):**

```
npx skills add mvanhorn/last30days-skill -g
```

يتيح الخيار `-g` المهارة لجميع مشاريع المستخدم، ويمكن حذفه لتثبيتها داخل مشروع واحد

تجد طرقا أخرى للتثبيت، منها `claude.ai` وOpenClaw والتثبيت اليدوي، في [قسم التثبيت](#التثبيت)

تعمل مصادر Reddit وHacker News وPolymarket وGitHub مباشرة دون إعداد مسبق، وعند التشغيل الأول يساعدك معالج الإعداد على إضافة X وYouTube وTikTok وarXiv وTechmeme ومصادر أخرى خلال نحو 30 ثانية

---

تصويت على Reddit، وإعجابات على X، ونصوص فيديوهات YouTube، وتفاعل على TikTok، وتوقعات على Polymarket يضع الناس أموالهم خلفها

كل يوم يترك ملايين الأشخاص إشارات عن الموضوعات التي تشغلهم وما يثقون به، وتبحث `/last30days` في هذه المصادر بالتوازي، وتقيم النتائج بحسب التفاعل الفعلي، ثم يجمعها وكيل ذكاء اصطناعي في تقرير واحد يمكن الرجوع إلى مصادره

تجد في Google صفحات ومقالات اختار محرروها ما ينشرونه، بينما تتابع `/last30days` ما يقوله الناس أنفسهم

المعلومات موزعة بين منصات لكل منها واجهة API وصلاحيات ومفاتيح وصول خاصة بها، ويختلف ما يمكن لكل مساعد ذكاء اصطناعي الوصول إليه مباشرة، فبحث Google لا يغطي محادثات Reddit وX بالطريقة نفسها، كما تختلف إمكانات ChatGPT وGemini وClaude باختلاف المصادر والتكاملات المتاحة لهما

عندما توفر مفاتيح API أو جلسات المتصفح المسموح باستخدامها، يستطيع الوكيل جمع إشارات من عدة منصات في بحث واحد، ثم ترتيبها ومقارنتها ليعرفك بما يستحق القراءة

جرب مثلا:

```
/last30days Peter Steinberger
```

لديك اجتماع غدا مع شخص وتريد معرفة ما يشغله حاليا، قد تعثر بالبحث المعتاد على ملف LinkedIn لم يحدث منذ 2023، لكن المثال الوارد في README الأصلي يجمع نشاط Peter Steinberger خلال الشهر: انضمامه إلى OpenAI للعمل على Codex، وموقفه من حظر Anthropic للوكلاء الخارجيين، و23 طلب PR بمعدل دمج 85%، وعمله على LobsterOS للتحكم بالوكلاء عبر الأجهزة، ونقاش على r/ClaudeCode حصل على 569 صوت تأييد وتباينت فيه الآراء حوله

هذه التفاصيل موزعة بين منشورات X ونقاشات Reddit وتفريغات YouTube وسجل GitHub، وهي صورة قد يصعب تجميعها من بحث واحد معتاد

## لماذا أنشئت هذه المهارة؟

بدأت تطويرها لأن متابعة أخبار الذكاء الاصطناعي أصبحت عملا يوميا، وفي أحيان كثيرة كان أشخاص يتناقشون على Reddit وX قد جربوا شيئا جديدا وتوصلوا إلى نتائج عملية قبل أن تصل هذه المعرفة إلى المقالات أو بيانات تدريب النماذج

كنت أحتاج إلى أمثلة حديثة أستند إليها وأنا أكتب الـPrompts، ثم بدأت أستخدم المهارة في مواقف أوسع، قبل مكالمة مبيعات لأفهم ما حدث مع شركة خلال آخر 30 يوما، وقبل اجتماع لأقرأ منشورات الشخص ومشاركاته في البودكاست، وقبل زيارة Disney World لأعرف الألعاب المغلقة وما يقوله الزوار عن Genie+، وقبل بناء منتج لأتعرف على المشكلات التي يناقشها مستخدموه

لو كنت ستقابل رئيسا تنفيذيا غدا، فهل تعرف ما الذي نشره وناقشه خلال الشهر الماضي؟ هذا هو نوع الأسئلة الذي صممت المهارة لأجله

## المصادر وما الذي يمكن أن تعرفه منها

| المصدر | ما الذي تبحث عنه المهارة؟ |
|--------|---------------------------|
| **Reddit** | آراء مباشرة وتعليقات مرتبة بأصوات التأييد، مع إمكانية البحث دون مفتاح API |
| **X / Twitter** | ردود الفعل السريعة، وسلاسل المتخصصين، والنقاشات فور ظهور الخبر |
| **YouTube** | تفريغ حلقات قد تبلغ 45 دقيقة والبحث داخلها عن خمس عبارات تستحق التوقف عندها |
| **TikTok** | مقاطع صناع المحتوى، مثل فيديو وصل إلى 3.6 مليون شخص ويقدم زاوية قد لا تظهر في نتائج Google |
| **Instagram Reels** | منظور صناع المحتوى ومؤشرات الثقافة البصرية مع نصوص الكلام المنطوق |
| **Hacker News** | نقاشات المطورين، مثل موضوع حصل على 825 نقطة و899 تعليقا |
| **Polymarket** | احتمالات مستمدة من أموال المشاركين، مثل توقع بنسبة 96% لمبيعات ألبوم وآخر بنسبة 4% لعملية استحواذ |
| **GitHub** | نشاط الأشخاص عبر PRs والمستودعات والإصدارات، أو قضايا ومناقشات موضوع معين |
| **Digg** | مجموعات أخبار من قسمي التقنية والذكاء الاصطناعي، مع اقتباسات من X ونسبتها إلى أصحابها، وتفعل تلقائيا عند توفر `digg-pp-cli` في `PATH` |
| **arXiv** | أوراق بحثية حديثة ضمن الفترة المطلوبة، دون مفتاح API، ويتفعل عند توفر `arxiv-pp-cli` في `PATH` |
| **Techmeme** | تغطية الأخبار التقنية ضمن نافذة البحث الزمنية، دون مفتاح API، ويتفعل عند توفر `techmeme-pp-cli` في `PATH` |
| **LinkedIn** | منشورات ومقالات مهنية، مع إعطاء المقالات وزنا أعلى ضمن الإشارات |
| **Meta Ads** | إعلانات العلامات التجارية النشطة، والنصوص والعروض وأكواد الخصم والتفريغات الصوتية |
| **StockTwits** | توجهات المتداولين، ويعمل تلقائيا عندما يكون البحث عن سهم أو عملة مشفرة |
| **Threads** | محادثات صناع المحتوى والعلامات التجارية |
| **Pinterest** | اكتشاف المنتجات والأفكار بصريا عبر الصور المحفوظة والتعليقات |
| **Xiaohongshu (RED)** | إشارات من المحتوى الصيني حول أسلوب الحياة والمنتجات، ويطلب صراحة باستخدام `--search xhs` مع تشغيل `x-mcp` أو `xiaohongshu-mcp` محليا وتسجيل الدخول |
| **Bluesky** | منشورات شبكة AT Protocol الاجتماعية اللامركزية |
| **Perplexity** | تلخيص عبر Agent API، مع بديل OpenRouter Sonar، ونتائج Search API الخام، وإمكانية طلب Deep Research صراحة |
| **DEV Community (dev.to)** | مقالات المطورين وأبرز التعليقات عبر API عامة دون مفتاح، ويمكن تفعيلها بـ`--devto-tags` أو `--search devto` أو `INCLUDE_SOURCES=devto` |
| **Truth Social** | البحث في المنشورات بعد توفير `TRUTHSOCIAL_TOKEN` |
| **Telegram** | منشورات عامة من قنوات تحدد أسماءها مسبقا، ويحتاج إلى `SCRAPECREATORS_API_KEY` وقائمة قنوات، دون اكتشاف قنوات بالكلمات المفتاحية |
| **Trustpilot** | تقييم الشركة وعدد المراجعات وملخص لآراء العملاء، عند توفر `trustpilot-pp-cli` وجلسة صالحة |
| **Amazon** | تقييمات المنتجات وأسعارها ومراجعات العملاء، عند توثيق الدخول إلى `brightdata` CLI، مع احتساب الاستخدام من رصيد Bright Data |
| **DripStack** | نشرات مالية وتحليلات عبر API عامة دون مفتاح، مع تفعيلها بـ`--search dripstack` أو `INCLUDE_SOURCES=dripstack` |
| **الوظائف وصفحات التوظيف** | إعلانات الوظائف العامة كمؤشر على أولويات الشركة، باستخدام `--hiring-signals` أو `--search jobs` أو خطة البحث |
| **الملفات المحلية** | ملفات Markdown والنصوص على جهازك، وملفات PDF عند توفر `pdftotext`، بعد تحديد `--corpus` أو `LAST30DAYS_CORPUS_DIRS` |
| **الويب** | المقالات والتغطيات التحريرية والمقارنات، بوصفها مصدرا إضافيا ضمن البحث |

يمكنك أن تطلب من `/last30days` تضمين المصادر المناسبة لسؤالك، لكن إتاحتها تتوقف على مفاتيح الوصول والأدوات المثبتة ونوع الموضوع وخيارات التفعيل، والتفاصيل في [متطلبات المصادر](CONFIGURATION.md#source-requirements)

قد يحظى نقاش على Reddit بـ1,500 صوت تأييد بينما تمر مقالة دون اهتمام يذكر، وقد يحقق فيديو على TikTok نحو 3.6 مليون مشاهدة، أو يجذب سوق توقعات تداولات بقيمة 66 ألف دولار، وكلها إشارات مفيدة عند قراءة ما يهتم به الناس، مع مراعاة طبيعة كل منصة وما تقيسه أرقامها

ترتب المهارة النتائج وفق التفاعل الاجتماعي ومدى صلتها بالسؤال، وهو معيار مختلف عن ترتيب صفحات الويب في نتائج البحث المعتادة

## استخدامات عملية

**التحضير لاجتماع:** `/last30days Peter Steinberger` يجمع أخبار انضمامه إلى فريق Codex في OpenAI، ونقاشه حول حظر Anthropic للوكلاء الخارجيين، و23 طلب PR بمعدل دمج 85%، وعمله على LobsterOS، ونقاش r/ClaudeCode الذي حصل فيه تعليق حول قيود استخدام OpenClaw على 227 صوت تأييد، وهي تفاصيل قد لا تجدها في ملف LinkedIn

**قراءة إشارات التوظيف:** `/last30days Listen Labs --hiring-signals` يبحث في الوظائف المفتوحة وصفحات التوظيف، ثم يستشهد بها لتفسير المجالات التي يبدو أن الشركة تركز عليها، مثل أمن المؤسسات أو نجاح العملاء أو البنية التحتية، دون التعامل معها باعتبارها وعودا بخارطة منتجات مستقبلية

**اكتشاف موضوع يستحق المتابعة مبكرا:** اسأل `/last30days what's exploding in AI agents?` لتنتقل المهارة إلى وضع الاكتشاف، وتراجع قوائم Reddit وأبرز قصص Hacker News ومحتوى Digg ومنشورات X عند توفر الوصول، ثم يختار الوكيل الموضوعات الجديرة بالمتابعة ويقترح زوايا لمقالات أو حلقات بودكاست، لتخرج بقائمة من 5 إلى 10 موضوعات مرتبة وفق سرعة انتشارها، مع أرقام من عدة مصادر وأمر متابعة جاهز لكل موضوع

**متابعة خبر بعد ظهوره:** `/last30days Kanye West` مثال من README الأصلي يجمع منع دخوله المملكة المتحدة وإلغاء Wireless Festival وانسحاب الرعاة، وفي الوقت نفسه وصول BULLY للمركز الثاني على Billboard، وعودة Fantano لمراجعة الألبوم (653 ألف مشاهدة)، وظهور Lauryn Hill وTravis Scott في حفل SoFi Homecoming المكون من 44 أغنية، ثم احتمال 86% في Polymarket لسؤال حول عودته للتغريد، ويضم التقرير في ذلك المثال 23 نقاشا من Reddit و17 فيديو من YouTube ونحو 86 ألف صوت تأييد

**مقارنة أدوات:** `/last30days OpenClaw vs Hermes vs Paperclip` يجمع أعداد نجوم GitHub مباشرة، وكانت الأرقام في المثال 351 ألفا لـOpenClaw و31 ألفا لـHermes و49 ألفا لـPaperclip، ثم يقارن التنفيذ والذاكرة والأمان والاستخدام الأنسب، ويورد آراء المجتمع، ومنها تشبيه @IMJustinBrooke لشخصيتي Charmander وCharizard

**فهم حدث عالمي:** `/last30days Iran vs USA` مثال على تقرير من اليوم الثامن والثلاثين للحرب المذكورة في README الأصلي، تضمن مهلة ترامب لإعادة فتح مضيق هرمز، وإسقاط طائرتين أمريكيتين، وسعر النفط عند 126 دولارا للبرميل، وتصريح IEA حول تعطل الإمدادات، واحتمالا بنسبة 74% لوقف إطلاق النار بحلول 31 ديسمبر على Polymarket، مع 27 منشورا من X و10 فيديوهات من YouTube و20 سوق توقعات، وهذه أرقام من التقرير المذكور وليست تحديثا مباشرا

**التخطيط لرحلة:** `/last30days Universal Epic Universe` يجمع أخبار التوسعة التي بدأ العمل عليها، وتصريح Project 680، والتجهيزات التي تشير إلى عرض ألعاب نارية لم يعلن عنه رسميا، ومتوسط انتظار Mine-Cart Madness الذي بلغ 148 دقيقة في المثال، وعدم توفر بطاقة سنوية وقتها، وانزعاج السكان المحليين، وتوقف Stardust Racers للصيانة حتى 5 أبريل

**تعلم مهارة بسرعة:** `/last30days Nano Banana Pro prompting` يجمع تجارب المستخدمين مع كتابة الـPrompts، ومنها استخدام بنية JSON متداخلة اقترحها @pictsbyai لتجنب اختلاط مفاهيم الصورة، وتفضيل التعديل قبل إعادة التوليد، ثم يمكن أن يساعدك في كتابة Prompt قابل للاستخدام يستند إلى تلك التجارب

## ما الجديد؟

راجع [أحدث إصدار منشور](https://github.com/mvanhorn/last30days-skill/releases/latest) لمعرفة النسخة الحالية، أو [سجل التغييرات](CHANGELOG.md) للتفاصيل حسب الإصدار، أما التغييرات التي لم تصدر بعد فتسجل في [changelog.d/](changelog.d/)

تصف هذه الصفحة الكود الموجود حاليا في الفرع الأساسي، وقد تختلف بعض التفاصيل عن النسخة المثبتة لديك

## التثبيت

| البيئة | طريقة التثبيت | التحديث |
|--------|--------------|---------|
| **Claude Code** (موصى به) | `/plugin marketplace add mvanhorn/last30days-skill` | تلقائيا عبر Marketplace أو بالأمر `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` ثم `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex وCursor وCopilot وGemini CLI وأكثر من 50 بيئة [Agent Skills](https://agentskills.io)** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** | [تحميل `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) ورفعه من Customize > Skills > + > Create skill > Upload a skill | تحميل الإصدار الأحدث ورفعه مجددا |
| **Claude Desktop** | [تحميل `.mcpb` المناسب لجهازك](https://github.com/mvanhorn/last30days-skill/releases/latest) وإضافته من Settings > Extensions | تحميل الحزمة الجديدة وإضافتها |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (موصى به)

```
/plugin marketplace add mvanhorn/last30days-skill
```

التثبيت عبر Marketplace مناسب لمن يريد تحديثات تلقائية عند نشر إصدار جديد، ويمكن طلب التحقق من التحديث يدويا عبر `claude plugin update last30days@last30days-skill`

وإذا كنت تفضل التثبيت عبر Agent Skills على Claude Code، فهذا الخيار متاح أيضا:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

### رابط للتجربة السريعة

[تجربة Last30Days عبر Telegram أو WhatsApp](https://app.clawmama.run/skills/2ne05f/hermes?utm_source=github&utm_medium=issue&utm_campaign=skill_outreach_mvanhorn_last30days_skill)

يمكن تثبيت إضافة Marketplace ونسخة `npx skills` معا، لكن Claude Code يعرض `/last30days` مرتين عند تفعيل الطريقتين، ولهذا يفضل اختيار طريقة تثبيت واحدة على الجهاز

### Grok (xAI Build CLI)

يدعم [Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) تثبيت last30days كإضافة مباشرة تتبع المستودع:

```bash
grok plugin install mvanhorn/last30days-skill
```

أو إضافة المستودع كمصدر للـMarketplace ثم تثبيت الإضافة بالاسم:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

يتيح `--trust` تخطي تأكيد التثبيت، ويحدث الأمر `grok plugin update last30days` الإضافة لاحقا، ويستطيع Grok أيضا قراءة ملفات Claude Code للتوافق، بينما تعتمد طريقة Grok الأساسية على ملفات `.grok-plugin/`، وهي الطريقة المستخدمة في [سوق إضافات xAI](https://github.com/xai-org/plugin-marketplace)، ويبقى `npx skills add` خيارا صالحا عبر البيئات المختلفة

أما على Grok Bot، فيبدأ البحث في X باستخدام أدوات البوت المدمجة ثم موصل X الخاص به، مع إمكان استخدام API الرسمية عبر `X_BEARER_TOKEN` كبديل

### Codex وCursor وCopilot وGemini CLI وبقية بيئات Agent Skills

تدعم أداة [Agent Skills](https://agentskills.io) أكثر من 50 بيئة، منها `codex` و`cursor` و`github-copilot` و`gemini-cli` و`claude-code` و`windsurf` و`cline` و`continue` و`roo` و`aider-desk` و`opencode` و`goose`، والقائمة الكاملة في [vercel-labs/skills](https://github.com/vercel-labs/skills)

```bash
npx skills add mvanhorn/last30days-skill -g
```

يثبت `-g` المهارة على مستوى المستخدم لتتوفر في جميع مشاريعه، وبدونه يثبتها `npx skills` محليا داخل `./.skills/` في المشروع

يمكن استخدام Codex Desktop وغيره من البيئات المعتمدة على المجلدات داخل مجلدات عادية أو مستودعات Git، ولمعاينة ملفات الإعداد التي ستقرأ أو تكتب قبل بدء البحث، شغل `scripts/last30days.py --preflight` من مجلد المهارة المثبتة، أو نفذ `python3 skills/last30days/scripts/last30days.py --preflight` من نسخة المستودع المصدرية

يعرض الفحص مصدر الإعدادات وخطة التعامل مع ملفات تعريف ارتباط المتصفح والملفات التي قد تكتب والأوامر الاختيارية، دون قراءة Cookies أو الكتابة أو بدء البحث، ولا يشترط تشغيله في الإعداد الأول

لتحديد بيئة بعينها أو أكثر:

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

للتحديث لاحقا:

```bash
npx skills update last30days -g
```

ولتحديث جميع المهارات المثبتة عالميا عبر `npx skills`:

```bash
npx skills update -g
```

يمكن عرض المهارات أو إزالتها باستخدام `npx skills list -g` و`npx skills remove last30days -g`

### claude.ai (الويب)

1. [حمل ملف `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) من أحدث إصدار
2. انتقل إلى [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. اضغط `+`، ثم `Create skill`، وبعدها `Upload a skill`، واختر الملف

تأكد من تفعيل `Code execution and file creation` ضمن Capabilities حتى تتمكن المهارة من العمل

### Claude Desktop

يثبت Claude Desktop المهارة كخادم MCP باستخدام ملف `.mcpb`، وهي حزمة جاهزة لإضافات Model Context Protocol

1. من [أحدث إصدار](https://github.com/mvanhorn/last30days-skill/releases/latest)، حمل الملف المناسب لجهازك:
   - لأجهزة macOS بمعالجات Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - لأجهزة macOS بمعالجات Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - لأجهزة Linux بمعمارية x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. افتح Claude Desktop ثم Settings > Extensions وأضف الملف
3. أدخل مفاتيح API الخاصة بالمصادر التي تريد تفعيلها عند الطلب، وجميع الحقول اختيارية، ويمكن الاكتفاء بالبحث عبر الويب إذا تركتها فارغة، وتخزن المفاتيح في Keychain الخاص بجهازك
4. أعد تشغيل Claude Desktop، ثم اطلب بحثا عن `Peter Steinberger` أو أي موضوع آخر ليستخدم أداة `research`

**متطلبات التشغيل:** تحتاج إلى Python 3.12 أو أحدث ضمن `PATH`، إذ تحتوي الحزمة على كود البحث وتستخدم Python المثبت محليا، ويمكن تنزيله من [python.org](https://www.python.org/downloads/)

**المفاتيح منفصلة عن Claude Code:** إذا سبق أن أضفت مفاتيح API إلى `~/.config/last30days/.env`، فستحتاج إلى إدخالها في Claude Desktop مرة أخرى لأن كل واجهة تحتفظ بإعدادات الوصول الخاصة بها

دعم Windows مؤجل حاليا إلى حين استكمال نقاط التشغيل الخاصة بكل منصة، ويمكن متابعة مستجداته في قضية منفصلة على GitHub

### OpenClaw

```bash
clawhub install last30days-official
```

للعمليات المتعلقة بحساب X خارج بحث `/last30days`، مثل نشر التغريدات والردود أو تصدير المتابعين أو التعامل مع الوسائط والمراقبة، يمكن استخدام [TweetClaw](https://github.com/Xquik-dev/tweetclaw) كإضافة اختيارية لـOpenClaw، وهي من تطوير Xquik-dev وليست متطلبا لتشغيل last30days أو توصية رسمية منها

### التثبيت اليدوي (للمطورين)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

يحافظ الرابط الرمزي على اتصال النسخة المثبتة بملفات المشروع أثناء تعديلها، ولإنشاء ملف `.skill` من الكود المصدري المخصص لـ`claude.ai`، شغل `bash skills/last30days/scripts/build-skill.sh` للحصول على `dist/last30days.skill`

تعمل مصادر Reddit وتعليقاته وHacker News وPolymarket وGitHub مباشرة، وعند أول استخدام يرشدك معالج الإعداد إلى بقية المصادر، ومنها أدوات arXiv وTechmeme المجانية

## استخدم مفاتيح API الخاصة بك

لكل منصة طريقة وصول وصلاحيات مستقلة، وبإمكانك جمع مصادرها في بحث واحد عند توفير مفاتيح API أو جلسات المتصفح المناسبة

| المصادر | ما الذي تحتاج إليه؟ | التكلفة |
|---------|---------------------|---------|
| Reddit مع التعليقات وHN وPolymarket وGitHub وStockTwits | لا شيء | مجاني |
| arXiv وTechmeme | أدوات CLI مجانية يثبتها معالج الإعداد في التشغيل الأول | مجاني |
| X / Twitter | `X_BEARER_TOKEN` لاستخدام API الرسمية للمنشورات الحديثة، عادة خلال نحو أسبوع ما لم يملك مشروعك وصولا إلى الأرشيف الكامل، ويعمل كبديل في Grok Bot بعد أدوات X المدمجة أو اختياريا في غيره عبر `LAST30DAYS_X_BACKEND=xapi`، أو يمكنك تسجيل الدخول إلى x.com في المتصفح أو توفير `XQUIK_API_KEY` أو `XAI_API_KEY` | رصيد API تابع لمشروع X الخاص بك، أو Cookies مجانية، أو تسعير المزود الآخر |
| YouTube | `brew install yt-dlp` | مجاني |
| Bluesky | كلمة مرور التطبيق من bsky.app | مجاني |
| TikTok وInstagram وThreads وPinterest وLinkedIn وتعليقات YouTube | مفتاح ScrapeCreators | 10,000 استدعاء مجاني، ثم الدفع حسب الاستخدام |
| Xiaohongshu (RED) | تشغيل `x-mcp` أو `xiaohongshu-mcp` محليا مع جلسة دخول، ثم اختيار `--search xhs` أو `INCLUDE_SOURCES=xiaohongshu` في `.env`، وتفحص last30days العنوانين `http://localhost:18060` و`http://host.docker.internal:18060` تلقائيا، أو تستخدم `XIAOHONGSHU_API_BASE` لعنوان آخر | لا تحتاج إلى مفتاح API خاص بـlast30days، وتتوقف التكلفة على خدمة جلسة المتصفح المحلية |
| DripStack | تفعيل `--search dripstack` أو `INCLUDE_SOURCES=dripstack` | API عامة مجانية دون مفتاح |
| Perplexity Agent API وSearch API وDeep Research | مفتاح Perplexity أو OpenRouter كبديل Sonar | الدفع حسب الاستخدام، ويتيح المفتاح المباشر Agent API وDeep Research في الخلفية |
| البحث في الويب | مفتاح Brave Search | 2,000 استعلام مجاني شهريا |

### macOS Keychain (اختياري)

يمكنك حفظ المفاتيح في Keychain على macOS بدلا من ملف `.env`، وتقرأها المهارة تلقائيا عند غياب القيمة من إعدادات البيئة أو ملف `.env`، اللذين يملكان أولوية أعلى

```bash
# إعداد تفاعلي: يطلب المفاتيح المعروفة ويمكن تخطي أي مفتاح بإدخال فارغ
skills/last30days/scripts/setup-keychain.sh

# أو حفظ مفتاح واحد يدويا
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# عرض العناصر أو حذفها
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

تخزن العناصر باسم خدمة `last30days-<KEY>` للمستخدم الحالي، ولا يجري تحميلها على Linux أو Windows

إذا كانت مفاتيحك محفوظة بأسماء خدمات أخرى في Keychain، فيمكن تعريف خريطة `LAST30DAYS_KEYCHAIN_ALIASES` كما هو موضح في [دليل الإعدادات](CONFIGURATION.md#reusing-existing-macos-keychain-items) دون نسخ القيم السرية

راجع [CONFIGURATION.md](CONFIGURATION.md) للمتطلبات الخاصة بكل مصدر وأولوية مزودي الاستدلال والبحث في الويب

## الإعدادات

**أين تحفظ نتائج البحث؟** يستخدم `LAST30DAYS_MEMORY_DIR` المسار `~/Documents/Last30Days/` افتراضيا، وعلى Windows المسار `C:\Users\<you>\Documents\Last30Days\`، ويمكن تغييره عبر متغير البيئة أو `--save-dir <path>` في تشغيل محدد، كما يتيح `--output <file>` تحديد ملف نتيجة بعينه وفقا للصيغة التي تختارها باستخدام `--emit`، و`--save-suffix=<name>` لحفظ نسخ متعددة من الموضوع نفسه، وينتج كل تشغيل مع `--save-dir` ملفا بصيغة `<slug>-raw[-suffix].md`، ولرؤية عمليات الكتابة المخطط لها مسبقا استخدم `--preflight`

**مخرجات منظمة للوكلاء والأتمتة:** اطلب من `/last30days` إخراج JSON للحصول على ملف تعريف الوكيل المستقر ذي الإصدار المحدد، وإذا كنت تستخدم السكربت مباشرة لأغراض التطوير أو الأتمتة فنفذ `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`، وأضف `--json-profile=raw` فقط عندما تحتاج إلى بيانات `Report` الداخلية دون ترقيم إصدار، وتفاصيل الحقول في [مرجع تصدير JSON](docs/reference/json-export.md)

**اكتشاف موضوعات دون تحديد موضوع مسبقا:** اسأل `/last30days what's trending in AI agents?` لتحصل على قائمة بحث مرتبة، وفي بيئات الوكلاء يحدد النموذج الموضوعات ويستبعد النتائج غير المفيدة ويقيم صلاحيتها ويقترح زوايا محتوى، أما عند تشغيل السكربت مباشرة أو ضمن Cron فاستخدم `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` للحصول على أسماء موضوعات محددة بقواعد ثابتة دون اقتراح الزوايا، ويمكن إضافة `--emit=json` لإخراج منظم بإصدار محدد، ولا يمكن جمع وضع الاكتشاف مع موضوع موضعي أو `--drill` في الأمر نفسه

**مراقبة الاتجاهات عبر مرات التشغيل:** تحصل افتراضيا على ملف Markdown جديد لكل بحث، ولتجميع النتائج بمرور الوقت استخدم `--store` لتخزينها في SQLite، ثم [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) للمتابعة المجدولة مع خيار إشعار Slack أو Webhook عند العثور على نتائج جديدة، و[`scripts/briefing.py`](skills/last30days/scripts/briefing.py) لتقارير يومية أو أسبوعية، والتفاصيل في [دليل المراقبة](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings)

**مكتبة أبحاث قابلة للاشتراك:** يمكنك أن تطلب من `/last30days` إنشاء مكتبة أبحاثك، أو تنفيذ `python3 skills/last30days/scripts/last30days.py library feed` مباشرة أثناء التطوير أو الأتمتة، لتحويل التقارير المحفوظة إلى `index.html` وخلاصة Atom باسم `feed.xml` وصفحات للقراءة، ويبقى النشر اختياريا عبر `--publish`، وعند تفعيله تصبح الصفحات عامة افتراضيا، ولجعل خلاصة Atom قابلة للاشتراك استضف المجلد الناتج على خدمة صفحات ثابتة مثل GitHub Pages

**البحث في الأبحاث السابقة:** اطلب `/last30days search my library for MCP servers` أو `/last30days have I researched MCP servers before?`، وللاستخدام المباشر نفذ `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`، ويعمل هذا البحث محليا دون اتصال، مع فهرسة تدريجية للتقارير المحفوظة ودمج النتائج المسجلة وتجميعها حسب الموضوع والتاريخ، كما قد تظهر فقرة **From your library** في الأبحاث الجديدة حين توجد أبحاث سابقة ذات صلة، ويمكن إيقافها عبر `LAST30DAYS_LIBRARY_CONTEXT=off`

تجد أيضا في [CONFIGURATION.md](CONFIGURATION.md) أمثلة للتهيئة بحسب العميل، وقوائم Subreddits المتخصصة، والتجربة الاختبارية `beta` للتعديلات قيد التطوير

## معرض أبحاث المجتمع

إذا كنت تنشر تحديثات دورية عن الذكاء الاصطناعي أو تتابع سوقا أو موضوعا محددا باستخدام last30days، فيمكنك مشاركة رابط المكتبة العامة أو خلاصة Atom بعد استضافة `feed.xml` في [نقاش المجتمع المخصص](https://github.com/mvanhorn/last30days-skill/issues/532)، وستضاف مساهمات المجتمع هنا مع وصولها

## كيف تعمل؟

1. **تحدد الموضوع:** شخصا أو شركة أو منتجا أو تقنية أو مقارنة بين أداتين
2. **يحدد الوكيل الجهات والحسابات المرتبطة به:** حسابات X، ومنها حسابات المؤسسين، ومستودعات GitHub، ومجتمعات Reddit، ووسوم TikTok، وقنوات YouTube، فمثلا يجمع ما يرتبط بـKanye West أو مستودع `openclaw/openclaw` بحسب السؤال
3. **يبحث في المصادر بالتوازي:** مع صيغ بحث متعددة، ثم يقيم النتائج حسب التفاعل والارتباط والحداثة
4. **يقرأ التفاصيل:** تفريغات YouTube، وأبرز تعليقات Reddit مع الأصوات، ووصف TikTok، واحتمالات Polymarket، حتى لا يقتصر التقرير على العناوين والروابط
5. **يجمع الإشارات المتكررة:** إذا ظهر خبر Wireless Festival على Reddit وناقشه الناس على X وتناول TikTok أسعار تذاكره، يجمعها في موضوع واحد
6. **يكتب التقرير:** مع شواهد قابلة للتتبع وروابط مصادر وترتيب بحسب اهتمام الناس الفعلي
7. **تتابع الأسئلة:** يستطيع المساعد بعد البحث استخدام المعلومات المجموعة للإجابة عن أسئلة لاحقة، أو صياغة Prompts ورسائل وتخطيط رحلات أو مناقشة تصميم برمجي، ضمن حدود الأدلة الموجودة

## ماذا قال المستخدمون؟

> وجدت مهارة في Claude Code تبحث في أي موضوع خلال آخر 30 يوما عبر Reddit وX وYouTube وHacker News، ثم تساعدك في كتابة الـPrompts، كنت أبحث يدويا في Reddit وX قبل كل قطعة محتوى، صفحة بعد صفحة ونقاشا بعد نقاش، وهذا الجزء كان يستغرق 90 دقيقة، والمهارة اختصرت علي هذه الخطوات
>
> @itsjasonai

> هذه المهارة حلت محل خطوات البحث المعتادة عندي، تعطيها موضوعا فتبحث في Reddit وX والويب عما يناقشه الناس فعليا خلال آخر 30 يوما
>
> @itswilsoncharles

> خمسة من أكثر عشرة مستودعات انتشارا على GitHub اليوم تخص أدوات Claude، والمركز الأول كان `mvanhorn/last30days-skill`
>
> @yieldhunter95

## مفتوح المصدر

المشروع مرخص وفق MIT، دون تتبع أو تحليلات استخدام، وتبقى أبحاثك على جهازك وفق التفاصيل المذكورة في [التعامل مع البيانات](#التعامل-مع-البيانات)

لأوامر الاختبار راجع [CONTRIBUTING.md](CONTRIBUTING.md#tests)

بني باستخدام Python 3.12+ و`yt-dlp` وNode.js وعميل Bird المضمن للبحث في X وScrapeCreators API، وساهم [@j-sperling](https://github.com/j-sperling) في بنية المحرك للإصدار الثالث

يمكنك مراجعة [دليل المساهمة](CONTRIBUTING.md) لتقديم PR، و[قائمة المساهمين](CONTRIBUTORS.md)، و[سجل الإصدارات](CHANGELOG.md)

## التعامل مع البيانات

تعمل المهارة محليا بشكل افتراضي، ولا ترسل بيانات إلى الخارج إلا عبر واجهات البحث التي تستدعيها، مثل Reddit أو ScrapeCreators

**إعادة ترتيب النتائج:** إذا أعددت مزود استدلال باستخدام `OPENAI_API_KEY` أو `XAI_API_KEY` أو `OPENROUTER_API_KEY` أو `GOOGLE_API_KEY` أو `GEMINI_API_KEY` أو `GOOGLE_GENAI_API_KEY`، يرسل المحرك موضوع البحث ومقتطفات المحتوى المسترجعة إلى المزود لتقييم الصلة، وهذا إجراء اختياري، وعند غياب مفاتيح الاستدلال يستخدم التقييم المحلي دون طلب خارجي لإعادة الترتيب، وتجد أولوية المزودين في [CONFIGURATION.md](CONFIGURATION.md)

**التخزين المحلي:** إذا ضبطت `LAST30DAYS_STORE=1`، تحفظ النتائج في قاعدة SQLite باسم `research.db` داخل مجلد الحفظ الفعلي، مع أولوية `--save-dir` على `LAST30DAYS_MEMORY_DIR`، وإن لم يحدد مجلد للحفظ فالموقع هو `~/.local/share/last30days/research.db`، وتفعيل التخزين لا يرفع قاعدة البيانات إلى خدمة بعيدة، وتشرح [CONFIGURATION.md](CONFIGURATION.md#--store-flag) كيفية الاحتفاظ بالبيانات وحذفها بعد إيقاف العمليات التي تستخدم القاعدة

## سجل النجوم على GitHub

<a href="https://star-history.dera.page/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
