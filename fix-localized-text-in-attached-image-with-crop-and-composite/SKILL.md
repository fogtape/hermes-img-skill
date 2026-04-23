---
name: fix-localized-text-in-attached-image-with-crop-and-composite
description: Use when a user wants only a small text region in an attached image fixed while the rest of the image must remain pixel-stable, especially for Telegram image-cache paths and chart/UI card text misalignment.
---

# Fix Localized Text In Attached Image With Crop And Composite

## Overview
When the user says “只修这个字/框，别的不要动”, do **not** regenerate the full image. Crop the smallest practical region, repair only that crop, then paste the repaired region back into the untouched original.

## When to Use
- Attached/local image path is available.
- The defect is confined to one box/card/label region.
- The user explicitly wants everything else unchanged.
- AI image editing may rewrite surrounding layout if run on the whole image.

## Procedure
1. Read the image size first.
2. Use vision or a multimodal API call to estimate the target region bbox.
3. Make a slightly padded crop around that region.
4. Edit **only the crop** with `scripts/remote_image.py` (or the installed Hermes skill path).
5. In the prompt, explicitly list:
   - what to fix,
   - exact text to keep or restore,
   - elements that must remain unchanged.
6. If the API returns a different resolution, resize the edited crop back to the crop size.
7. Paste back only the repaired box/label area, not the entire crop if margins might have drifted.
8. Save final output as PNG first to avoid whole-image JPEG recompression noise.
9. Verify by diffing original vs final; changed bbox should match the pasted region.
10. Run a final vision check on the finished image.

## Verification
- Confirm target text is readable and aligned.
- Confirm `obvious_other_changes=false` via vision check if available.
- Confirm pixel diff bbox equals or is close to the intended pasted region.

## Common Mistakes
- Editing the whole image instead of the local crop.
- Saving final output as JPEG and accidentally changing pixels everywhere.
- Pasting the whole edited crop back when only the inner box was meant to change.
- Not specifying the exact restored labels in the edit prompt.

## Minimal Command Pattern
```bash
python3 scripts/remote_image.py \
  --input /path/to/crop.png \
  --size 1536x1024 \
  --prompt "Only fix the misaligned text in this box; keep icons, border, colors, layout, and everything else unchanged."
```
