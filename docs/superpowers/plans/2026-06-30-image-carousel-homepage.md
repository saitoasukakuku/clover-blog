# Image Carousel Homepage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real `/` homepage with an immersive `media/index_img` background carousel while keeping `/index/` as the existing article list and filter page.

**Architecture:** Add a new `home` view that prepares carousel slide data from `MEDIA_ROOT/index_img`, recent readable posts, and lightweight exploration data. Keep `/index/` and the existing `index` view intact. Render the new experience in a dedicated `home.html` template with local CSS and defensive JavaScript.

**Tech Stack:** Django 4.2 function views, Django templates, SQLite/MySQL-compatible ORM queries, Bootstrap-compatible base navigation, vanilla JavaScript, local media files.

---

## File Structure

- Modify `白车轴草/白车轴草/urls.py`: map `/` to `views.home` instead of redirecting to `index`.
- Modify `白车轴草/blog/views.py`: add homepage carousel helpers and the new `home` view near the existing `index` view.
- Modify `白车轴草/blog/templates/base.html`: make the brand link point to `home` and add a clear `阅读` link to `index`.
- Create `白车轴草/blog/templates/home.html`: render the immersive carousel homepage.
- Modify `白车轴草/blog/tests.py`: add focused tests in `HomepageTemplateIntegrationTests`.

No model or migration is required.

---

### Task 1: Add Failing Homepage Route and Visibility Tests

**Files:**
- Modify: `白车轴草/blog/tests.py`

- [ ] **Step 1: Add tests for the new `/` homepage behavior**

Add these test methods to `HomepageTemplateIntegrationTests` after `test_index_uses_shared_navigation_and_still_renders_search_and_posts`.

```python
    def test_home_uses_home_template_and_keeps_index_as_article_list(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home.html')
        self.assertTemplateUsed(response, 'base.html')
        self.assertContains(response, '开始阅读')
        self.assertContains(response, reverse('index'))

        index_response = self.client.get(reverse('index'))

        self.assertEqual(index_response.status_code, 200)
        self.assertTemplateUsed(index_response, 'index.html')
        self.assertContains(index_response, '搜索文章')

    def test_home_recent_posts_use_public_visibility_for_anonymous_users(self):
        author = User.objects.create_user(username='public-home-author', password='StrongPass12345')
        public_post = Post.objects.create(
            author=author,
            title='Public homepage post',
            category='life',
            content='Public content',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='Private homepage post',
            category='life',
            content='Private content',
            status='published',
            visibility='private',
        )
        Post.objects.create(
            author=author,
            title='Draft homepage post',
            category='life',
            content='Draft content',
            status='draft',
            visibility='public',
        )

        response = self.client.get(reverse('home'))

        self.assertEqual(list(response.context['recent_posts']), [public_post])
        self.assertContains(response, 'Public homepage post')
        self.assertNotContains(response, 'Private homepage post')
        self.assertNotContains(response, 'Draft homepage post')

    def test_home_recent_posts_include_logged_in_users_private_published_posts(self):
        current_user = User.objects.create_user(username='home-current', password='StrongPass12345')
        other_user = User.objects.create_user(username='home-other', password='StrongPass12345')
        own_private_post = Post.objects.create(
            author=current_user,
            title='Own private homepage post',
            category='life',
            content='Own private content',
            status='published',
            visibility='private',
        )
        Post.objects.create(
            author=other_user,
            title='Other private homepage post',
            category='life',
            content='Other private content',
            status='published',
            visibility='private',
        )
        self.client.login(username='home-current', password='StrongPass12345')

        response = self.client.get(reverse('home'))

        self.assertIn(own_private_post, list(response.context['recent_posts']))
        self.assertContains(response, 'Own private homepage post')
        self.assertNotContains(response, 'Other private homepage post')
```

