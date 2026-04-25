---
name: img
description: Use when a user asks to generate, edit, retouch, or P 图 an image on this Hermes host — including ordinary natural-language requests without `/img`, especially from Telegram attachments or local cached image paths — and the result should go through the host's OpenAI-compatible Images API.
---

# img

## Overview
Use this for both `/img ...` and ordinary requests like “生成一个图 / 帮我做张海报 / 按这张图改一下 / P 一下 / 参考这张图生成”.

Always call the installed script `~/.hermes/skills/img/scripts/remote_image.py` unless the user explicitly asks to test another local copy. Default model fallback is `gpt-image-2` unless the user explicitly asks for another image model.

If the request is clearly image generation or image editing, route here even when the user did not type `/img`.

Also use this skill for image-output directory management requests from Hermes CLI or Telegram, such as:
- “清理图片缓存 / 清理生成图片 / 删掉旧图”
- “只保留最近 20 次生成”
- “删除 7 天前的图片”
- “看看图片目录 / 列出最近图片记录 / 图片缓存占了多大”

## Core Rules
- Use attached local cache image paths or user-provided local file paths as `--input`.
- If multiple images are clearly intended, pass multiple `--input` flags in order.
- Default to `--n 1`; do not request multiple variants unless asked.
- Default to straight-through behavior when no `--profile` is specified: pass the user's prompt through as-is, keep `n=1`, and avoid injecting profile-derived quality/background/prompt suffixes.
- For plain-language image requests, default to this skill and `gpt-image-2`; `/img` is optional, not required.
- Treat the user's wording as the primary instruction. Do not add OCR/bbox/vision pre-analysis, crop/composite, or other session-model side work before the first `remote_image.py` attempt unless image choice is genuinely ambiguous or the user explicitly asks for troubleshooting.
- Use `--profile` only when the user explicitly wants an enhancement layer or a known preset such as `official-like`, `fast`, `anime-poster`, `photo-real`, or `social-cover`.
- For generation requests, prefer the script's transport abstraction instead of assuming `/images/generations` is always the correct provider endpoint.
- When the user provides an input image but the wording is clearly reference-generation intent (such as “参考这张图生成 / 以这张图为参考 / 同风格来一张”), treat it as generation, not edit.
- Current implementation keeps classic OpenAI-style defaults: text-only generation goes through generation transport, while requests with attached input images default to `/images/edits` unless the caller explicitly forces another transport.
- Treat explicit `/responses` reference-image generation as a provider-specific override, not the default path.
- Plain edits, retouching, localized fixes, and “别的不要动” requests should stay on `/images/edits`.
- Do not silently fall back to a different image backend when this skill applies unless `remote_image.py` is unavailable or the user explicitly asks for another backend/model.
- Do not use `--debug` unless the user asks.
- Do not ask the user to run `/model` just for image work.
- Never expose API keys, auth headers, or `.env` secrets.
- For output-directory cleanup/listing, only operate inside Hermes image output root (`generated-images`).
- For Telegram natural-language cleanup requests, prefer safe preview/listing first when the user is ambiguous.
- `cleanup-all` is high risk: briefly explain impact, then execute only if the user clearly asked to clear everything.

## Input Selection
Hermes often injects attachment cache paths like `vision_analyze using image_url: /abs/path/file.jpg`.

Priority:
1. explicit local file path from the user;
2. first clearly relevant attached image;
3. extra vision inspection only when image choice or edit scope is ambiguous.

