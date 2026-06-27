# Archive and Tags Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add lightweight archive and tags browsing pages without changing the database schema.

**Architecture:** Reuse a shared readable-post queryset helper in `blog.views` so the homepage, archive page, and tags page enforce the same visibility rules. Build archive groups and tag counts in Python because `Post.tags` is currently a comma-separated string and the expected post count is small.

**Tech Stack:** Django 4.2 function views, Django templates, Bootstrap 5, existing Django `TestCase` suite.

---

## File Structure

- Modify `白车轴草/blog/tests.py`
  - Add regression tests for archive visibility, tag counting, tag links, and navigation links.
- Modify `白车轴草/blog/views.py`
  - Add `get_readable_published_posts(request_user)`.
  - Refactor `index()` to use the helper.
  - Add `build_archive_groups(posts)`.
  - Add `build_tag_counts(posts)`.
  - Add `archive_view(request)`.
  - Add `tags_view(request)`.
- Modify `白车轴草/白车轴草/urls.py`
  - Add `/archive/` and `/tags/` URL patterns.
- Modify `白车轴草/blog/templates/base.html`
  - Add navigation links to archive and tags pages.
- Modify `白车轴草/blog/templates/index.html`
  - Add matching navigation links because the homepage is a standalone template.
- Create `白车轴草/blog/templates/archive.html`
  - Render grouped archive data.
- Create `白车轴草/blog/templates/tags.html`
  - Render tag counts and links back to homepage search.

---

### Task 1: Add failing route and behavior tests

**Files:**
- Modify: `白车轴草/blog/tests.py`

- [ ] **Step 1: Add archive and tags tests near the existing index tests**

Insert these tests after `test_index_card_metadata_contains_clickable_filter_links`:

```python
    def test_archive_page_groups_readable_posts_by_month(self):
        author = User.objects.create_user(
            username='archive-author',
            password='StrongPass12345',
        )
        june_post = Post.objects.create(
            author=author,
            title='六月公开文章',
            category='life',
            content='六月正文',
            status='published',
            visibility='public',
        )
        may_post = Post.objects.create(
            author=author,
            title='五月公开文章',
            category='study',
            content='五月正文',
            status='published',
            visibility='public',
        )
        draft_post = Post.objects.create(
            author=author,
            title='草稿不进归档',
            category='tech',
            content='草稿正文',
            status='draft',
            visibility='private',
        )
        Post.objects.filter(pk=june_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 27, 9, 0)),
        )
        Post.objects.filter(pk=may_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 5, 20, 9, 0)),
        )
        Post.objects.filter(pk=draft_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 28, 9, 0)),
        )

        response = self.client.get(reverse('archive'))

        self.assertEqual(response.status_code, 200)
        archive_groups = response.context['archive_groups']
        self.assertEqual(len(archive_groups), 2)
        self.assertEqual(archive_groups[0]['year'], 2026)
        self.assertEqual(archive_groups[0]['month'], 6)
        self.assertEqual(archive_groups[0]['posts'][0].title, '六月公开文章')
        self.assertEqual(archive_groups[1]['month'], 5)
        self.assertContains(response, '六月公开文章')
        self.assertContains(response, '五月公开文章')
        self.assertNotContains(response, '草稿不进归档')
```

- [ ] **Step 2: Add logged-in private-post archive visibility test**

Insert this test after the previous archive test:

