import json
import os
from datetime import datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from blog.models import Post, UserProfile


class AuthViewsTests(TestCase):
    def test_register_creates_and_logs_in_user(self):
        response = self.client.post(reverse('register'), {
            'username': 'newuser',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertRedirects(response, reverse('index'))
        self.assertTrue(User.objects.filter(username='newuser').exists())
        self.assertEqual(self.client.session['_auth_user_id'], str(User.objects.get(username='newuser').id))

    def test_login_accepts_existing_user(self):
        User.objects.create_user(username='writer', password='StrongPass12345')

        response = self.client.post(reverse('login'), {
            'username': 'writer',
            'password': 'StrongPass12345',
        })

        self.assertRedirects(response, reverse('index'))
        self.assertEqual(self.client.session['_auth_user_id'], str(User.objects.get(username='writer').id))

    def test_protected_create_post_redirects_to_login(self):
        response = self.client.get(reverse('create_post'))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('create_post')}")

    def test_create_post_belongs_to_current_user(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.post(reverse('create_post'), {
            'title': '我的文章',
            'category': 'life',
            'content': '只属于当前用户',
            'action': 'publish',
        })

        self.assertRedirects(response, reverse('index'))
        self.assertEqual(Post.objects.get(title='我的文章').author, user)

    def test_index_only_shows_current_users_posts(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        other = User.objects.create_user(username='other', password='StrongPass12345')
        Post.objects.create(author=owner, title='自己的文章', category='life', content='可见', status='published')
        Post.objects.create(author=other, title='别人的文章', category='life', content='不可见', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        self.assertContains(response, '自己的文章')
        self.assertNotContains(response, '别人的文章')

    def test_index_about_card_uses_current_user_profile_and_posts(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        other = User.objects.create_user(username='other', password='StrongPass12345')
        UserProfile.objects.create(user=owner, nickname='写作者', bio='记录自己的文章。')
        Post.objects.create(author=owner, title='当前账号文章', category='life', content='可见', status='published')
        Post.objects.create(author=owner, title='当前账号草稿', category='life', content='不可见', status='draft')
        Post.objects.create(author=other, title='其他账号文章', category='tech', content='不可见', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        self.assertContains(response, '写作者')
        self.assertContains(response, '记录自己的文章。')
        self.assertContains(response, '当前账号文章')
        self.assertNotContains(response, '当前账号草稿')
        self.assertNotContains(response, '其他账号文章')
        self.assertEqual(response.context['published_count'], 1)
        self.assertEqual(response.context['category_count'], 1)

    def test_index_search_filters_current_users_published_posts(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        other = User.objects.create_user(username='other', password='StrongPass12345')
        Post.objects.create(author=owner, title='雨后散步', category='life', content='今天适合散步', status='published')
        Post.objects.create(author=owner, title='Django 记录', category='tech', content='视图和模板', status='published')
        Post.objects.create(author=owner, title='雨声草稿', category='life', content='不可见', status='draft')
        Post.objects.create(author=other, title='雨后别人的文章', category='life', content='不可见', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'q': '雨后'})

        result_titles = [post.title for post in response.context['posts'].object_list]
        self.assertEqual(result_titles, ['雨后散步'])
        self.assertEqual(response.context['search_query'], '雨后')

    def test_index_search_matches_content_and_category_label(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        Post.objects.create(author=owner, title='普通标题', category='tech', content='包含松饼这个关键词', status='published')
        Post.objects.create(author=owner, title='分类命中文章', category='life', content='没有直接关键词', status='published')
        Post.objects.create(author=owner, title='不相关文章', category='reading', content='别的内容', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        content_response = self.client.get(reverse('index'), {'q': '松饼'})
        category_response = self.client.get(reverse('index'), {'q': '生活'})

        content_titles = [post.title for post in content_response.context['posts'].object_list]
        category_titles = [post.title for post in category_response.context['posts'].object_list]
        self.assertEqual(content_titles, ['普通标题'])
        self.assertEqual(category_titles, ['分类命中文章'])

    def test_index_search_can_combine_with_category_and_pagination(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        for index in range(7):
            Post.objects.create(
                author=owner,
                title=f'Django 生活 {index}',
                category='life',
                content='搜索分页',
                status='published',
            )
        Post.objects.create(author=owner, title='Django 技术', category='tech', content='搜索分页', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'q': 'Django', 'category': 'life'})

        result_titles = [post.title for post in response.context['posts'].object_list]
        self.assertEqual(len(result_titles), 6)
        self.assertTrue(all(title.startswith('Django 生活') for title in result_titles))
        self.assertContains(response, '?q=Django&amp;category=life&amp;page=2')
        self.assertContains(response, 'value="Django"')

    def test_index_date_search_filters_by_created_date(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        target_post = Post.objects.create(author=owner, title='六月文章', category='life', content='夏天', status='published')
        other_post = Post.objects.create(author=owner, title='五月文章', category='life', content='春天', status='published')
        Post.objects.filter(pk=target_post.pk).update(created_at=timezone.make_aware(datetime(2026, 6, 13, 12, 0)))
        Post.objects.filter(pk=other_post.pk).update(created_at=timezone.make_aware(datetime(2026, 5, 20, 12, 0)))
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'date': '2026-06-13'})

        result_titles = [post.title for post in response.context['posts'].object_list]
        self.assertEqual(result_titles, ['六月文章'])
        self.assertEqual(response.context['selected_date'], '2026-06-13')
        self.assertContains(response, 'value="2026-06-13"')
        self.assertContains(response, '正在筛选日期')

    def test_index_date_search_can_combine_with_keyword_and_category(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        matched = Post.objects.create(author=owner, title='Django 日期', category='tech', content='筛选', status='published')
        wrong_date = Post.objects.create(author=owner, title='Django 旧文', category='tech', content='筛选', status='published')
        wrong_category = Post.objects.create(author=owner, title='Django 生活', category='life', content='筛选', status='published')
        Post.objects.filter(pk=matched.pk).update(created_at=timezone.make_aware(datetime(2026, 6, 13, 12, 0)))
        Post.objects.filter(pk=wrong_date.pk).update(created_at=timezone.make_aware(datetime(2026, 6, 12, 12, 0)))
        Post.objects.filter(pk=wrong_category.pk).update(created_at=timezone.make_aware(datetime(2026, 6, 13, 13, 0)))
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'q': 'Django', 'date': '2026-06-13', 'category': 'tech'})

        result_titles = [post.title for post in response.context['posts'].object_list]
        self.assertEqual(result_titles, ['Django 日期'])
        self.assertEqual(response.context['pagination_prefix'], 'q=Django&date=2026-06-13&category=tech&')
        self.assertContains(response, 'value="2026-06-13"')

    def test_detail_requires_post_owner(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        other = User.objects.create_user(username='other', password='StrongPass12345')
        post = Post.objects.create(author=other, title='别人的文章', category='life', content='不可见', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('post_detail', args=[post.id]))

        self.assertEqual(response.status_code, 404)

    def test_logout_clears_session(self):
        User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.get(reverse('logout'))

        self.assertRedirects(response, reverse('index'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_user_center_requires_login(self):
        response = self.client.get(reverse('user_center'))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('user_center')}")

    def test_user_center_creates_profile(self):
        User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.get(reverse('user_center'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(UserProfile.objects.filter(user__username='writer').exists())

    def test_user_center_updates_profile_and_email(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.post(reverse('user_center'), {
            'nickname': '写作者',
            'bio': '记录一点生活和技术。',
            'email': 'writer@example.com',
        })

        user.refresh_from_db()
        profile = UserProfile.objects.get(user=user)
        self.assertRedirects(response, reverse('user_center'))
        self.assertEqual(user.email, 'writer@example.com')
        self.assertEqual(profile.nickname, '写作者')
        self.assertEqual(profile.bio, '记录一点生活和技术。')

    def test_footer_social_links_use_root_settings_for_everyone(self):
        root = User.objects.create_superuser(
            username='root',
            password='StrongPass12345',
            email='root@example.com',
        )
        UserProfile.objects.create(
            user=root,
            github_url='https://github.com/root',
            weibo_url='https://weibo.com/root',
        )
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
            email='writer@example.com',
        )
        UserProfile.objects.create(
            user=user,
            github_url='https://github.com/writer',
            weibo_url='https://weibo.com/writer',
        )
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        self.assertContains(response, 'href="https://github.com/root"')
        self.assertContains(response, 'href="https://weibo.com/root"')
        self.assertContains(response, 'href="mailto:root@example.com"')
        self.assertContains(response, f'href="{reverse("rss_feed")}"')
        self.assertNotContains(response, 'https://github.com/writer')
        self.assertNotContains(response, 'https://weibo.com/writer')
        self.assertNotContains(response, 'mailto:writer@example.com')

    def test_only_root_gets_footer_setup_links_when_root_settings_are_missing(self):
        User.objects.create_superuser(username='root', password='StrongPass12345')
        User.objects.create_user(username='writer', password='StrongPass12345')

        self.client.login(username='writer', password='StrongPass12345')
        response = self.client.get(reverse('index'))
        self.assertContains(response, 'title="GitHub（root 未配置）"')
        self.assertContains(response, 'aria-disabled="true"')

        self.client.logout()
        self.client.login(username='root', password='StrongPass12345')
        response = self.client.get(reverse('index'))
        self.assertContains(response, f'href="{reverse("user_center")}" title="GitHub（未配置）"')

    def test_rss_feed_route_returns_xml(self):
        root = User.objects.create_superuser(username='root', password='StrongPass12345')
        writer = User.objects.create_user(username='writer', password='StrongPass12345')
        Post.objects.create(author=root, title='root 的文章', category='life', content='可见', status='published')
        Post.objects.create(author=writer, title='writer 的文章', category='life', content='不可见', status='published')

        response = self.client.get(reverse('rss_feed'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/rss+xml; charset=utf-8')
        self.assertContains(response, '<rss version="2.0">')
        self.assertContains(response, 'root 的文章')
        self.assertNotContains(response, 'writer 的文章')


class StartupPostCommandTests(TestCase):
    def openai_response(self):
        response_body = {
            'output': [
                {
                    'type': 'message',
                    'content': [
                        {
                            'type': 'output_text',
                            'text': (
                                '{"title": "给早晨留出十分钟的整理时间", '
                                '"category": "life", '
                                '"tags": ["生活技巧", "整理"], '
                                '"content": "早晨的状态往往会影响一整天。可以把起床后的前十分钟留给简单整理：先喝一杯温水，再把桌面上明显不用的物品放回原位，最后写下今天最重要的一件事。这个过程不需要追求完美，重点是让自己从混乱里慢慢进入节奏。整理空间的同时，也是在整理注意力。坚持几天后，你会发现开始工作或学习时，犹豫和拖延会少一点。"}'
                            ),
                        }
                    ],
                }
            ]
        }
        return FakeOpenAIResponse(response_body)

    def test_create_startup_post_creates_one_published_daily_article_for_user(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        command_output = StringIO()
        current_date = timezone.localdate()

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            with patch('blog.management.commands.create_startup_post.urlopen', return_value=self.openai_response()):
                call_command('create_startup_post', stdout=command_output)

        post = Post.objects.get(author=author)
        self.assertEqual(post.status, 'published')
        self.assertEqual(post.category, 'life')
        self.assertIn('自动发布', post.tags)
        self.assertIn('生活技巧', post.tags)
        self.assertIn(f'daily:{current_date.isoformat()}', post.tags)
        self.assertIn(current_date.strftime('%Y-%m-%d'), post.title)
        self.assertIn('Created daily article', command_output.getvalue())

    def test_create_startup_post_can_create_draft_when_requested(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            with patch('blog.management.commands.create_startup_post.urlopen', return_value=self.openai_response()):
                call_command('create_startup_post', draft=True)

        post = Post.objects.get(author=author)
        self.assertEqual(post.status, 'draft')

    def test_create_startup_post_skips_duplicate_for_same_day(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        command_output = StringIO()

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            with patch('blog.management.commands.create_startup_post.urlopen', return_value=self.openai_response()):
                call_command('create_startup_post', stdout=command_output)
                call_command('create_startup_post', stdout=command_output)

        self.assertEqual(Post.objects.filter(author=author).count(), 1)
        self.assertIn('Daily article already exists', command_output.getvalue())

    def test_create_startup_post_requires_openai_api_key(self):
        User.objects.create_user(username='白车轴草', password='StrongPass12345')

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CommandError):
                call_command('create_startup_post')

    def test_create_startup_post_requires_existing_user(self):
        with self.assertRaises(CommandError):
            call_command('create_startup_post', username='missing-user')


class FakeOpenAIResponse:
    def __init__(self, response_body):
        self.response_body = response_body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.response_body).encode('utf-8')
