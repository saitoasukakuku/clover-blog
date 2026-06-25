# POST-Only Article and Draft Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make article and draft deletion accept only authenticated POST requests submitted by CSRF-protected forms.

**Architecture:** Keep the existing URLs, ownership checks, status checks, and redirects. Add Django's `require_POST` decorator at the view boundary, then replace the two destructive links with small POST forms so normal page usage still works.

**Tech Stack:** Django 4.2 function views, Django templates, Django `TestCase`.

---

## File Map

- Modify `白车轴草/blog/tests.py`: add focused request-method, deletion, and template regression tests.
- Modify `白车轴草/blog/views.py`: restrict `delete_draft` and `delete_post` to POST.
- Modify `白车轴草/blog/templates/drafts.html`: submit draft deletion through a CSRF-protected POST form.
- Modify `白车轴草/blog/templates/post_detail.html`: submit published article deletion through a CSRF-protected POST form.

### Task 1: Add failing deletion method and form tests

**Files:**
- Modify: `白车轴草/blog/tests.py`

- [ ] **Step 1: Add focused deletion tests**

Add this class before `StartupPostCommandTests`:

```python
class PostDeletionTests(TestCase):
    def test_delete_draft_rejects_get_request(self):
        author = User.objects.create_user(
            username='draft-author',
            password='StrongPass12345',
        )
        draft_post = Post.objects.create(
            author=author,
            title='不能通过 GET 删除的草稿',
            category='life',
            content='草稿正文',
            status='draft',
        )
        self.client.login(
            username='draft-author',
            password='StrongPass12345',
        )

        response = self.client.get(
            reverse('delete_draft', args=[draft_post.id]),
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Post.objects.filter(id=draft_post.id).exists())

    def test_author_can_delete_draft_with_post_request(self):
        author = User.objects.create_user(
            username='draft-author',
            password='StrongPass12345',
        )
        draft_post = Post.objects.create(
            author=author,
            title='通过 POST 删除的草稿',
            category='life',
            content='草稿正文',
            status='draft',
        )
        self.client.login(
            username='draft-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_draft', args=[draft_post.id]),
        )

        self.assertRedirects(response, reverse('drafts'))
        self.assertFalse(Post.objects.filter(id=draft_post.id).exists())

    def test_drafts_page_uses_post_form_for_deletion(self):
        author = User.objects.create_user(
            username='draft-author',
            password='StrongPass12345',
        )
        draft_post = Post.objects.create(
            author=author,
            title='带删除表单的草稿',
            category='life',
            content='草稿正文',
            status='draft',
        )
        self.client.login(
            username='draft-author',
            password='StrongPass12345',
        )

        response = self.client.get(reverse('drafts'))

        delete_draft_url = reverse(
            'delete_draft',
            args=[draft_post.id],
        )
        self.assertContains(
            response,
            f'<form method="post" action="{delete_draft_url}">',
        )
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_delete_published_post_rejects_get_request(self):
        author = User.objects.create_user(
            username='published-author',
            password='StrongPass12345',
        )
        published_post = Post.objects.create(
            author=author,
            title='不能通过 GET 删除的文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        self.client.login(
            username='published-author',
            password='StrongPass12345',
        )

        response = self.client.get(
            reverse('delete_post', args=[published_post.id]),
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            Post.objects.filter(id=published_post.id).exists()
        )

    def test_author_can_delete_published_post_with_post_request(self):
        author = User.objects.create_user(
            username='published-author',
            password='StrongPass12345',
        )
        published_post = Post.objects.create(
            author=author,
            title='通过 POST 删除的文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        self.client.login(
            username='published-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_post', args=[published_post.id]),
        )

        self.assertRedirects(response, reverse('index'))
        self.assertFalse(
            Post.objects.filter(id=published_post.id).exists()
        )

    def test_post_detail_uses_post_form_for_deletion(self):
        author = User.objects.create_user(
            username='published-author',
            password='StrongPass12345',
        )
        published_post = Post.objects.create(
            author=author,
            title='带删除表单的文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        self.client.login(
            username='published-author',
            password='StrongPass12345',
        )

        response = self.client.get(
            reverse('post_detail', args=[published_post.id]),
        )

        delete_post_url = reverse(
            'delete_post',
            args=[published_post.id],
        )
        self.assertContains(
            response,
            f'<form method="post" action="{delete_post_url}">',
        )
        self.assertContains(response, 'csrfmiddlewaretoken')
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.PostDeletionTests
```

Expected: four failures caused by GET returning a redirect instead of 405 and both templates lacking the deletion forms. The two POST deletion tests should already pass because POST currently reaches the existing views.

- [ ] **Step 3: Commit the failing regression tests**

```powershell
git add .\白车轴草\blog\tests.py
git commit -m "Test POST-only article deletion"
```

### Task 2: Restrict deletion views to POST

**Files:**
- Modify: `白车轴草/blog/views.py`

- [ ] **Step 1: Add `require_POST` to draft deletion**

Change the decorators to:

```python
@login_required
@require_POST
def delete_draft(request, post_id):
```

- [ ] **Step 2: Add `require_POST` to published article deletion**

Change the decorators to:

```python
@login_required
@require_POST
def delete_post(request, post_id):
```

- [ ] **Step 3: Run the focused tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.PostDeletionTests
```

Expected: the GET rejection and POST deletion tests pass; the two template tests still fail.

### Task 3: Replace destructive links with POST forms

**Files:**
- Modify: `白车轴草/blog/templates/drafts.html`
- Modify: `白车轴草/blog/templates/post_detail.html`

- [ ] **Step 1: Replace the draft deletion link**

Use:

```html
<form method="post" action="{% url 'delete_draft' post.id %}">
    {% csrf_token %}
    <button type="submit"
            class="btn btn-outline-danger text-nowrap"
            onclick="return confirm('确定要删除这篇草稿吗？此操作不可恢复。')">
        <i class="fas fa-trash-alt me-1"></i>删除
    </button>
</form>
```

- [ ] **Step 2: Replace the published article deletion link**

Use:

```html
<form method="post" action="{% url 'delete_post' post.id %}">
    {% csrf_token %}
    <button type="submit"
            class="btn btn-outline-danger btn-sm rounded-pill"
            onclick="return confirm('确定要永久删除这篇已发布的文章吗？此操作不可恢复。')">
        <i class="fas fa-trash-alt me-1"></i>删除文章
    </button>
</form>
```

- [ ] **Step 3: Run the focused tests and verify GREEN**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.PostDeletionTests
```

Expected: all six tests pass.

- [ ] **Step 4: Commit the implementation**

```powershell
git add .\白车轴草\blog\views.py
git add .\白车轴草\blog\templates\drafts.html
git add .\白车轴草\blog\templates\post_detail.html
git commit -m "Require POST for article deletion"
```

### Task 4: Verify the complete project

**Files:**
- Verify only.

- [ ] **Step 1: Run Django system checks**

```powershell
python .\白车轴草\manage.py check
```

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 2: Run migration consistency check**

```powershell
python .\白车轴草\manage.py makemigrations --check --dry-run
```

Expected: `No changes detected`.

- [ ] **Step 3: Run the complete blog test suite**

```powershell
python .\白车轴草\manage.py test blog
```

Expected: all tests pass. The known missing `staticfiles` directory warning is acceptable.

- [ ] **Step 4: Inspect the final diff and repository state**

```powershell
git diff --check
git status --short --branch
```

Expected: no whitespace errors and no unrelated changes.

- [ ] **Step 5: Push the verified commits**

```powershell
git push origin main
```

Expected: the local `main` commits are pushed to `origin/main`.