```python
    def test_archive_page_includes_current_users_private_published_posts(self):
        current_user = User.objects.create_user(
            username='archive-current',
            password='StrongPass12345',
        )
        other_user = User.objects.create_user(
            username='archive-other',
            password='StrongPass12345',
        )
        own_private_post = Post.objects.create(
            author=current_user,
            title='自己的私密已发布文章',
            category='life',
            content='自己可见',
            status='published',
            visibility='private',
        )
        other_private_post = Post.objects.create(
            author=other_user,
            title='别人的私密已发布文章',
            category='life',
            content='别人私密',
            status='published',
            visibility='private',
        )
        Post.objects.filter(pk=own_private_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 25, 9, 0)),
        )
        Post.objects.filter(pk=other_private_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 24, 9, 0)),
        )
        self.client.login(username='archive-current', password='StrongPass12345')

        response = self.client.get(reverse('archive'))

        archive_titles = [
            post.title
            for archive_group in response.context['archive_groups']
            for post in archive_group['posts']
        ]
        self.assertIn('自己的私密已发布文章', archive_titles)
        self.assertNotIn('别人的私密已发布文章', archive_titles)
        self.assertContains(response, '自己的私密已发布文章')
        self.assertNotContains(response, '别人的私密已发布文章')
```

- [ ] **Step 3: Add tag counting and link test**

Insert this test after the archive visibility tests:

```python
    def test_tags_page_counts_visible_tags_once_per_post(self):
        author = User.objects.create_user(
            username='tag-author',
            password='StrongPass12345',
        )
        Post.objects.create(
            author=author,
            title='标签文章一',
            category='life',
            tags='生活, Django, 生活,,',
            content='标签正文一',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='标签文章二',
            category='study',
            tags='Django, 学习',
            content='标签正文二',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='草稿标签不统计',
            category='tech',
            tags='隐藏',
            content='草稿正文',
            status='draft',
            visibility='private',
        )

        response = self.client.get(reverse('tags'))

        self.assertEqual(response.status_code, 200)
        tag_counts = response.context['tag_counts']
        self.assertEqual(tag_counts[0], {'name': 'Django', 'count': 2})
        self.assertIn({'name': '生活', 'count': 1}, tag_counts)
        self.assertIn({'name': '学习', 'count': 1}, tag_counts)
        self.assertNotIn({'name': '隐藏', 'count': 1}, tag_counts)
        self.assertContains(response, 'href="/index/?q=Django"')
        self.assertContains(response, '2 篇')
```

- [ ] **Step 4: Add navigation links test**

Insert this test after the tag test:

```python
    def test_base_navigation_links_to_archive_and_tags_pages(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, f'href="{reverse("archive")}"')
        self.assertContains(response, f'href="{reverse("tags")}"')
        self.assertContains(response, '归档')
        self.assertContains(response, '标签')
```

- [ ] **Step 5: Run the focused tests and verify they fail**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.AuthViewsTests.test_archive_page_groups_readable_posts_by_month blog.tests.AuthViewsTests.test_archive_page_includes_current_users_private_published_posts blog.tests.AuthViewsTests.test_tags_page_counts_visible_tags_once_per_post blog.tests.AuthViewsTests.test_base_navigation_links_to_archive_and_tags_pages
```

Expected result: tests fail because `archive` and `tags` routes do not exist yet, and navigation links are not present yet.

---

### Task 2: Add shared visibility helper and page data builders

**Files:**
- Modify: `白车轴草/blog/views.py`

- [ ] **Step 1: Add readable-post helper below `append_ai_cover_attribution`**

Add:

```python
def get_readable_published_posts(request_user):
    if request_user.is_authenticated:
        return Post.objects.filter(
            Q(status='published', visibility='public')
            | Q(author=request_user, status='published')
        ).distinct().order_by('-created_at')

    return Post.objects.filter(
        status='published',
        visibility='public',
    ).order_by('-created_at')
```

- [ ] **Step 2: Refactor `index()` to use the helper**

Replace the authenticated/anonymous `all_posts` assignment in `index()` with:

```python
    all_posts = get_readable_published_posts(request.user)
    if request.user.is_authenticated:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        about_posts = Post.objects.filter(
            author=request.user,
            status='published',
        )
    else:
        profile = owner_profile
        about_posts = Post.objects.filter(
            author=owner,
            status='published',
            visibility='public',
        ) if owner else Post.objects.none()
