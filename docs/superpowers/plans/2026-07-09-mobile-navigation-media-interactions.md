# Mobile Navigation, Media, and Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve account navigation, mobile media management, and article interaction feedback without breaking existing fallback behavior.

**Architecture:** Keep navigation and toast presentation in the shared base template, expose dual HTML/JSON behavior from the existing interaction views, and add one validated media-resource update endpoint. Page-specific JavaScript handles progressive enhancement only.

**Tech Stack:** Django 4.2, Django templates, Bootstrap 5, vanilla JavaScript, Django TestCase.

---

### Task 1: Lock navigation behavior

**Files:**
- Modify: `白车轴草/blog/tests.py`
- Modify: `白车轴草/blog/templates/base.html`
- Modify: `白车轴草/blog/templates/home.html`
- Modify: `白车轴草/blog/views.py`

- [ ] Add tests asserting logout redirects to `home`, guest/authenticated hero actions differ, archive and tag shortcuts are absent from shared navigation, and the notification badge remains.
- [ ] Run the focused tests and confirm they fail on current labels and redirect behavior.
- [ ] Update the templates and logout view with account-aware high-frequency destinations.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Add JSON article interactions and timed toasts

**Files:**
- Modify: `白车轴草/blog/tests.py`
- Modify: `白车轴草/blog/views.py`
- Modify: `白车轴草/blog/templates/base.html`
- Modify: `白车轴草/blog/templates/post_detail.html`

- [ ] Add tests that XMLHttpRequest favorite and like submissions return JSON state and do not enqueue Django messages.
- [ ] Run the focused tests and confirm the existing redirects fail the JSON assertions.
- [ ] Add JSON responses while preserving normal redirects.
- [ ] Replace visible alerts with a hidden message queue and global timed toast renderer.
- [ ] Add article-page fetch handling, immediate button animation, state updates, and error recovery.
- [ ] Re-run focused interaction tests.

### Task 3: Make music management mobile-friendly and editable

**Files:**
- Modify: `白车轴草/blog/tests.py`
- Modify: `白车轴草/白车轴草/urls.py`
- Modify: `白车轴草/blog/views.py`
- Modify: `白车轴草/blog/templates/media_manager.html`

- [ ] Add tests for music-tab redirects, upload progress hooks, selectable resource rows, safe track rename, cover replacement, lyric text saving, collision rejection, and non-superuser denial.
- [ ] Run the focused tests and confirm the endpoint and markup are missing.
- [ ] Add explicit media-path validation and the music-resource update endpoint.
- [ ] Redesign the music inventory for desktop rows and mobile cards with one shared edit modal.
- [ ] Add XMLHttpRequest upload progress with normal multipart fallback and persistent music-tab selection.
- [ ] Re-run focused media tests.

### Task 4: Integrate and verify

**Files:**
- Modify: `AGENTS.md`

- [ ] Run Django checks, migration consistency checks, and the full `blog` test suite.
- [ ] Start the local development server and verify the affected screens at desktop and 599px mobile widths.
- [ ] Review `git diff` and `git diff --check`, update the project handoff summary, then commit only the intended files.
- [ ] Push `main` to GitHub without connecting to the production server.
