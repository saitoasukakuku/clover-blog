# Homepage Vision Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional real image analysis to homepage carousel copy generation before DeepSeek writes the final short Chinese text.

**Architecture:** Keep `generate_homepage_copy` as the orchestration command. Add small helper methods inside the command for image encoding, OpenAI Responses API requests, response text extraction, and vision JSON normalization. The homepage rendering path continues to read the same `index_img_copy.json` shape.

**Tech Stack:** Django management command, Pillow, Python standard `urllib`, DeepSeek Chat Completions, OpenAI Responses API vision input.

---

### Task 1: Vision Data Flow Tests

**Files:**
- Modify: `白车轴草/blog/tests.py`

- [ ] **Step 1: Write a failing test for the vision stage**

Add a test under `HomepageCopyGenerationCommandTests` that creates one image, sets `DEEPSEEK_API_KEY` and `HOMEPAGE_VISION_API_KEY`, patches the OpenAI request to return JSON containing `character_candidates`, patches the DeepSeek request to return valid homepage copy, then asserts:

- OpenAI request was called once.
- Its request body contains an `input_image` data URL.
- DeepSeek request body includes the returned character name.
- `index_img_copy.json` still contains the final homepage copy fields.

- [ ] **Step 2: Write a failing test for `--skip-vision`**

Add a test proving `call_command('generate_homepage_copy', '--skip-vision')` does not call the OpenAI request method even when `HOMEPAGE_VISION_API_KEY` is configured.

- [ ] **Step 3: Run the focused tests and confirm they fail**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.HomepageCopyGenerationCommandTests
```

Expected: failures because the command has no `--skip-vision` option and no OpenAI vision methods yet.

### Task 2: Vision Command Implementation

**Files:**
- Modify: `白车轴草/blog/management/commands/generate_homepage_copy.py`

- [ ] **Step 1: Add command configuration**

Add:

- `HOMEPAGE_VISION_API_KEY`
- `HOMEPAGE_VISION_MODEL`, default `gpt-5.5`
- `--skip-vision`

- [ ] **Step 2: Add image encoding**

Add a helper that opens the homepage image, applies EXIF orientation, converts to RGB, bounds it to a maximum size, saves it as JPEG to memory, and returns a `data:image/jpeg;base64,...` URL.

- [ ] **Step 3: Add OpenAI Responses API call**

POST to `https://api.openai.com/v1/responses` with bearer auth, a text instruction, and one `input_image` item. Extract text from `output_text` first, then from `output[].content[].text` as fallback.

- [ ] **Step 4: Normalize vision JSON**

Convert JSON response into a small dict with stable fields: `subjects`, `scene`, `style`, `character_candidates`, and `concise_description`. Invalid JSON becomes a short `concise_description` fallback.

- [ ] **Step 5: Feed vision analysis into DeepSeek**

Each pending image description should contain both `metadata_description` and `vision_analysis`. Update the DeepSeek prompt so it uses `vision_analysis` first, mentions known characters only when confident, and stays concise.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.HomepageCopyGenerationCommandTests
```

Expected: all focused tests pass.

### Task 3: Verification, Docs, Deploy

**Files:**
- Modify: `AGENTS.MD`

- [ ] **Step 1: Run full verification**

Run:

```powershell
python .\白车轴草\manage.py check
python .\白车轴草\manage.py test blog
git diff --check
```

- [ ] **Step 2: Update AGENTS**

Document the new env vars, the optional vision stage, the fallback behavior, and the upload command users should run after adding images.

- [ ] **Step 3: Commit, push, deploy**

Stage only related files, commit, push to `main`, and deploy with:

```bash
sudo clover-blog-deploy
```

- [ ] **Step 4: Production verification**

Verify services are active, local homepage returns `200`, Cloudflare homepage returns `200`, and the server has the latest deployed functional commit.