```

- [ ] **Step 3: Add archive and tag builders below `get_category_counts`**

Add:

```python
def build_archive_groups(posts):
    archive_groups = []
    group_lookup = {}

    for post in posts:
        local_created_at = timezone.localtime(post.created_at)
        group_key = (local_created_at.year, local_created_at.month)
        if group_key not in group_lookup:
            archive_group = {
                'year': local_created_at.year,
                'month': local_created_at.month,
                'label': f'{local_created_at.year} 年 {local_created_at.month} 月',
                'posts': [],
            }
            group_lookup[group_key] = archive_group
            archive_groups.append(archive_group)

        group_lookup[group_key]['posts'].append(post)

    return archive_groups


def build_tag_counts(posts):
    tag_counter = Counter()

    for post in posts:
        unique_tags = set(post.tag_list)
        for tag in unique_tags:
            tag_counter[tag] += 1

    return [
        {'name': tag_name, 'count': tag_count}
        for tag_name, tag_count in sorted(
            tag_counter.items(),
            key=lambda tag_item: (-tag_item[1], tag_item[0].lower()),
        )
    ]
```

- [ ] **Step 4: Add archive and tags views below `index()`**

Add:

```python
def archive_view(request):
    posts = get_readable_published_posts(request.user).select_related(
        'author',
        'author__profile',
    )
    archive_groups = build_archive_groups(posts)
    return render(request, 'archive.html', {
        'archive_groups': archive_groups,
    })


def tags_view(request):
    posts = get_readable_published_posts(request.user)
    tag_counts = build_tag_counts(posts)
    return render(request, 'tags.html', {
        'tag_counts': tag_counts,
    })
```

- [ ] **Step 5: Run the focused tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.AuthViewsTests.test_archive_page_groups_readable_posts_by_month blog.tests.AuthViewsTests.test_archive_page_includes_current_users_private_published_posts blog.tests.AuthViewsTests.test_tags_page_counts_visible_tags_once_per_post
```

Expected result: tests still fail because URLs and templates are not added yet.

---

### Task 3: Add routes, templates, and navigation links

**Files:**
- Modify: `白车轴草/白车轴草/urls.py`
- Modify: `白车轴草/blog/templates/base.html`
- Modify: `白车轴草/blog/templates/index.html`
- Create: `白车轴草/blog/templates/archive.html`
- Create: `白车轴草/blog/templates/tags.html`

- [ ] **Step 1: Add URL patterns**

In `urlpatterns`, add these two routes after the `index/` route:

```python
    path('archive/', views.archive_view, name='archive'),
    path('tags/', views.tags_view, name='tags'),
```

- [ ] **Step 2: Add base navigation links**

Inside `白车轴草/blog/templates/base.html`, add this list before the user-center `<ul class="navbar-nav ms-auto...">`:

```html
                <ul class="navbar-nav me-auto mb-2 mb-lg-0">
                    <li class="nav-item">
                        <a class="nav-link" href="{% url 'archive' %}">
                            <i class="far fa-calendar-alt me-1"></i>归档
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{% url 'tags' %}">
                            <i class="fas fa-tags me-1"></i>标签
                        </a>
                    </li>
                </ul>
```

- [ ] **Step 3: Add homepage navigation links**

Inside `白车轴草/blog/templates/index.html`, add the same list before the user-center `<ul class="navbar-nav ms-auto...">`:

```html
                <ul class="navbar-nav me-auto mb-2 mb-lg-0">
                    <li class="nav-item">
                        <a class="nav-link" href="{% url 'archive' %}">
                            <i class="far fa-calendar-alt me-1"></i>归档
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{% url 'tags' %}">
                            <i class="fas fa-tags me-1"></i>标签
                        </a>
                    </li>
                </ul>
```

- [ ] **Step 4: Create archive template**

Create `白车轴草/blog/templates/archive.html`:

