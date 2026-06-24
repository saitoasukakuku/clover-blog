from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from blog.models import Comment, UserProfile


class ChineseUserCreationForm(UserCreationForm):
    error_messages = {
        'password_mismatch': '两次输入的密码不一致。',
    }

    email = forms.EmailField(
        label='邮箱',
        required=False,
        help_text='可选。填写后会绑定到账号，方便以后展示或联系。',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入邮箱',
            'autocomplete': 'email',
        }),
        error_messages={'invalid': '请输入有效的邮箱地址。'},
    )
    nickname = forms.CharField(
        label='昵称',
        required=False,
        max_length=50,
        help_text='可选。昵称会优先作为展示名称，留空则显示用户名。',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入昵称',
            'autocomplete': 'nickname',
            'maxlength': 50,
        }),
    )
    username = forms.CharField(
        label='用户名',
        max_length=150,
        help_text='用户名最多 150 个字符，只能包含字母、数字和 @/./+/-/_。',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入用户名',
            'autocomplete': 'username',
        }),
        error_messages={
            'required': '请输入用户名。',
            'max_length': '用户名最多 150 个字符。',
            'unique': '这个用户名已经被注册。',
        },
    )
    password1 = forms.CharField(
        label='密码',
        help_text='密码至少 8 位，不能与个人信息太相似，不能是常见密码，也不能全是数字。',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入密码',
            'autocomplete': 'new-password',
        }),
        error_messages={'required': '请输入密码。'},
    )
    password2 = forms.CharField(
        label='确认密码',
        help_text='请再次输入同样的密码，用于确认。',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '请再次输入密码',
            'autocomplete': 'new-password',
        }),
        error_messages={'required': '请再次输入密码。'},
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'nickname', 'password1', 'password2')

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('这个邮箱已经被注册。')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data.get('email', '')
        if commit:
            user.save()
            UserProfile.objects.get_or_create(
                user=user,
                defaults={'nickname': self.cleaned_data.get('nickname', '')},
            )
        return user


class ChineseAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label='用户名',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入用户名',
            'autocomplete': 'username',
        }),
        error_messages={'required': '请输入用户名。'},
    )
    password = forms.CharField(
        label='密码',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入密码',
            'autocomplete': 'current-password',
        }),
        error_messages={'required': '请输入密码。'},
    )

    error_messages = {
        'invalid_login': '用户名或密码不正确，请重新输入。',
        'inactive': '这个账号已被停用。',
    }


class UserCenterForm(forms.ModelForm):
    email = forms.EmailField(
        label='绑定邮箱',
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入常用邮箱',
            'autocomplete': 'email',
        }),
        error_messages={'invalid': '请输入有效的邮箱地址。'},
    )

    class Meta:
        model = UserProfile
        fields = ('avatar', 'nickname', 'bio', 'github_url', 'weibo_url', 'email')
        labels = {
            'avatar': '头像',
            'nickname': '昵称',
            'bio': '个人简介',
            'github_url': 'GitHub 链接',
            'weibo_url': '微博链接',
        }
        widgets = {
            'avatar': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*',
            }),
            'nickname': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '给自己起个展示昵称',
                'maxlength': 50,
            }),
            'bio': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': '写一句简短介绍',
                'rows': 4,
                'maxlength': 160,
            }),
            'github_url': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://github.com/yourname',
                'autocomplete': 'url',
            }),
            'weibo_url': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://weibo.com/yourname',
                'autocomplete': 'url',
            }),
        }
        help_texts = {
            'avatar': '支持 JPG、PNG 等常见图片格式。',
            'nickname': '留空时默认显示用户名。',
            'bio': '最多 160 个字符，会显示在用户中心。',
            'github_url': '填写后页脚 GitHub 图标会跳转到这个地址。',
            'weibo_url': '填写后页脚微博图标会跳转到这个地址。',
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        super().__init__(*args, **kwargs)
        self.fields['email'].initial = self.user.email

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()
        if email and User.objects.exclude(pk=self.user.pk).filter(email__iexact=email).exists():
            raise forms.ValidationError('这个邮箱已经绑定到其他账号。')
        return email

    def save(self, commit=True):
        profile = super().save(commit=False)
        self.user.email = self.cleaned_data.get('email', '')
        if commit:
            self.user.save(update_fields=['email'])
            profile.save()
        return profile


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ('content',)
        labels = {
            'content': '评论内容',
        }
        widgets = {
            'content': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': '写下你的评论...',
                'rows': 4,
                'maxlength': 1000,
            }),
        }
        error_messages = {
            'content': {
                'required': '请输入评论内容。',
                'max_length': '评论内容不能超过 1000 个字符。',
            },
        }