## Cost / Drift Control
- Keep `n=1` by default.
- Avoid “给我几个版本” unless requested.
- If the user explicitly prefers a direct, straight-through edit (`直接改 / 直来直往 / 直接传图片和提示词`), make the first attempt a whole-image pass-through edit with the original image and prompt before adding any crop/OCR/bbox workflow.
- For small local fixes where pixel stability matters more than speed, or after one direct attempt clearly no-ops / echoes / times out, crop the smallest practical region, edit the crop, then composite back into the untouched original.
- **RELEVANT SUB-SKILL:** `fix-localized-text-in-attached-image-with-crop-and-composite`
- When layout matters and the user explicitly cares, pass `--size` yourself instead of relying on hidden inference.
- For ordinary generation, if the user did not explicitly pass `--size`, omit the size field by default and let the provider choose naturally.
- Keep explicit `--size` values as the highest priority override.
- For edits, default to omitting `size` as well; send it only when the caller explicitly asks for a specific size or selects a profile with a preset size.
- Retry at most once, and only when the fix is obvious.
- On this host, if `gpt-image-2` fails with provider-side availability errors such as `HTTP 503: No available channels for this model`, keep `gpt-image-2` as the default and report the failure.
- On this host, if `gpt-image-2` on `/images/generations` returns `provider_connection_closed` for a Chinese or heavily decorated prompt, and the user wants you to keep trying rather than stop, prefer one minimal retry with a much shorter prompt and no profile/size embellishment; if needed, try an English short prompt because that has proven more stable here than a Chinese short prompt on the same provider path.
- Only fall back to `gpt-image-1.5` when the user explicitly accepts a model downgrade or only asked for a result without insisting on `gpt-image-2`.

## Optional Profiles
`remote_image.py` supports optional request profiles for better defaults without breaking existing usage.

Recommended profiles:
- `official-like`: more polished composition / quality defaults
- `fast`: lighter augmentation, faster defaults
- `anime-poster`: poster-style anime / 国漫 output
- `photo-real`: realistic photographic output
- `social-cover`: 小红书封面 / 社媒首图 / 营销封面构图

Rules:
- If no `--profile` is passed, keep the request in straight-through mode: no prompt suffix injection, no hidden quality defaults, no auto-selected style profile.
- If the user explicitly emphasizes speed, draft/preview intent, or wording like “快速来一张 / 先来一版 / 先看方向”, prefer the lighter `fast` profile only when they also accept a profile-driven enhancement layer.
- Explicit CLI args such as `--model`, `--size`, `--quality`, `--background`, `--n` override any profile defaults.
- Do not imply that profile selection changed the model unless the actual returned `model` says so.
- `--negative-hints` is optional and should be treated as lightweight guardrails, not as a guarantee.

## Runtime Resolution
Image runtime config should prefer image-specific overrides when present, but they are optional.

Important: the authoritative runtime sources are the CLI flags, env vars, `~/.hermes/.env`, and Hermes main config `~/.hermes/config.yaml` as listed below. Do not assume a skill-local helper file such as `~/.hermes/skills/img/config.yaml` is the active runtime source unless `remote_image.py` explicitly reads it.

Generation transport policy:
1. `--transport`
2. default `auto` for generation, `images` for edit
3. in `auto`, prefer `/images/generations` for ordinary text-only generation, and default requests with attached input images to `/images/edits`; use `/responses` only when the caller explicitly overrides transport for provider-specific reference-image generation
4. if a generate request fails, preserve and inspect the full `attempts` list; the primary user-facing error should be the first real failed attempt, not a later fallback error that hides the root cause
5. keep edit on `/images/edits` unless the user explicitly asks for another protocol

Base URL priority:
1. `--base-url`
2. `OPENAI_BASE_URL_IMAGE`
3. `OPENAI_BASE_URL`
4. Hermes main config `model.base_url`
5. fallback `https://api.openai.com/v1`

Operational pitfall:
- On long-running Hermes gateway/chat processes, inherited process environment can keep older `OPENAI_BASE_URL_IMAGE` / `OPENAI_API_KEY_IMAGE` values even after `~/.hermes/.env` has been updated.
- If `remote_image.py --dry-run --show-resolved` still shows the old image base URL after editing `~/.hermes/.env`, inspect the live process environment and treat the effective runtime as the process env, not the file.
- In that case, future image requests may keep using the old provider until the relevant Hermes process/session is restarted or relaunched with the new environment.

Model priority:
1. `--model`
2. `OPENAI_IMAGE_MODEL`
3. fallback `gpt-image-2`

API key priority:
1. `--api-key`
2. `OPENAI_API_KEY_IMAGE`
3. `OPENAI_API_KEY`
4. Hermes main config `model.api_key`

## Image Output Directory Management
The script also supports management operations for Hermes CLI and Telegram natural-language requests.