- [ ] **Step 2: Run tests and verify they fail for the expected reason**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.HomepageTemplateIntegrationTests
```

Expected result:

- `test_home_uses_home_template_and_keeps_index_as_article_list` fails because `reverse('home')` still redirects to `/index/` and does not use `home.html`.
- The visibility tests fail because the new homepage context does not exist yet.

- [ ] **Step 3: Commit the failing tests**

Run:

```powershell
git add 白车轴草/blog/tests.py
git commit -m "test: cover image carousel homepage"
```

---

### Task 2: Add Carousel Slide Helpers and Homepage View

**Files:**
- Modify: `白车轴草/blog/views.py`

- [ ] **Step 1: Add imports**

Change the existing URL import in `views.py`:

```python
from urllib.parse import quote, urlparse
```

Add this Django settings import near the other Django imports:

```python
from django.conf import settings
```

- [ ] **Step 2: Add homepage constants after the image upload constants**

Place this block after `ALLOWED_IMAGE_EXTENSIONS`.

```python
HOMEPAGE_IMAGE_DIR_NAME = 'index_img'
HOMEPAGE_ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
HOMEPAGE_MAX_CAROUSEL_SLIDES = 12
HOMEPAGE_THEME_PRESETS = [
    {
        'accent': '#5f8fc8',
        'accent_strong': '#2c5f96',
        'accent_soft': 'rgba(95, 143, 200, .18)',
        'kicker': '晨湖 · 清透',
        'headline': '让每次进入网站，都像翻开一张新的明信片。',
        'lead': '首页背景从图片库轮播，文字、按钮和卡片颜色跟着图片气质变化。读者先感受到这一刻的氛围，再进入文章、归档或标签。',
        'card_title': '湖边醒来的阅读时间',
        'card_text': '蓝色和雪山适合清透的第一屏，按钮和标签会切换成偏冷的蓝色。',
        'moods': ['清晨', '远山', '慢生活'],
    },
    {
        'accent': '#c8893e',
        'accent_strong': '#8a5726',
        'accent_soft': 'rgba(200, 137, 62, .20)',
        'kicker': '秋屋 · 温暖',
        'headline': '先坐下来，再慢慢读几篇文章。',
        'lead': '当背景切到木屋和秋色，首页文案可以更像邀请。整体从清透变成温暖，适合生活记录和博主介绍。',
        'card_title': '木屋旁边的慢阅读',
        'card_text': '暖色背景适合把“认识博主”和“最近文章”做得更有亲近感。',
        'moods': ['秋天', '木屋', '随笔'],
    },
    {
        'accent': '#d06a54',
        'accent_strong': '#9a3e31',
        'accent_soft': 'rgba(208, 106, 84, .20)',
        'kicker': '夕照 · 强烈',
        'headline': '把一天的尾声，写进新的开场。',
        'lead': '夕阳和红叶更有戏剧感，适合让标题更短、更有画面。首页可以不是固定气质，而是随图库变化。',
        'card_title': '夕色里的首页入口',
        'card_text': '强色图片需要更厚的遮罩，文字保持清楚，按钮使用更深的主题色。',
        'moods': ['夕阳', '红叶', '故事'],
    },
    {
        'accent': '#8aa0b8',
        'accent_strong': '#4f657c',
        'accent_soft': 'rgba(138, 160, 184, .22)',
        'kicker': '雪林 · 安静',
        'headline': '安静一点，也能让网站更有记忆。',
        'lead': '雪景时减少装饰和饱和度，首页会变得更平静。适合展示归档、标签和长期阅读入口。',
        'card_title': '雪地里的安静入口',
        'card_text': '冷色低饱和背景适合突出文章和归档，不需要太多动效。',
        'moods': ['雪景', '安静', '归档'],
    },
]
```

- [ ] **Step 3: Add helper functions before `index`**

Add this code after `get_related_posts`.

```python
def get_homepage_image_file_names():
    image_directory = os.path.join(settings.MEDIA_ROOT, HOMEPAGE_IMAGE_DIR_NAME)
    try:
        image_file_names = sorted(os.listdir(image_directory), key=str.lower)
    except OSError:
        return []

    allowed_file_names = []
    for image_file_name in image_file_names:
        image_file_path = os.path.join(image_directory, image_file_name)
        _, image_extension = os.path.splitext(image_file_name)
        if image_extension.lower() not in HOMEPAGE_ALLOWED_IMAGE_EXTENSIONS:
            continue
        if not os.path.isfile(image_file_path):
            continue
        allowed_file_names.append(image_file_name)
        if len(allowed_file_names) >= HOMEPAGE_MAX_CAROUSEL_SLIDES:
            break
    return allowed_file_names


def build_homepage_carousel_slides():
    carousel_slides = []
    image_file_names = get_homepage_image_file_names()
    media_url_prefix = f"{settings.MEDIA_URL.rstrip('/')}/{HOMEPAGE_IMAGE_DIR_NAME}"

    for image_index, image_file_name in enumerate(image_file_names):
        theme_preset = HOMEPAGE_THEME_PRESETS[image_index % len(HOMEPAGE_THEME_PRESETS)]
        carousel_slides.append({
            'image_url': f"{media_url_prefix}/{quote(image_file_name)}",
            'file_name': image_file_name,
            **theme_preset,
        })
    return carousel_slides
