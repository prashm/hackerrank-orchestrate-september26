# Token usage: final full-dataset run

- Requests in the run: **250**
- Model calls recorded: **34** (0 live API calls, 34 served from local cache)

## Per model

| Provider | Model | Calls (live/cached) | Input tokens | Output tokens | Cache read | Cache write | Est. cost (USD) |
|---|---|---|---|---|---|---|---|
| anthropic | `claude-opus-5-5` | 0/34 | 57,837 | 27,868 | 40,152 | 2,488 | $0.8092 |

## Overall

| Metric | Value |
|---|---|
| Input tokens | 57,837 |
| Output tokens | 27,868 |
| Cache read / write tokens | 40,152 / 2,488 |
| Total tokens | 128,345 |
| Average tokens per request | 513.4 |
| Estimated total cost (USD) | $0.8092 |
| Estimated cost per request (USD) | $0.00324 |

## By purpose

| Purpose | Calls | Input | Output |
|---|---|---|---|
| image_extraction | 16 | 25,399 | 3,916 |
| message_extraction | 18 | 32,438 | 23,952 |

## Notes

- LLM is used only for evidence extraction: 16 document images (vision) and all messages (batched). Every financial decision and explanation is computed deterministically in Python.
- Results are cached by content hash in code/cache/; reruns with unchanged inputs make zero API calls (cached calls are listed with their original token counts and $0 incremental cost).
- Prices: Anthropic list prices per million tokens; cost includes prompt-cache reads/writes.
