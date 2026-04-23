---
name: img
description: Use when a user asks to generate, edit, retouch, or P 图 an image on this Hermes host — including ordinary natural-language requests without `/img`, especially from Telegram attachments or local cached image paths — and the result should go through the host's OpenAI-compatible Images API.
---

# img

## Overview
Use this for both `/img ...` and ordinary requests like “生成一个图 / 帮我做张海报 / 按这张图改一下 / P 一下 / 参考这张图生成”.

Always call `scripts/remote_image.py`. Default model is `gpt-image-2` unless the user explicitly asks for another image model.

If the request is clearly image generation or image editing, route here even when the user did not type `/img`.

## Core Rules
- Use attached local cache image paths or user-provided local file paths as `--input`.
- If multiple images are clearly intended, pass multiple `--input` flags in order.
- Default to `--n 1`; do not request multiple variants unless asked.
- For plain-language image requests, default to this skill and `gpt-image-2`; `/img` is optional, not required.
- Do not silently fall back to a different image backend when this skill applies unless `remote_image.py` is unavailable or the user explicitly asks for another backend/model.
- Do not use `--debug` unless the user asks.
- Do not ask the user to run `/model` just for image work.
- Never expose API keys, auth headers, or `.env` secrets.

## Input Selection
Hermes often injects attachment cache paths like `vision_analyze using image_url: /abs/path/file.jpg`.

Priority:
1. explicit local file path from the user;
2. first clearly relevant attached image;
3. extra vision inspection only when image choice or edit scope is ambiguous.

## Cost / Drift Control
- Keep `n=1` by default.
- Avoid “给我几个版本” unless requested.
- For small local fixes, do not regenerate the whole image. Crop the smallest practical region, edit the crop, then composite back into the untouched original.
- **RELEVANT SUB-SKILL:** `fix-localized-text-in-attached-image-with-crop-and-composite`
- When layout matters, choose a size roughly matching the source aspect ratio instead of blindly using the square default.
- Retry at most once, and only when the fix is obvious.
- On this host, if `gpt-image-2` fails with provider-side availability errors such as `HTTP 503: No available channels for this model`, keep `gpt-image-2` as the default and report the failure.
- Only fall back to `gpt-image-1.5` when the user explicitly accepts a model downgrade or only asked for a result without insisting on `gpt-image-2`.

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
python3 scripts/remote_image.py --help
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
