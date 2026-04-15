---
description: Research what people actually say about any topic in the last 30 days across Reddit, X, YouTube, TikTok, Hacker News, Polymarket, GitHub, and the web.
argument-hint: <topic> — e.g. "nvidia earnings reaction" or "best noise cancelling headphones"
allowed-tools: [Bash, Read, Write, AskUserQuestion, WebSearch]
---

Invoke the `last30days` skill with the user's arguments: $ARGUMENTS

Use the skill's canonical pipeline (plan → retrieve → normalize → fuse → rerank → cluster → render). If the user provided no arguments, ask them for a topic before proceeding.