```html
{% extends 'base.html' %}

{% block title %}文章归档 - 白车轴草{% endblock %}

{% block extra_css %}
<style>
    .archive-page {
        max-width: 980px;
        margin: 42px auto 64px;
        padding: 0 16px;
    }

    .archive-hero {
        background: linear-gradient(135deg, #f1f8e9, #ffffff);
        border: 1px solid #e0f0dd;
        border-radius: 24px;
        padding: 32px;
        margin-bottom: 28px;
        box-shadow: 0 16px 40px rgba(76, 175, 80, 0.08);
    }

    .archive-hero h1 {
        color: #2e7d32;
        font-weight: 700;
        margin-bottom: 8px;
    }

    .archive-group {
        background: #fff;
        border: 1px solid #eef4ec;
        border-radius: 18px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 12px 30px rgba(0, 0, 0, 0.04);
    }

    .archive-month {
        color: #2e7d32;
        font-weight: 700;
        margin-bottom: 16px;
    }

    .archive-post {
        display: flex;
        justify-content: space-between;
        gap: 18px;
        padding: 14px 0;
        border-top: 1px solid #f1f3f0;
    }

    .archive-post:first-of-type {
        border-top: 0;
    }

    .archive-post-title {
        color: #2d3a2d;
        font-weight: 600;
        text-decoration: none;
    }

    .archive-post-title:hover {
        color: #2e7d32;
    }

    .archive-meta {
        color: #7b8a7b;
        font-size: 0.9rem;
    }

    .archive-empty {
        background: #fff;
        border: 1px dashed #cfe5cc;
        border-radius: 18px;
        color: #7b8a7b;
        padding: 32px;
        text-align: center;
    }

    @media (max-width: 640px) {
        .archive-post {
            display: block;
        }
    }
</style>
{% endblock %}

{% block content %}
<main class="archive-page">
    <section class="archive-hero">
        <h1><i class="far fa-calendar-alt me-2"></i>文章归档</h1>
        <p class="mb-0 text-muted">按月份回看已经发布的文章。</p>
    </section>

    {% if archive_groups %}
        {% for archive_group in archive_groups %}
        <section class="archive-group">
            <h2 class="archive-month">{{ archive_group.label }}</h2>
            {% for post in archive_group.posts %}
            <article class="archive-post">
                <div>
                    <a class="archive-post-title" href="{% url 'post_detail' post.id %}">{{ post.title }}</a>
                    <div class="archive-meta mt-1">
                        <i class="fas fa-folder me-1"></i>{{ post.category_label }}
                        <span class="mx-2">·</span>
                        <i class="fas fa-user me-1"></i>{{ post.author.profile.display_name|default:post.author.username }}
                    </div>
                </div>
                <div class="archive-meta text-nowrap">
                    <i class="far fa-clock me-1"></i>{{ post.created_at|date:"Y-m-d" }}
                    <span class="ms-2"><i class="far fa-eye me-1"></i>{{ post.views_count }}</span>
                </div>
            </article>
            {% endfor %}
        </section>
        {% endfor %}
    {% else %}
    <div class="archive-empty">
        暂时还没有可以查看的已发布文章。
    </div>
    {% endif %}
</main>
{% endblock %}
```

- [ ] **Step 5: Create tags template**

Create `白车轴草/blog/templates/tags.html`:

