# Homepage Vision Copy Design

## Goal

Improve homepage carousel copy generation so new background images can be described from actual visual content, not only filename, image size, brightness, and average color.

## Current Problem

`generate_homepage_copy` currently sends DeepSeek a text-only metadata description. The model receives no pixels, so it cannot identify subjects, scenes, anime characters, props, or composition. This causes generic color-based copy.

## Design

Add an optional vision analysis stage before DeepSeek copy generation.

The command will:

1. Read each pending image from `media/index_img/`.
2. Keep the existing metadata description as a fallback.
3. If `HOMEPAGE_VISION_API_KEY` is configured and `--skip-vision` is not passed, resize the image to a bounded JPEG data URL and send it to the OpenAI Responses API vision endpoint.
4. Ask the vision model for compact JSON: subjects, scene, style, possible character names, and one concise visual description.
5. Send both metadata and vision analysis to DeepSeek.
6. Ask DeepSeek to prefer the vision analysis, use character names only when the vision model is confident, and keep homepage copy short.
7. Write the final homepage copy to `media/index_img_copy.json`, preserving existing skip and `--force` behavior.

## Configuration

- `HOMEPAGE_VISION_API_KEY`: required to enable visual analysis.
- `HOMEPAGE_VISION_MODEL`: optional; defaults to `gpt-5.5`.
- `--skip-vision`: command option for forcing the old metadata-only path.

The command must not use `OPENAI_API_KEY` implicitly. This prevents accidental cost if another part of the server has an OpenAI key for unrelated work.

## Failure Behavior

If the vision key is missing, the command keeps using metadata-only generation.

If the vision request fails for one image, the command writes a warning and falls back to metadata for that image instead of aborting the whole batch.

## Testing

Add tests that prove:

- With `HOMEPAGE_VISION_API_KEY`, the command sends an image data URL to the vision API.
- DeepSeek receives the vision analysis, including character candidates.
- `--skip-vision` avoids the vision API even when a key is configured.
- The generated copy cache format remains compatible with the existing homepage rendering code.