```

- [ ] **Step 4: Add the `home` view before `index`**

Add this view after `build_homepage_carousel_slides`.

```python
def home(request):
    owner, owner_profile = get_site_owner_profile()
    readable_posts = get_readable_published_posts(request.user).select_related(
        'author',
        'author__profile',
    )
    recent_posts = list(readable_posts[:3])
    for recent_post in recent_posts:
        recent_post.card_display_tags = get_display_tags(recent_post)[:3]

    if request.user.is_authenticated:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
    else:
        profile = owner_profile

    featured_post = recent_posts[0] if recent_posts else None
    carousel_slides = build_homepage_carousel_slides()
    return render(request, 'home.html', {
        'carousel_slides': carousel_slides,
        'recent_posts': recent_posts,
        'featured_post': featured_post,
        'profile': profile,
        'owner': owner,
    })
```

- [ ] **Step 5: Run tests and verify route tests still fail because URL and template are not wired**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.HomepageTemplateIntegrationTests
```

Expected result:

- The helper code imports successfully.
- Tests that expect `home.html` still fail until routes and template are added.

- [ ] **Step 6: Commit the view helpers**

Run:

```powershell
git add 白车轴草/blog/views.py
git commit -m "feat: prepare homepage carousel data"
```

---

### Task 3: Wire the Root Route and Shared Navigation

**Files:**
- Modify: `白车轴草/白车轴草/urls.py`
- Modify: `白车轴草/blog/templates/base.html`

- [ ] **Step 1: Change the root route**

In `urls.py`, remove:

```python
from django.views.generic import RedirectView
```

Change:

```python
path('', RedirectView.as_view(pattern_name='index', permanent=False), name='home'),
```

to:

```python
path('', views.home, name='home'),
```

- [ ] **Step 2: Update the brand and add the reading link**

In `base.html`, change the brand link:

```django
<a class="navbar-brand d-flex align-items-center" href="{% url 'home' %}">
```

Inside the `quick-nav` list, add this item before `归档`:

```django
<li class="nav-item">
    <a class="nav-link quick-nav-link" href="{% url 'index' %}">
        <i class="fas fa-book-open me-1"></i>阅读
    </a>
</li>
```

- [ ] **Step 3: Run tests and verify the missing template is the remaining failure**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.HomepageTemplateIntegrationTests
```

Expected result:

- Routing resolves `home`.
- Tests still fail until `home.html` exists.

- [ ] **Step 4: Commit route and navigation changes**

Run:

```powershell
git add 白车轴草/白车轴草/urls.py 白车轴草/blog/templates/base.html
git commit -m "feat: route root to homepage"
```

---

### Task 4: Create the Image Carousel Homepage Template

**Files:**
- Create: `白车轴草/blog/templates/home.html`

- [ ] **Step 1: Create `home.html` with the approved structure**

Create a template that extends `base.html`, uses `{{ carousel_slides|json_script:"homepage-carousel-slides" }}`, and renders:

- a full-height hero section;
- brand-aware text and action buttons;
- manual carousel dots;
- a feature card using the active background image;
- recent post cards;
- exploration shortcuts to `/index/`, `/archive/`, and `/tags/`;
- fallback CSS gradient when `carousel_slides` is empty.

Use these template variables:

```django
{% extends 'base.html' %}
{% load static %}

{% block title %}白车轴草{% endblock %}

{% block content %}
{{ carousel_slides|json_script:"homepage-carousel-slides" }}
<section class="immersive-homepage {% if not carousel_slides %}no-carousel-images{% endif %}">
    ...
</section>
{% endblock %}
```

The JavaScript must read slide data like this:

```javascript
const slidesElement = document.getElementById('homepage-carousel-slides');
const carouselSlides = slidesElement ? JSON.parse(slidesElement.textContent) : [];
let currentSlideIndex = 0;

function applyCarouselSlide(slide) {
    document.documentElement.style.setProperty('--home-accent', slide.accent);
    document.documentElement.style.setProperty('--home-accent-strong', slide.accent_strong);
    document.documentElement.style.setProperty('--home-accent-soft', slide.accent_soft);
    heroBackground.style.backgroundImage = `url('${slide.image_url}')`;
    featurePreview.style.backgroundImage = `url('${slide.image_url}')`;
    kicker.textContent = slide.kicker;
    headline.textContent = slide.headline;
    lead.textContent = slide.lead;
    cardTitle.textContent = slide.card_title;
    cardText.textContent = slide.card_text;
    moodChips.innerHTML = '';
    slide.moods.forEach((mood) => {
        const moodChip = document.createElement('span');
        moodChip.className = 'home-mood-chip';
        moodChip.textContent = mood;
        moodChips.appendChild(moodChip);
    });
}
```

The script must return early when `carouselSlides.length === 0`.

- [ ] **Step 2: Run the homepage integration tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.HomepageTemplateIntegrationTests
```

