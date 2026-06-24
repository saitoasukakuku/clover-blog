from django.contrib import admin

from blog.models import Comment, Post, UserProfile


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'category', 'status', 'visibility', 'views_count', 'created_at', 'updated_at')
    list_filter = ('status', 'category', 'visibility', 'created_at')
    search_fields = ('title', 'content', 'tags')
    ordering = ('-created_at',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'nickname', 'github_url', 'weibo_url', 'updated_at')
    search_fields = ('user__username', 'nickname', 'bio')
    ordering = ('user__username',)


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('post', 'author', 'parent', 'content_preview', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('content', 'author__username', 'post__title')
    ordering = ('-created_at',)

    @admin.display(description='评论内容')
    def content_preview(self, comment):
        return comment.content[:40]
