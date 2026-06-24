import json
import os
import tempfile
from datetime import datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import signing
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.db.models.fields.files import FieldFile
from blog.management.commands.create_startup_post import Command
from blog.models import Comment, Post, UserProfile
from blog.views import AI_COVER_TOKEN_SALT


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

    def test_register_saves_email_and_nickname(self):
        response = self.client.post(reverse('register'), {
            'username': 'newuser',
            'email': 'newuser@example.com',
            'nickname': '小草',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertRedirects(response, reverse('index'))
        user = User.objects.get(username='newuser')
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(user.email, 'newuser@example.com')
        self.assertEqual(profile.nickname, '小草')

    def test_register_rejects_duplicate_email(self):
        User.objects.create_user(
            username='existing',
            email='used@example.com',
            password='StrongPass12345',
        )

        response = self.client.post(reverse('register'), {
            'username': 'newuser',
            'email': 'used@example.com',
            'nickname': '小草',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())
        self.assertContains(response, '这个邮箱已经被注册。')

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
            'tags': '生活,记录',
            'content': '只属于当前用户',
            'action': 'publish',
        })

        self.assertRedirects(response, reverse('index'))
        post = Post.objects.get(title='我的文章')
        self.assertEqual(post.author, user)
        self.assertEqual(post.tags, '生活,记录')

    def test_generate_ai_post_requires_login(self):
        response = self.client.post(reverse('generate_ai_post'), {
            'topic': '学习 Django',
            'article_length': 'medium',
        })

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('generate_ai_post')}",
        )

    def test_generate_ai_post_returns_editable_draft_without_saving_post(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        Post.objects.create(
            author=user,
            title='以前的文章',
            category='life',
            content='以前的正文',
            status='published',
        )
        self.client.login(username='writer', password='StrongPass12345')
        generated_article = {
            'title': 'Django 学习记录',
            'category': 'study',
            'tags': ['Django', '学习笔记'],
            'content': '这是 AI 生成后供用户继续修改的正文。',
        }

        with patch.dict(os.environ, {'DEEPSEEK_MODEL': 'test-model'}):
            with patch(
                'blog.views.StartupPostCommand.generate_custom_article',
                return_value=generated_article,
            ) as generate_custom_article:
                response = self.client.post(reverse('generate_ai_post'), {
                    'topic': '学习 Django',
                    'requirements': '语气自然',
                    'article_length': 'medium',
                })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'title': 'Django 学习记录',
            'category': 'study',
            'tags': 'Django,学习笔记',
            'content': '这是 AI 生成后供用户继续修改的正文。',
            'cover': None,
            'cover_warning': '',
        })
        self.assertEqual(Post.objects.filter(author=user).count(), 1)
        generate_custom_article.assert_called_once_with(
            model='test-model',
            topic='学习 Django',
            requirements='语气自然',
            article_length='medium',
            recent_titles=['以前的文章'],
        )

    def test_generate_ai_post_limits_repeated_requests(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')
        generated_article = {
            'title': '测试标题',
            'category': 'life',
            'tags': ['测试', '生活'],
            'content': '测试正文',
        }

        with patch(
            'blog.views.StartupPostCommand.generate_custom_article',
            return_value=generated_article,
        ):
            first_response = self.client.post(reverse('generate_ai_post'), {
                'topic': '第一次生成',
                'article_length': 'short',
            })
            second_response = self.client.post(reverse('generate_ai_post'), {
                'topic': '立即再次生成',
                'article_length': 'short',
            })

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 429)
        self.assertIn('请等待', second_response.json()['error'])

    def test_generate_ai_post_can_return_signed_pexels_cover(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')
        generated_article = {
            'title': '雨天阅读',
            'category': 'reading',
            'tags': ['阅读', '雨天'],
            'content': '适合雨天阅读的一篇文章。',
        }
        pexels_photo = {
            'id': 12345,
            'url': 'https://www.pexels.com/photo/books-12345/',
            'photographer': 'Test Photographer',
            'photographer_url': 'https://www.pexels.com/@test',
            'src': {
                'landscape': 'https://images.pexels.com/photos/12345/books.jpg',
            },
        }

        with patch.dict(os.environ, {'PEXELS_API_KEY': 'test-key'}):
            with patch(
                'blog.views.StartupPostCommand.generate_custom_article',
                return_value=generated_article,
            ):
                with patch(
                    'blog.views.StartupPostCommand.search_pexels_photo',
                    return_value=pexels_photo,
                ):
                    response = self.client.post(reverse('generate_ai_post'), {
                        'topic': '雨天阅读',
                        'article_length': 'short',
                        'generate_cover': 'true',
                    })

        self.assertEqual(response.status_code, 200)
        response_cover = response.json()['cover']
        self.assertEqual(
            response_cover['preview_url'],
            'https://images.pexels.com/photos/12345/books.jpg',
        )
        signed_cover_data = signing.loads(
            response_cover['token'],
            salt=AI_COVER_TOKEN_SALT,
        )
        self.assertEqual(signed_cover_data['photo_id'], 12345)
        self.assertEqual(signed_cover_data['photographer'], 'Test Photographer')

    def test_create_post_downloads_signed_ai_cover(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')
        cover_data = {
            'image_url': 'https://images.pexels.com/photos/12345/books.jpg',
            'photo_id': 12345,
            'photo_url': 'https://www.pexels.com/photo/books-12345/',
            'photographer': 'Test Photographer',
            'photographer_url': 'https://www.pexels.com/@test',
        }
        ai_cover_token = signing.dumps(cover_data, salt=AI_COVER_TOKEN_SALT)

        with tempfile.TemporaryDirectory() as temporary_media_root:
            with self.settings(MEDIA_ROOT=temporary_media_root):
                with patch(
                    'blog.views.StartupPostCommand.download_pexels_image',
                    return_value=b'test-image-bytes',
                ) as download_pexels_image:
                    response = self.client.post(reverse('create_post'), {
                        'title': '带 AI 封面的文章',
                        'category': 'reading',
                        'tags': '阅读,雨天',
                        'content': '文章正文',
                        'visibility': 'private',
                        'action': 'draft',
                        'ai_cover_token': ai_cover_token,
                    })

        self.assertRedirects(response, reverse('drafts'))
        post = Post.objects.get(title='带 AI 封面的文章')
        self.assertTrue(post.cover.name.startswith('covers/ai_'))
        self.assertIn('Photo by Test Photographer on Pexels', post.content)
        download_pexels_image.assert_called_once_with(cover_data['image_url'])

    def test_index_only_shows_current_users_posts(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        other = User.objects.create_user(username='other', password='StrongPass12345')
        Post.objects.create(author=owner, title='自己的文章', category='life', content='可见', status='published')
        Post.objects.create(author=other, title='别人的文章', category='life', content='不可见', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        self.assertContains(response, '自己的文章')
        self.assertNotContains(response, '别人的文章')

    def test_index_my_posts_filter_only_shows_current_users_published_posts(self):
        current_user = User.objects.create_user(
            username='current',
            password='StrongPass12345',
        )
        other_user = User.objects.create_user(
            username='other',
            password='StrongPass12345',
        )
        Post.objects.create(
            author=current_user,
            title='我的公开文章',
            category='life',
            content='公开正文',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=current_user,
            title='我的私密文章',
            category='study',
            content='私密正文',
            status='published',
            visibility='private',
        )
        Post.objects.create(
            author=other_user,
            title='其他用户公开文章',
            category='tech',
            content='其他正文',
            status='published',
            visibility='public',
        )
        self.client.login(username='current', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'author': 'current'})

        result_titles = [
            post.title
            for post in response.context['posts'].object_list
        ]
        self.assertCountEqual(result_titles, ['我的公开文章', '我的私密文章'])
        self.assertTrue(response.context['is_my_posts_filter'])
        self.assertEqual(response.context['selected_author_label'], 'current')
        self.assertContains(response, '正在看我的文章')
        self.assertNotContains(response, '其他用户公开文章')

    def test_index_author_filter_only_shows_selected_authors_visible_posts(self):
        current_user = User.objects.create_user(
            username='current',
            password='StrongPass12345',
        )
        other_user = User.objects.create_user(
            username='other',
            password='StrongPass12345',
        )
        UserProfile.objects.create(user=other_user, nickname='其他作者')
        Post.objects.create(
            author=current_user,
            title='当前用户文章',
            category='life',
            content='当前正文',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=other_user,
            title='其他作者公开文章',
            category='tech',
            content='公开正文',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=other_user,
            title='其他作者私密文章',
            category='study',
            content='私密正文',
            status='published',
            visibility='private',
        )
        self.client.login(username='current', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'author': 'other'})

        result_titles = [
            post.title
            for post in response.context['posts'].object_list
        ]
        self.assertEqual(result_titles, ['其他作者公开文章'])
        self.assertFalse(response.context['is_my_posts_filter'])
        self.assertEqual(response.context['selected_author_label'], '其他作者')
        self.assertContains(response, '正在筛选作者')
        self.assertNotContains(response, '其他作者私密文章')

    def test_index_card_metadata_contains_clickable_filter_links(self):
        author = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=author,
            title='可以筛选的文章',
            category='mood',
            content='正文',
            status='published',
            visibility='public',
        )
        Post.objects.filter(pk=post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 24, 12, 0)),
        )

        response = self.client.get(reverse('index'))

        self.assertContains(response, 'author=writer')
        self.assertContains(response, 'date=2026-06-24')
        self.assertContains(response, 'category=mood')
        self.assertContains(response, 'post-meta-link')
        self.assertContains(response, 'post-category')

    def test_index_about_card_uses_current_user_profile_and_post_stats(self):
        owner = User.objects.create_superuser(username='root', password='StrongPass12345')
        current_user = User.objects.create_user(username='current', password='StrongPass12345')
        UserProfile.objects.create(user=owner, nickname='站点博主', bio='记录公开文章。')
        current_profile = UserProfile.objects.create(
            user=current_user,
            nickname='当前用户',
            bio='这是当前登录用户。',
        )
        Post.objects.create(
            author=owner,
            title='站点博主文章',
            category='life',
            content='公开可见',
            status='published',
            visibility='public',
            views_count=20,
        )
        Post.objects.create(
            author=current_user,
            title='当前用户文章',
            category='tech',
            content='自己可见',
            status='published',
            views_count=7,
        )
        self.client.login(username='current', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.context['profile'], current_profile)
        self.assertContains(response, '当前用户')
        self.assertContains(response, '这是当前登录用户。')
        self.assertEqual(response.context['published_count'], 1)
        self.assertEqual(response.context['total_views'], 7)

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

    def test_logged_in_user_can_comment_on_public_post(self):
        user = User.objects.create_user(
            username='commenter',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        self.client.login(
            username='commenter',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {'content': '这是一条测试评论。'},
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        comment = Comment.objects.get(post=post)
        self.assertEqual(comment.author, user)
        self.assertEqual(comment.content, '这是一条测试评论。')


    def test_anonymous_user_cannot_comment(self):
        author = User.objects.create_user(
            username='author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {'content': '游客评论。'},
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('add_comment', args=[post.id])}",
        )
        self.assertFalse(Comment.objects.filter(post=post).exists())


    def test_private_post_cannot_be_commented(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='私密文章',
            category='life',
            content='私密正文',
            status='published',
            visibility='private',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {'content': '不应该保存的评论。'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Comment.objects.filter(post=post).exists())

    def test_logged_in_user_can_reply_to_comment(self):
        post_author = User.objects.create_user(
            username='post-author',
            password='StrongPass12345',
        )
        reply_author = User.objects.create_user(
            username='reply-author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=post_author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        parent_comment = Comment.objects.create(
            post=post,
            author=post_author,
            content='主评论。',
        )
        self.client.login(
            username='reply-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {
                'content': '这是一条回复。',
                'parent_id': parent_comment.id,
            },
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        reply = Comment.objects.get(parent=parent_comment)
        self.assertEqual(reply.post, post)
        self.assertEqual(reply.author, reply_author)
        self.assertEqual(reply.content, '这是一条回复。')

    def test_reply_parent_must_belong_to_same_post(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        first_post = Post.objects.create(
            author=user,
            title='第一篇文章',
            category='life',
            content='第一篇正文',
            status='published',
            visibility='public',
        )
        second_post = Post.objects.create(
            author=user,
            title='第二篇文章',
            category='life',
            content='第二篇正文',
            status='published',
            visibility='public',
        )
        other_post_comment = Comment.objects.create(
            post=second_post,
            author=user,
            content='另一篇文章的评论。',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[first_post.id]),
            {
                'content': '伪造的跨文章回复。',
                'parent_id': other_post_comment.id,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Comment.objects.filter(
                post=first_post,
                content='伪造的跨文章回复。',
            ).exists()
        )

    def test_reply_cannot_target_another_reply(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        parent_comment = Comment.objects.create(
            post=post,
            author=user,
            content='主评论。',
        )
        first_reply = Comment.objects.create(
            post=post,
            author=user,
            parent=parent_comment,
            content='第一层回复。',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {
                'content': '不允许的第二层回复。',
                'parent_id': first_reply.id,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Comment.objects.filter(content='不允许的第二层回复。').exists()
        )

    def test_deleting_parent_comment_also_deletes_replies(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        parent_comment = Comment.objects.create(
            post=post,
            author=user,
            content='主评论。',
        )
        reply = Comment.objects.create(
            post=post,
            author=user,
            parent=parent_comment,
            content='回复。',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_comment', args=[parent_comment.id]),
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        self.assertFalse(Comment.objects.filter(id=parent_comment.id).exists())
        self.assertFalse(Comment.objects.filter(id=reply.id).exists())

    def test_comment_author_can_delete_own_comment(self):
        post_author = User.objects.create_user(
            username='post-author',
            password='StrongPass12345',
        )
        comment_author = User.objects.create_user(
            username='comment-author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=post_author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        comment = Comment.objects.create(
            post=post,
            author=comment_author,
            content='由评论者删除。',
        )
        self.client.login(
            username='comment-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_comment', args=[comment.id]),
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        self.assertFalse(Comment.objects.filter(id=comment.id).exists())

    def test_post_author_can_delete_comment_on_own_post(self):
        post_author = User.objects.create_user(
            username='post-author',
            password='StrongPass12345',
        )
        comment_author = User.objects.create_user(
            username='comment-author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=post_author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        comment = Comment.objects.create(
            post=post,
            author=comment_author,
            content='由文章作者管理。',
        )
        self.client.login(
            username='post-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_comment', args=[comment.id]),
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        self.assertFalse(Comment.objects.filter(id=comment.id).exists())

    def test_unrelated_user_cannot_delete_comment(self):
        post_author = User.objects.create_user(
            username='post-author',
            password='StrongPass12345',
        )
        comment_author = User.objects.create_user(
            username='comment-author',
            password='StrongPass12345',
        )
        unrelated_user = User.objects.create_user(
            username='unrelated',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=post_author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        comment = Comment.objects.create(
            post=post,
            author=comment_author,
            content='不能被无关用户删除。',
        )
        self.client.login(
            username='unrelated',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_comment', args=[comment.id]),
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        self.assertTrue(Comment.objects.filter(id=comment.id).exists())

    def test_delete_comment_rejects_get_request(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        comment = Comment.objects.create(
            post=post,
            author=user,
            content='不能通过 GET 删除。',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.get(
            reverse('delete_comment', args=[comment.id]),
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Comment.objects.filter(id=comment.id).exists())

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
        Post.objects.create(author=root, title='root 的文章', category='life', content='可见', status='published', visibility='public')
        Post.objects.create(author=writer, title='writer 的文章', category='life', content='不可见', status='published')

        response = self.client.get(reverse('rss_feed'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/rss+xml; charset=utf-8')
        self.assertContains(response, '<rss version="2.0">')
        self.assertContains(response, 'root 的文章')
        self.assertNotContains(response, 'writer 的文章')
    
    def test_post_detail_increments_views_count(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        post = Post.objects.create(
            author=owner,
            title='有浏览量的文章',
            category='life',
            content='测试浏览量',
            status='published',
        )
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('post_detail', args=[post.id]))

        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.views_count, 1)


class StartupPostCommandTests(TestCase):
    def deepseek_response(self):
        response_body = {
            'choices': [
                {
                    'message': {
                        'content': (
                            '{"title": "给早晨留出十分钟的整理时间", '
                            '"category": "life", '
                            '"tags": ["生活技巧", "整理"], '
                            '"content": "早晨的状态往往会影响一整天。可以把起床后的前十分钟留给简单整理：先喝一杯温水，再把桌面上明显不用的物品放回原位，最后写下今天最重要的一件事。这个过程不需要追求完美，重点是让自己从混乱里慢慢进入节奏。整理空间的同时，也是在整理注意力。坚持几天后，你会发现开始工作或学习时，犹豫和拖延会少一点。"}'
                        ),
                    },
                }
            ]
        }
        return FakeDeepSeekResponse(response_body)

    def test_create_startup_post_creates_one_published_daily_article_for_user(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        command_output = StringIO()
        current_date = timezone.localdate()

        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}, clear=True):
            with patch('blog.management.commands.create_startup_post.urlopen', return_value=self.deepseek_response()):
                call_command('create_startup_post', stdout=command_output)

        post = Post.objects.get(author=author)
        self.assertEqual(post.status, 'published')
        self.assertEqual(post.visibility, 'public')
        self.assertEqual(post.category, 'life')
        self.assertIn('自动发布', post.tags)
        self.assertIn('生活技巧', post.tags)
        self.assertIn(f'daily:{current_date.isoformat()}', post.tags)
        self.assertIn(current_date.strftime('%Y-%m-%d'), post.title)
        self.assertIn('Created daily article', command_output.getvalue())

    def test_create_startup_post_can_create_draft_when_requested(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')

        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}, clear=True):
            with patch('blog.management.commands.create_startup_post.urlopen', return_value=self.deepseek_response()):
                call_command('create_startup_post', draft=True)

        post = Post.objects.get(author=author)
        self.assertEqual(post.status, 'draft')
        self.assertEqual(post.visibility, 'private')

    def test_create_startup_post_skips_duplicate_for_same_day(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        command_output = StringIO()

        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}, clear=True):
            with patch('blog.management.commands.create_startup_post.urlopen', return_value=self.deepseek_response()):
                call_command('create_startup_post', stdout=command_output)
                call_command('create_startup_post', stdout=command_output)

        self.assertEqual(Post.objects.filter(author=author).count(), 1)
        self.assertIn('Daily article already exists', command_output.getvalue())

    def test_create_startup_post_can_attach_cover_to_existing_daily_post(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        current_date = timezone.localdate()
        post = Post.objects.create(
            author=author,
            title=f'{current_date.strftime("%Y-%m-%d")}｜已有文章',
            category='life',
            tags=f'自动发布,生活技巧,daily:{current_date.isoformat()}',
            content='已有正文',
            status='published',
        )

        with patch.dict(os.environ, {'PEXELS_API_KEY': 'test-key'}, clear=True):
            with patch('blog.management.commands.create_startup_post.Command.attach_cover') as attach_cover:
                call_command('create_startup_post', username='白车轴草', cover_existing=True)

        self.assertEqual(Post.objects.filter(author=author).count(), 1)
        attach_cover.assert_called_once()
        attached_post = attach_cover.call_args.args[0]
        generated_article = attach_cover.call_args.args[1]
        self.assertEqual(attached_post, post)
        self.assertEqual(generated_article['title'], '已有文章')
        self.assertEqual(generated_article['category'], 'life')

    def test_create_startup_post_requires_deepseek_api_key(self):
        User.objects.create_user(username='白车轴草', password='StrongPass12345')

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CommandError):
                call_command('create_startup_post')

    def test_create_startup_post_requires_existing_user(self):
        with self.assertRaises(CommandError):
            call_command('create_startup_post', username='missing-user')

    def test_attach_cover_saves_pexels_photo_and_attribution(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        post = Post.objects.create(
            author=author,
            title='测试文章',
            category='life',
            tags='自动发布,daily:2026-06-23',
            content='测试正文',
            status='published',
        )
        generated_article = {
            'title': '给早晨留出十分钟的整理时间',
            'category': 'life',
            'tags': ['生活技巧', '整理'],
            'content': '测试正文',
        }
        pexels_photo = {
            'id': 12345,
            'url': 'https://www.pexels.com/photo/test-photo-12345/',
            'photographer': 'Test Photographer',
            'photographer_url': 'https://www.pexels.com/@test',
            'src': {'landscape': 'https://images.pexels.com/photos/12345/test.jpg'},
        }
        command = Command()

        with patch.dict(os.environ, {'PEXELS_API_KEY': 'test-key'}, clear=True):
            with patch.object(command, 'search_pexels_photo', return_value=pexels_photo):
                with patch.object(command, 'download_pexels_image', return_value=b'image-bytes'):
                    with patch.object(FieldFile, 'save') as save_cover:
                        command.attach_cover(post, generated_article, timezone.localdate())

        post.refresh_from_db()
        self.assertIn('Photo by Test Photographer on Pexels', post.content)
        self.assertIn('https://www.pexels.com/photo/test-photo-12345/', post.content)
        save_cover.assert_called_once()


class FakeDeepSeekResponse:
    def __init__(self, response_body):
        self.response_body = response_body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.response_body).encode('utf-8')