Expected result:

- The new home tests pass.
- Existing index integration tests still pass.

- [ ] **Step 3: Commit the template**

Run:

```powershell
git add 白车轴草/blog/templates/home.html
git commit -m "feat: add image carousel homepage template"
```

---

### Task 5: Add Carousel Image Discovery Tests

**Files:**
- Modify: `白车轴草/blog/tests.py`

- [ ] **Step 1: Add tests for `MEDIA_ROOT/index_img` behavior**

Add these methods to `HomepageTemplateIntegrationTests`.

```python
    def test_home_carousel_uses_allowed_images_from_media_index_img(self):
        with tempfile.TemporaryDirectory() as temporary_media_root:
            image_directory = os.path.join(temporary_media_root, 'index_img')
            os.makedirs(image_directory)
            with open(os.path.join(image_directory, 'first image.jpg'), 'wb') as image_file:
                image_file.write(b'fake jpg')
            with open(os.path.join(image_directory, 'second.png'), 'wb') as image_file:
                image_file.write(b'fake png')
            with open(os.path.join(image_directory, 'notes.txt'), 'wb') as text_file:
                text_file.write(b'not an image')

            with self.settings(MEDIA_ROOT=temporary_media_root, MEDIA_URL='/media/'):
                response = self.client.get(reverse('home'))

        carousel_slides = response.context['carousel_slides']
        self.assertEqual(len(carousel_slides), 2)
        self.assertEqual(carousel_slides[0]['file_name'], 'first image.jpg')
        self.assertEqual(carousel_slides[0]['image_url'], '/media/index_img/first%20image.jpg')
        self.assertEqual(carousel_slides[1]['file_name'], 'second.png')
        self.assertNotContains(response, 'notes.txt')

    def test_home_carousel_handles_missing_media_index_img(self):
        with tempfile.TemporaryDirectory() as temporary_media_root:
            with self.settings(MEDIA_ROOT=temporary_media_root, MEDIA_URL='/media/'):
                response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['carousel_slides'], [])
        self.assertContains(response, 'no-carousel-images')
```

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.HomepageTemplateIntegrationTests
```

Expected result:

- All homepage integration tests pass.

- [ ] **Step 3: Commit image discovery tests**

Run:

```powershell
git add 白车轴草/blog/tests.py
git commit -m "test: cover homepage carousel images"
```

---

### Task 6: Full Verification and Visual Check

**Files:**
- No new files required.

- [ ] **Step 1: Run Django checks**

Run:

```powershell
python .\白车轴草\manage.py check
```

Expected result:

```text
System check identified no issues
```

- [ ] **Step 2: Run the full blog test suite**

Run:

```powershell
python .\白车轴草\manage.py test blog
```

Expected result:

```text
OK
```

- [ ] **Step 3: Start the Django development server**

Run:

```powershell
python .\白车轴草\manage.py runserver 127.0.0.1:8000
```

Expected result:

```text
Starting development server at http://127.0.0.1:8000/
```

- [ ] **Step 4: Verify in the in-app browser**

Open:

```text
http://127.0.0.1:8000/
```

Check:

- the root page is the new image carousel homepage;
- the carousel loads images from `/media/index_img/`;
- the manual dots switch image, copy, mood chips, and colors;
- the `阅读` and `开始阅读` links go to `/index/`;
- `/index/` still shows the old article list page;
- the page has no horizontal overflow on desktop or mobile viewport.

- [ ] **Step 5: Commit final fixes if verification finds issues**

If verification requires small fixes, commit only the files touched for those fixes:

```powershell
git add 白车轴草/blog/templates/home.html 白车轴草/blog/views.py 白车轴草/blog/templates/base.html 白车轴草/白车轴草/urls.py 白车轴草/blog/tests.py
git commit -m "fix: polish image carousel homepage"
```

---

## Self-Review

- Spec coverage: The plan covers `/` route ownership, `/index/` preservation, `MEDIA_ROOT/index_img` image discovery, fallback behavior, theme presets, recent post visibility, navigation changes, template creation, and verification.
- Completion scan: The plan contains no unresolved markers or scope decisions.
- Type consistency: The plan consistently uses `carousel_slides`, `image_url`, `accent_strong`, `card_title`, `recent_posts`, and `home`.
