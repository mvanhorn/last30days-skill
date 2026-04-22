# Fixture: `Prompting GPT Image 2` Resolved-block regression

Documentation-grade fixture. Captures the pre-fix and post-fix shape of the
Step 0.55 Resolved block for the topic `Prompting GPT Image 2`. Not parsed
by test code — read by reviewers when evaluating regressions in
`scripts/lib/categories.py` or the SKILL.md Step 0.55 block.

The live assertion lives in `tests/test_category_integration.py`. This
markdown fixture exists so reviewers can eyeball expected behavior without
running pytest.

## Failing run (2026-04-22, pre-fix)

User ran `/last30days Prompting GPT Image 2`. Step 0.55 WebSearch returned
OpenAI-brand communities. The model resolved exactly those.

```
Resolved:
- X: @OpenAI (+ @sama, @openaidevs)
- Reddit: r/OpenAI, r/ChatGPT, r/singularity, r/artificial, r/ChatGPTpromptengineering
- TikTok: #gptimage2, #openai, #aiart
```

Engine run returned thin results. User manually intervened with "make sure
to check image generatorion reddits too" and re-ran with the image-gen
peer subs added.

## Expected run (post-fix, no user intervention)

After Step 0.55 Section 2a (category-peer expansion) and Unit 2's engine-side
merge in `auto_resolve`, the same topic produces:

```
Resolved:
- X: @OpenAI (+ @sama, @openaidevs)
- Reddit: r/OpenAI, r/ChatGPT, r/singularity, r/ChatGPTpromptengineering, r/StableDiffusion, r/midjourney, r/dalle2, r/aiArt (+ ai_image_generation peers)
- TikTok: #gptimage2, #openai, #aiart
```

The peer subs (`StableDiffusion, midjourney, dalle2, aiArt`) appear alongside
the WebSearch-returned brand subs. The `(+ ai_image_generation peers)`
annotation is the observable contract — its absence on a product-in-a-known-
category topic is a Step 0.55 regression.

## Guards

- `tests/test_categories.py::DetectCategoryHappyPath::test_prompting_gpt_image_2_matches_image_generation`
- `tests/test_resolve.py::MergeCategoryPeersHappyPath::test_image_gen_topic_appends_peers`
- `tests/test_resolve.py::AutoResolveCategoryIntegration::test_auto_resolve_returns_category_key`
- `tests/test_category_integration.py` — end-to-end over `auto_resolve` with
  a stubbed WebSearch that mimics the original failing response.

## When to update this fixture

- Category map changed (a peer sub was reordered, added, or removed).
- The observable Resolved-block annotation format changed.
- A new category was added that affects this topic.

Do not update casually. This file is the pre/post record of the 2026-04-22
failure.