```html
{% extends 'base.html' %}

{% block title %}文章标签 - 白车轴草{% endblock %}

{% block extra_css %}
<style>
    .tags-page {
        max-width: 980px;
        margin: 42px auto 64px;
        padding: 0 16px;
    }

    .tags-hero {
        background: linear-gradient(135deg, #f1f8e9, #ffffff);
        border: 1px solid #e0f0dd;
        border-radius: 24px;
        padding: 32px;
        margin-bottom: 28px;
        box-shadow: 0 16px 40px rgba(76, 175, 80, 0.08);
    }

    .tags-hero h1 {
        color: #2e7d32;
        font-weight: 700;
        margin-bottom: 8px;
    }

    .tag-cloud {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
    }

    .tag-pill {
        align-items: center;
        background: #fff;
        border: 1px solid #d8ecd5;
        border-radius: 999px;
        color: #2e7d32;
        display: inline-flex;
        gap: 8px;
        padding: 10px 16px;
        text-decoration: none;
        transition: all 0.2s ease;
    }

    .tag-pill:hover {
        background: #f1f8e9;
        color: #1b5e20;
        transform: translateY(-1px);
    }

    .tag-count {
        background: #e8f5e9;
        border-radius: 999px;
        color: #4c7d4f;
        font-size: 0.82rem;
        padding: 2px 8px;
    }

    .tags-empty {
        background: #fff;
        border: 1px dashed #cfe5cc;
        border-radius: 18px;
        color: #7b8a7b;
        padding: 32px;
        text-align: center;
    }
</style>
{% endblock %}

{% block content %}
<main class="tags-page">
    <section class="tags-hero">
        <h1><i class="fas fa-tags me-2"></i>文章标签</h1>
        <p class="mb-0 text-muted">点击标签后，会使用首页搜索展示相关文章。</p>
    </section>

    {% if tag_counts %}
    <section class="tag-cloud" aria-label="文章标签列表">
        {% for tag in tag_counts %}
        <a class="tag-pill" href="{% url 'index' %}?q={{ tag.name|urlencode }}">
            <span># {{ tag.name }}</span>
            <span class="tag-count">{{ tag.count }} 篇</span>
        </a>
        {% endfor %}
    </section>
    {% else %}
    <div class="tags-empty">
        暂时还没有可以展示的标签。
    </div>
    {% endif %}
</main>
{% endblock %}
```

- [ ] **Step 6: Run the focused tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.AuthViewsTests.test_archive_page_groups_readable_posts_by_month blog.tests.AuthViewsTests.test_archive_page_includes_current_users_private_published_posts blog.tests.AuthViewsTests.test_tags_page_counts_visible_tags_once_per_post blog.tests.AuthViewsTests.test_base_navigation_links_to_archive_and_tags_pages
```

Expected result: all four focused tests pass.

---

### Task 4: Full verification and documentation

**Files:**
- Modify: `AGENTS.MD`

- [ ] **Step 1: Update project documentation**

Update `AGENTS.MD` route and feature sections to include:

```markdown
- 文章归档页，按年月查看已发布文章。
- 文章标签页，按标签数量查看标签并跳转首页搜索。
```

Add routes:

```markdown
- `/archive/`：文章归档页。
- `/tags/`：文章标签页。
```

- [ ] **Step 2: Run Django checks**

Run:

```powershell
python .\白车轴草\manage.py check
```

Expected result: `System check identified no issues`.

- [ ] **Step 3: Run migration consistency check**

Run:

```powershell
python .\白车轴草\manage.py makemigrations --check --dry-run
```

Expected result: `No changes detected`.

- [ ] **Step 4: Run full blog tests**

Run:

```powershell
python .\白车轴草\manage.py test blog
```

Expected result: all blog tests pass.

- [ ] **Step 5: Review diff**

Run:

```powershell
git diff --check
git diff --stat
```

Expected result: no whitespace errors; diff only includes archive/tag feature files, tests, routes, and documentation.

- [ ] **Step 6: Commit and push**

Run:

```powershell
git add AGENTS.MD docs/superpowers/plans/2026-06-27-archive-tags.md 白车轴草/blog/tests.py 白车轴草/blog/views.py 白车轴草/白车轴草/urls.py 白车轴草/blog/templates/base.html 白车轴草/blog/templates/index.html 白车轴草/blog/templates/archive.html 白车轴草/blog/templates/tags.html
git commit -m "Add archive and tags pages"
git push origin main
```

Expected result: commit and push succeed.

