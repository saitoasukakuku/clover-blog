from django.contrib import admin

from blog.models import Post, UserProfile


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'category', 'status', 'views_count', 'created_at', 'updated_at')
    list_filter = ('status', 'category', 'created_at')
    search_fields = ('title', 'content', 'tags')
    ordering = ('-created_at',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'nickname', 'github_url', 'weibo_url', 'updated_at')
    search_fields = ('user__username', 'nickname', 'bio')
    ordering = ('user__username',)