Map user intent like this:

- “看看图片目录 / 列出最近生成记录 / 图片缓存占了多大”
  - `python3 ~/.hermes/skills/img/scripts/remote_image.py --list-runs`
- “删掉 7 天前的图片 / 清理 3 天前的旧图”
  - `python3 ~/.hermes/skills/img/scripts/remote_image.py --cleanup-days 7`
- “只保留最近 20 次生成 / 旧图删掉，只留最近 10 个目录”
  - `python3 ~/.hermes/skills/img/scripts/remote_image.py --cleanup-keep 20`
- “先看看会删哪些”
  - add `--dry-run`
- “把图片缓存全删了 / 清空生成图片目录”
  - `python3 ~/.hermes/skills/img/scripts/remote_image.py --cleanup-all`

Reply style for Telegram:
- Listing: summarize root path, total runs, recent items, and size.
- Cleanup dry-run: say this is a preview and list what would be deleted.
- Cleanup actual: say how many run directories were deleted and roughly how much space was freed.
- Prefer the returned `data.summary` field as the first sentence in Telegram replies, then add only the most useful details.

## Telegram Delivery
In this environment, `MEDIA:/path/to/file.png` sends the file as a Telegram photo, which may be compressed.

If the user asks for 原图 / 无压缩 / 发文件 / 不要 Telegram 压缩 / 原始质量:
1. keep the generated image locally;
2. package the final image(s) into a `.zip` file;
3. send the `.zip` via `MEDIA:/abs/path/file.zip` as the lossless deliverable;
4. preview image is optional.

If the user did not ask for original-file delivery, normal image `MEDIA:` output is fine.

## Validation
Before replying:
- confirm output files exist;
- for edits, reject byte-identical echoes of the input;
- for small targeted fixes, visually check the edited region and obvious surrounding drift;
- for Telegram lossless delivery, confirm the `.zip` exists;
- inspect `timings_ms`, `attempts`, and any recorded stream timings when the user asks why a run was slow;
- only claim a specific image model if it exactly matches the `model` field returned by `remote_image.py` stdout JSON for that run;
- if any fallback, retry, or model downgrade happened, say it explicitly in the user-facing reply and never hide it.

## User-Facing Reply Style
For ordinary image generation results, keep the reply short and avoid extra chatter.

Preferred format:
- `好了，已用 <actual_model> 给你生成了一张<简短描述>图。`
- then send the image with `MEDIA:/abs/path/file.png`

Rules:
- `<actual_model>` must be the real requested model shown in the successful `remote_image.py` result for that exact run, such as `gpt-image-2`.
- If the successful run did not use `gpt-image-2`, do not imply it did. Say the actual model name directly.
- If `gpt-image-2` failed and the user did not explicitly allow a downgrade, stop and report the failure instead of silently retrying with another model.
- Only mention base_url / debug metadata when the user explicitly asks for the interface details or requests debug output.

## Quick Reference
Generate/edit with:
```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --help
```

List profiles:
```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --list-profiles --prompt x
```

Inspect resolved defaults safely:
```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py \
  --prompt "做一张竖版国漫海报" \
  --profile anime-poster \
  --dry-run \
  --show-resolved
```

Create multiple candidates only when explicitly requested:
```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py \
  --prompt "做一张科技海报" \
  --profile official-like \
  --best-of 3 \
  --record-run \
  --archive
```

List image runs:
```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --list-runs
```

Preview deleting runs older than 7 days:
```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --cleanup-days 7 --dry-run
```

Lossless Telegram package with Python stdlib:
```bash
python3 - <<'PY'
from pathlib import Path
import zipfile
src = Path('/abs/path/output.png')
out = src.with_suffix('.zip')
with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
    zf.write(src, arcname=src.name)
print(out)
PY
```

## Common Mistakes
- Using a different image backend instead of `remote_image.py`.
- Treating `/img` as mandatory when the user already made a plain-language image request.
- Ignoring already-injected attachment cache paths.
- Requesting multiple variants when one is enough.
- Regenerating the whole image for a tiny text fix.
- Sending only `MEDIA:/image.png` when the user explicitly asked for an uncompressed/original Telegram file.
