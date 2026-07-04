# Post Markdown Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let authors write Markdown-style article content with inserted links and images while keeping the stored post body as plain text.

**Architecture:** Extend the existing `post_content` template filter into a small safe Markdown subset renderer. Add one authenticated JSON upload endpoint for article body images under `media/post_images/`. Add a textarea toolbar and local preview to the existing create/edit form template without introducing a new third-party editor.

**Tech Stack:** Django 4.2 function views, Django template filters, Pillow image validation, Bootstrap/jQuery already present in the project.

---

### Task 1: Markdown Rendering

**Files:**
- Modify: `白车轴草/blog/templatetags/blog_extras.py`
- Modify: `白车轴草/blog/tests.py`

- [ ] Write tests for headings, bold, links, images, escaping raw HTML, and blocking `javascript:` URLs.
- [ ] Run `python .\白车轴草\manage.py test blog.tests.PostContentFilterTests` and verify the new tests fail first.
- [ ] Implement the safe Markdown subset in `post_content`.
- [ ] Re-run the focused tests and verify they pass.

### Task 2: Body Image Upload Endpoint

**Files:**
- Modify: `白车轴草/blog/views.py`
- Modify: `白车轴草/白车轴草/urls.py`
- Modify: `白车轴草/blog/tests.py`

- [ ] Write tests proving anonymous users cannot upload, valid logged-in image upload returns a `/media/post_images/...` URL, and invalid files return `400`.
- [ ] Run focused tests and verify they fail first.
- [ ] Implement `upload_post_image` using existing image validation and Django storage.
- [ ] Add URL name `upload_post_image`.
- [ ] Re-run focused tests and verify they pass.

### Task 3: Editor Toolbar And Preview

**Files:**
- Modify: `白车轴草/blog/templates/create_post.html`
- Modify: `白车轴草/blog/templates/post_detail.html`
- Modify: `白车轴草/blog/tests.py`

- [ ] Write template tests that assert the create/edit form includes Markdown toolbar buttons, image upload input, upload URL, and preview container.
- [ ] Run focused tests and verify they fail first.
- [ ] Add the toolbar above the textarea in `create_post.html`, with buttons for heading, bold, link, image URL, image upload, and preview.
- [ ] Add the same toolbar to the edit form area in `post_detail.html`.
- [ ] Add JavaScript helpers to insert Markdown at the cursor, upload images, and render a lightweight escaped preview.
- [ ] Re-run focused tests and verify they pass.

### Task 4: Verification And Deployment

**Files:**
- Modify: `AGENTS.MD`

- [ ] Run `python .\白车轴草\manage.py check`.
- [ ] Run `python .\白车轴草\manage.py test blog`.
- [ ] Run `git diff --check`.
- [ ] Update `AGENTS.MD` with the Markdown editor, upload route, and new test baseline.
- [ ] Commit, push, deploy, and verify server services plus Cloudflare homepage.
