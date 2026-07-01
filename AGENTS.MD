# AGENTS.md

## 项目概览

- 状态更新时间：2026-07-01
- 项目名：白车轴草
- 项目类型：Django 4.2 多用户博客
- 本地工作区：`E:\projects\django-projects\白车轴草`
- Django 工程根目录：`E:\projects\django-projects\白车轴草\白车轴草`
- GitHub 仓库：`https://github.com/saitoasukakuku/clover-blog`
- 默认分支：`main`
- 当前线上部署来源：GitHub `main`，通过服务器一键部署脚本快进更新
- 线上服务器公网 IPv4：`111.230.11.5`
- 线上服务器内网 IPv4：`10.1.0.7`
- 服务器项目目录：`/opt/clover-blog`
- 公网 HTTP 地址：`http://111.230.11.5/`
- 当前 Cloudflare Quick Tunnel：
  `https://salary-undergraduate-euros-ampland.trycloudflare.com/`

Cloudflare Quick Tunnel 地址是临时随机地址，服务重启后可能变化，不能作为稳定域名。
截至 2026-06-26，该 HTTPS 地址已验证返回 `200 OK`。

仓库根目录用于 Git、依赖清单和协作文档，真正的 Django 工程位于
`白车轴草/` 子目录。核心业务集中在单个 `blog` 应用中。

## 当前交接摘要

截至 2026-07-01，本地 `main` 分支与 `origin/main` 同步，最新提交为
`28fca64 fix: lock registration completion code use`。本地没有未提交的受 Git 跟踪
文件改动；仍有两个无关未跟踪目录 `docs/superpowers/mockups/` 和 `promo-video/`，
不要在无明确指令时暂存、提交或删除。

最近已推送到 GitHub `main` 的关键提交为：

- `28fca64 fix: lock registration completion code use`
- `c1b51c7 feat: complete registration with invite code`
- `1a6e0eb feat: add registration review page`
- `3a8d947 feat: request registration by email`
- `4ec54f9 feat: add registration request model`
- `28b6a3a feat: add image carousel homepage template`
- `2405dd7 feat: route root to homepage`
- `0dc70b6 feat: prepare homepage carousel data`

本地已完成 Django 检查、迁移一致性检查和 `blog` 应用测试，当前测试基线为
`136` 个测试通过。已验证 `/` 可访问新的沉浸式图片轮播欢迎页，`/index/` 保留文章
列表；已验证 `/register/` 只提交邮箱申请，超级用户可在 `/registration-requests/`
审核，通过后邮件发送一次性注册码，用户再到 `/register/complete/` 完成账号创建。

本次首页和注册审核功能尚未确认部署到生产服务器。2026-06-27 助手执行 SSH 批处理
连通性检查时，`ubuntu@111.230.11.5` 返回 `Permission denied (publickey,password)`，
因此助手无法直接运行 `sudo clover-blog-deploy`。需要服务器操作时，直接让用户在
服务器终端执行，并根据用户贴回的输出继续判断。

近期已完成的关键事项：

- 删除已发布文章和删除草稿已改为只接受 POST 请求，模板使用 CSRF 表单提交。
- 服务器一键部署脚本已可从 GitHub `main` 快进拉取并部署，服务器访问 GitHub 已通过
  `ssh.github.com:443` 和只读 Deploy Key 解决。
- 备份定时器、备份完整性检查、恢复演练、14 天备份保留策略和磁盘使用率告警已完成。
- 每日 AI 自动发文 systemd 定时器已配置为每天服务器时间 `08:30` 直接公开发布。
- 已新增归档页 `/archive/` 和标签页 `/tags/`，导航栏使用右侧快捷入口。
- 文章详情页和首页文章卡片已展示可点击标签；标签点击进入首页 `?tag=` 精确标签筛选，
  不再复用关键词搜索；内部 `daily:*` 标签不展示。
- 已新增作者主页、文章收藏、收藏列表和通知中心；评论、回复、好友申请通过和私信会
  生成站内通知。
- `/index/` 文章列表搜索框已移除按日期搜索；后端会忽略旧的 `date` 查询参数，
  文章卡片发布日期只作展示，不再作为筛选入口。
- `/` 已改为沉浸式欢迎页，背景轮播读取 `白车轴草/media/index_img/` 下的图片；
  `/index/` 继续作为原文章列表页。
- 注册流程已改为邮箱申请制：用户先填写邮箱，超级用户在站内审核页批准或拒绝；批准后
  系统发送一次性注册码邮件，用户用邮箱、注册码、用户名、昵称和密码完成注册。
- 邮件发送配置已支持 `.env` 中的 SMTP 环境变量；本地 `DEBUG=True` 默认使用 console
  邮件后端，生产默认使用 SMTP 后端。

当前建议的下一步可选方向：

- 恢复 Codex SSH 部署权限，或由用户在服务器运行 `sudo clover-blog-deploy`，
  部署最新 GitHub `main` 并执行迁移 `0011_registrationrequest.py`。
- 在生产 `/opt/clover-blog/.env` 配好 SMTP，至少包含 `EMAIL_HOST`、`EMAIL_PORT`、
  `EMAIL_HOST_USER`、`EMAIL_HOST_PASSWORD`、`EMAIL_USE_TLS` 或 `EMAIL_USE_SSL`、
  `DEFAULT_FROM_EMAIL`。
- 生产部署后验证 `/`、`/index/`、`/register/`、`/register/complete/` 和
  `/registration-requests/`。
- 确认超级用户 `白车轴草` 的邮箱是否正确；注册审核邮件会优先使用该超级用户邮箱作为
  发送身份候选。
- 后续可继续优化注册审核页，例如重发注册码、审核备注、邮件发送失败重试和管理后台筛选。

## 当前技术栈

- 本地 Python 3.9
- 线上 Python 3.12.3
- Django 4.2
- 本地数据库：SQLite
- 线上数据库：MySQL 8，通过 `.env` 配置
- Gunicorn 26
- Nginx 1.24
- WhiteNoise
- Pillow
- python-dotenv
- Bootstrap 5
- jQuery
- Font Awesome
- Cropper.js
- DeepSeek API
- Pexels API
- Cloudflare Tunnel

依赖清单位于仓库根目录的 `requirements.txt`。

当前依赖仍固定为 `Django==4.2`，尚未完成 Django 5.2 LTS 升级。不要在未运行
完整测试和线上部署验证前直接升级框架。

## 目录结构

- `AGENTS.md`：项目状态、协作规则和运维说明。
- `requirements.txt`：Python 依赖清单。
- `白车轴草/manage.py`：Django 管理命令入口。
- `白车轴草/白车轴草/settings.py`：项目配置和数据库选择逻辑。
- `白车轴草/白车轴草/urls.py`：项目路由。
- `白车轴草/blog/models.py`：文章、用户资料、注册申请、评论、好友和私信模型。
- `白车轴草/blog/forms.py`：注册申请、注册码注册、登录、用户资料、评论和私信表单。
- `白车轴草/blog/views.py`：主要业务视图。
- `白车轴草/blog/admin.py`：文章、用户资料、注册申请、评论、好友和私信后台配置。
- `白车轴草/blog/registration_approval.py`：注册审核、注册码生成、邮件发送和状态变更逻辑。
- `白车轴草/blog/site_owner.py`：站点博主账号选择逻辑。
- `白车轴草/blog/context_processors.py`：页脚社交链接上下文。
- `白车轴草/blog/templates/`：全部 Django 模板。
- `白车轴草/blog/templates/home.html`：沉浸式图片轮播欢迎页模板。
- `白车轴草/blog/templates/index.html`：文章列表页模板。
- `白车轴草/blog/templates/registration_requests.html`：超级用户注册审核页模板。
- `白车轴草/blog/static/`：本地 JS、Bootstrap、Font Awesome 等资源。
- `白车轴草/blog/management/commands/create_startup_post.py`：AI 自动发文命令。
- `白车轴草/blog/migrations/`：数据库迁移，当前 GitHub `main` 最新为
  `0011_registrationrequest.py`。
- `白车轴草/media/`：本地上传文件，已被 Git 忽略。
- `白车轴草/media/index_img/`：沉浸式首页背景轮播图片目录，已被 Git 忽略。
- `白车轴草/db.sqlite3`：本地开发数据库，已被 Git 忽略。

## 数据模型

### Post

核心文章模型 `blog.models.Post` 包含：

- `author`：文章作者，关联 Django `User`。
- `title`：标题，最长 200 个字符。
- `category`：分类，支持预设分类和自定义分类。
- `tags`：标签字符串，最长 200 个字符。
- `cover`：文章封面，保存到 `media/covers/`。
- `content`：文章正文。
- `status`：`draft` 或 `published`。
- `visibility`：`private` 或 `public`，默认 `private`。
- `views_count`：浏览次数。
- `created_at`：创建时间。
- `updated_at`：更新时间。

### UserProfile

用户资料模型 `blog.models.UserProfile` 与 Django `User` 一对一关联，包含：

- 头像
- 昵称
- 个人简介
- GitHub 链接
- 微博链接
- 创建和更新时间

显示名称优先使用昵称，昵称为空时使用用户名。

### RegistrationRequest

注册申请模型 `blog.models.RegistrationRequest` 用于邮箱审核注册流程，包含：

- `email`：申请邮箱，保存时会去除首尾空白、按 Django 邮箱规则标准化并 `casefold`。
- `status`：申请状态，支持 `pending`、`approved`、`rejected` 和 `used`。
- `invite_code_hash`：注册码哈希，不保存明文注册码。
- `code_expires_at`：注册码过期时间，目前审批通过后有效期为 7 天。
- `approved_by`：审核人，关联 Django `User`，可为空。
- `reviewed_at`：审核时间。
- `used_at`：注册码被成功使用的时间。
- `created_at`、`updated_at`：申请和更新时间。

注册申请同一邮箱只能存在一条。批准时会生成一次性注册码并发送邮件；完成注册时会用
数据库事务和 `select_for_update()` 锁定申请记录，确认状态、过期时间和注册码都有效后
创建用户，并把申请状态标记为 `used`。

### Comment

评论模型 `blog.models.Comment` 包含：

- `post`：所属文章。
- `author`：评论者。
- `parent`：父评论，可为空；用于一层回复。
- `content`：评论或回复内容，最长 1000 个字符。
- `created_at`：发表时间。

当前只支持一层回复：

- 主评论的 `parent` 为 `None`。
- 回复的 `parent` 指向主评论。
- 后端禁止回复另一条回复，避免无限嵌套。
- 删除主评论时，其回复通过 `CASCADE` 一并删除。

### FriendRequest

好友申请模型 `blog.models.FriendRequest` 包含：

- `sender`：申请人。
- `receiver`：接收人。
- `status`：`pending`、`accepted`、`rejected` 或 `cancelled`。
- `created_at`、`updated_at`：创建和更新时间。

同一方向的好友申请只能存在一条，不能向自己发送好友申请。

### Friendship

好友关系模型 `blog.models.Friendship` 使用 `user_low` 和 `user_high` 保存一对好友：

- 用户 ID 按从小到大排序，避免同一好友关系重复保存。
- 同一对用户只能存在一条好友关系。
- 用户不能与自己建立好友关系。

### PrivateMessage

私信模型 `blog.models.PrivateMessage` 包含：

- `sender`：发送者。
- `recipient`：接收者。
- `content`：私信内容，最长 2000 个字符。
- `is_read`：是否已读。
- `created_at`：发送时间。

用户不能给自己发送私信，私信只允许在好友之间发送。

### PostFavorite

文章收藏模型 `blog.models.PostFavorite` 包含：

- `user`：收藏用户。
- `post`：被收藏文章。
- `created_at`：收藏时间。

同一用户对同一篇文章只能收藏一次。收藏列表只展示当前用户仍有权限阅读的文章。

### Notification

站内通知模型 `blog.models.Notification` 包含：

- `recipient`：通知接收者。
- `actor`：触发通知的用户，可为空。
- `notification_type`：通知类型。
- `post`：相关文章，可为空。
- `comment`：相关评论，可为空。
- `private_message`：相关私信，可为空。
- `friend_request`：相关好友申请，可为空。
- `message`：通知文案，最长 255 个字符。
- `target_url`：通知点击后的站内跳转地址，可为空。
- `is_read`：是否已读。
- `created_at`：通知时间。

当前通知类型包括文章评论、评论回复、收到好友申请、好友申请通过和私信。通知只允许
接收者查看和标记已读。

## 用户与文章权限

### 首页与文章列表

- `/` 是沉浸式图片轮播欢迎页，面向游客和登录用户展示站点入口、今日推荐、最近更新和
  归档/标签等探索入口。
- `/` 的背景图从 `MEDIA_ROOT/index_img/` 读取，最多取前 8 张合法图片文件，并按预设
  主题自动切换文案、点缀色和氛围标签。
- `/index/` 是原文章列表页，继续承载搜索、分类、作者、标签和“只看我的文章”等筛选。

- 游客只能看到 `published + public` 的文章。
- 登录用户可以看到所有公开已发布文章，以及自己的全部已发布文章。
- 登录用户可以点击“只看我的文章”筛选自己的文章。
- 文章列表支持作者、分类、标签和关键词组合筛选；不再提供按日期筛选。
- 文章卡片中的作者、分类和标签可点击筛选；发布日期只展示，不再作为筛选入口。
- 作者筛选也会同步影响右侧“最近文章”和分类统计。

### 注册与账号创建

- `/register/` 只收集邮箱，提交后创建或重新打开 `RegistrationRequest`，不会直接创建
  Django 用户。
- 同一邮箱已注册为用户时，申请表单会拒绝提交。
- 同一邮箱已有待审核申请时，会提示等待审核；已通过且未使用的申请会引导用户去
  `/register/complete/`。
- `/registration-requests/` 只允许超级用户查看；批准和拒绝操作都只接受 POST 请求。
- 批准申请时会生成一次性注册码，注册码只以哈希形式入库，并通过邮件发送给申请邮箱。
- 拒绝申请时会清空原注册码哈希、过期时间和使用时间。
- `/register/complete/` 要求邮箱、注册码、用户名、昵称和两次密码；注册码验证成功后
  创建用户、保存昵称，并把注册申请状态标记为 `used`。

### 文章详情

- 游客只能访问公开且已发布的文章。
- 登录用户可以访问公开已发布文章，以及自己的文章。
- 只有文章作者可以编辑或删除自己的文章。
- 编辑表单支持修改状态、可见范围、分类、标签、正文和封面。
- 登录用户可以收藏或取消收藏自己有权限访问的文章。

### 评论与回复

- 只有公开且已发布的文章开放评论和回复。
- 只有登录用户可以发表评论或回复。
- 私密文章和草稿不显示评论区，也不能通过伪造请求评论。
- 评论者可以删除自己的评论或回复。
- 文章作者可以删除自己文章下的任意评论或回复。
- 无关用户不能删除他人的评论。
- 删除评论使用 POST 请求，GET 请求会被拒绝。
- 评论和回复总数显示在文章详情页。

### 好友与私信

- 登录用户可以搜索和查看其他用户。
- 支持发送、接受、拒绝和取消好友申请。
- 支持解除好友关系。
- 只有好友之间可以发送私信。
- 会话列表显示最近消息和未读状态。
- 打开会话后，对方发给当前用户的未读消息会被标记为已读。
- 好友、好友申请和私信操作均需要登录，并进行后端权限校验。

### 收藏与通知

- 收藏功能只对登录用户开放，并复用文章可见性校验。
- 收藏列表只展示当前用户仍可访问的文章。
- 评论文章、回复评论、收到好友申请、好友申请被接受和收到私信会生成站内通知。
- 通知中心只展示当前登录用户自己的通知。
- 标记单条通知或全部通知为已读都需要登录，并进行接收者校验。

## 已实现功能

### 首页欢迎页

- `/` 是沉浸式图片轮播欢迎页，不再直接进入文章列表。
- 背景轮播读取 `白车轴草/media/index_img/` 下的图片。
- 首页主题色、首屏文案、推荐卡片和氛围标签会随轮播图切换。
- 首屏提供进入文章列表、查看博主主页和阅读推荐文章的入口。
- 下方展示最近更新，并提供全部文章、归档、标签和关于博主入口。
- 缺少轮播图片时有降级背景，不会阻断页面访问。

### 账号与资料

- 邮箱申请注册、站内超级用户审核、邮件注册码和注册码完成注册。
- 注册申请审核页只允许超级用户访问。
- 注册码只保存哈希，有有效期，成功注册后只能使用一次。
- 登录、退出。
- 用户中心。
- 头像上传和裁剪。
- 昵称、简介、邮箱、GitHub、微博资料。
- 页脚社交链接使用站点博主账号资料。
- 作者主页，按用户展示头像、昵称、简介、文章列表、文章数量和总浏览量。

### 文章

- 创建文章。
- 保存草稿。
- 草稿箱。
- 发布文章。
- 编辑文章。
- 删除草稿和已发布文章。
- 文章公开/仅自己可见。
- 预设分类和自定义分类。
- 标签。
- 封面上传、裁剪、移除和替换。
- `/index/` 文章列表关键词搜索。
- 分类、作者和标签筛选。
- `/index/` 搜索结果显示命中文章数量。
- “只看我的文章”筛选。
- 文章归档页，按年月查看已发布文章。
- 文章标签页，按标签数量查看标签并跳转首页精确标签筛选。
- 文章详情页展示可点击标签，点击后跳转首页精确标签筛选；内部 `daily:*` 标签不展示。
- 首页文章卡片展示最多 3 个可点击标签，点击后跳转首页精确标签筛选；内部 `daily:*` 标签不展示。
- 首页已移除按时间搜索，后端会忽略旧的 `date` 查询参数。
- 分页。
- 最近文章。
- 浏览量统计。
- RSS。

### 文章列表页“关于博主”

- 登录后显示当前登录用户的头像、昵称、简介、已发布数量和总浏览量。
- 未登录时显示站点博主资料。
- 站点博主优先使用用户名为 `root` 的账号；不存在时使用第一个超级用户。

### 评论

- 公开文章评论。
- 一层评论回复。
- 评论和回复头像、昵称、时间显示。
- 评论者和文章作者删除权限。
- Django Admin 评论管理。

### 好友与私信

- 好友列表和用户搜索。
- 好友申请发送、接受、拒绝和取消。
- 解除好友。
- 好友私信会话列表。
- 私信发送、展示和已读状态。
- 导航栏好友申请和未读私信数量提示。
- Django Admin 好友关系、好友申请和私信管理。

### 收藏

- 文章详情页收藏和取消收藏。
- “我的收藏”列表，按收藏时间倒序分页展示。
- 收藏入口会带回跳地址，操作后返回原页面。
- Django Admin 文章收藏管理。

### 通知

- 通知中心。
- 导航栏未读通知数量提示。
- 单条通知标记已读并跳转到相关页面。
- 一键标记全部通知为已读。
- 评论、回复、好友申请、好友申请通过和私信自动生成通知。
- Django Admin 通知管理。

### AI 写作

创建文章页提供“AI 写文章”功能：

- 登录用户填写主题、补充要求和文章长度。
- 后端调用 DeepSeek 生成标题、分类、标签和正文。
- 生成结果只回填到表单，不自动保存或发布。
- 用户检查后自行保存草稿或发布。
- 每个会话有 60 秒生成冷却时间，减少 API 消耗。
- DeepSeek API Key 只保存在服务器 `.env`，不会传给浏览器。

### AI 封面

- AI 写作时可选择同时匹配 Pexels 封面。
- 页面先展示远程预览。
- 用户保存文章时，服务器才下载封面到本地媒体目录。
- 封面数据使用 Django 签名令牌，且只允许下载
  `https://images.pexels.com`。
- 手动上传或裁剪封面会覆盖 AI 推荐封面。
- 使用 Pexels 图片时会在正文末尾附加摄影师和图片来源。
- Pexels 失败不会阻止正文生成或文章保存。

### 每日 AI 自动发文

管理命令：

```powershell
python .\白车轴草\manage.py create_startup_post
```

主要行为：

- 使用 DeepSeek 生成每日文章。
- 默认创建公开且已发布的文章。
- 使用 `daily:YYYY-MM-DD` 标签防止当天重复生成。
- 根据文章内容从 Pexels 获取封面。
- `--draft`：生成仅自己可见的草稿。
- `--skip-cover`：跳过 Pexels 封面。
- `--cover-existing`：为当天已有自动文章补封面。
- `--username 用户名`：指定文章作者。
- `--model 模型名`：覆盖 `.env` 中的 DeepSeek 模型。

截至 2026-06-27，每日 AI 自动发文的 systemd 定时任务已在当前 Ubuntu 服务器配置：

- `clover-blog-startup-post.service`
- `clover-blog-startup-post.timer`

当前生产策略：

- 每天服务器时间 `08:30` 自动运行。
- 使用 Django 用户名 `白车轴草` 作为文章作者。
- 直接创建公开且已发布文章，不创建草稿。
- 默认尝试从 Pexels 匹配并保存封面。
- 不配置自动重试，避免接口异常时重复消耗 DeepSeek/Pexels API 额度。
- `Persistent=true`，服务器错过执行时间后会在恢复运行时补执行一次。
- 2026-06-27 已手动运行验证，生成文章
  `2026-06-27｜夏日凉拌黄瓜的脆爽秘诀`，并保存封面
  `auto_covers/2026-06-27-14270809.jpg`。

常用运维命令：

```bash
sudo systemctl list-timers --all | grep clover-blog-startup-post
sudo systemctl status clover-blog-startup-post.timer --no-pager -l
sudo systemctl status clover-blog-startup-post.service --no-pager -l
sudo journalctl -u clover-blog-startup-post.service --no-pager -n 80
sudo systemctl start clover-blog-startup-post.service
sudo systemctl disable --now clover-blog-startup-post.timer
sudo systemctl enable --now clover-blog-startup-post.timer
```

命令用途：

- 第一行：查看下一次自动发文时间。
- 第二行：查看定时器是否启用。
- 第三行：查看最近一次自动发文任务的执行状态。
- 第四行：查看最近 80 行自动发文日志。
- 第五行：立即手动运行一次自动发文任务；如果当天已经生成过，会跳过。
- 第六行：暂停每日自动发文。
- 第七行：恢复每日自动发文。

## 路由

主要路由位于 `白车轴草/白车轴草/urls.py`：

- `/`：沉浸式图片轮播欢迎页。
- `/admin/`：Django Admin。
- `/index/`：文章列表页。
- `/archive/`：文章归档页。
- `/tags/`：文章标签页。
- `/users/<username>/`：作者主页。
- `/register/`：邮箱注册申请。
- `/register/complete/`：输入邮箱、注册码、用户名、昵称和密码完成注册。
- `/registration-requests/`：超级用户注册申请审核页。
- `/registration-requests/<request_id>/approve/`：批准注册申请并发送注册码邮件。
- `/registration-requests/<request_id>/reject/`：拒绝注册申请。
- `/login/`：登录。
- `/logout/`：退出。
- `/user_center/`：用户中心。
- `/friends/`：好友列表、用户搜索和好友申请。
- `/friends/request/<user_id>/`：发送好友申请。
- `/friends/request/<request_id>/accept/`：接受好友申请。
- `/friends/request/<request_id>/reject/`：拒绝好友申请。
- `/friends/request/<request_id>/cancel/`：取消好友申请。
- `/friends/<user_id>/remove/`：解除好友关系。
- `/messages/`：私信会话列表。
- `/messages/<user_id>/`：与指定好友的私信会话。
- `/favorites/`：我的收藏。
- `/notifications/`：通知中心。
- `/notifications/<notification_id>/read/`：标记单条通知已读并跳转。
- `/notifications/read_all/`：标记全部通知已读。
- `/index/create_post/`：创建文章。
- `/index/create_post/ai/`：AI 写作 POST 接口。
- `/drafts/`：草稿箱。
- `/edit_post/<post_id>/`：编辑文章。
- `/delete_draft/<post_id>/`：删除草稿。
- `/post/<post_id>/`：文章详情。
- `/post/<post_id>/favorite/`：收藏或取消收藏文章。
- `/post/<post_id>/comment/`：提交评论或回复。
- `/comment/<comment_id>/delete/`：删除评论或回复。
- `/post/<post_id>/delete/`：删除已发布文章。
- `/rss.xml`：RSS。

## 数据库配置

`settings.py` 根据环境变量选择数据库：

- `DB_ENGINE=mysql`：使用 MySQL。
- 未设置时：使用本地 SQLite。

生产环境数据库信息通过 `/opt/clover-blog/.env` 提供。不要把 `.env`、
API Key、数据库密码、Django Secret Key 或其他凭据提交到 Git。

本地 SQLite 和媒体目录已在 `.gitignore` 中忽略。

## 环境变量

项目会使用以下环境变量：

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DB_ENGINE`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_MODEL`
- `PEXELS_API_KEY`
- `STARTUP_POST_USERNAME`
- `EMAIL_BACKEND`
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`
- `EMAIL_USE_SSL`
- `DEFAULT_FROM_EMAIL`

文档只能记录变量名，不得记录真实密钥或密码。

本地 `DEBUG=True` 时，如果未显式设置 `EMAIL_BACKEND`，默认使用
`django.core.mail.backends.console.EmailBackend`，注册码邮件会打印到控制台。生产
`DEBUG=False` 时，如果未显式设置 `EMAIL_BACKEND`，默认使用
`django.core.mail.backends.smtp.EmailBackend`，必须在 `/opt/clover-blog/.env` 中配置
SMTP 相关变量。

## 本地开发

从仓库根目录运行：

```powershell
python .\白车轴草\manage.py check
python .\白车轴草\manage.py test blog
python .\白车轴草\manage.py runserver
```

模型变更时运行：

```powershell
python .\白车轴草\manage.py makemigrations blog
python .\白车轴草\manage.py migrate
python .\白车轴草\manage.py makemigrations --check --dry-run
```

当前测试基线：

- `Found 136 test(s)`
- `Ran 136 tests`
- `OK`

2026-07-01 已重新运行：

```text
System check identified no issues (0 silenced).
Ran 136 tests
OK
```

测试期间可能出现以下警告：

```text
No directory at: ...\staticfiles\
```

本地尚未执行 `collectstatic` 时该警告可出现，不代表测试失败。生产部署会执行
`collectstatic`。

## Git 工作流

常用命令：

```powershell
git status
git diff
git add 文件路径
git commit -m "提交说明"
git push
```

默认在 `main` 分支工作。助手直接修改代码后，除非用户明确要求不要推送，否则应：

1. 运行相关检查和测试。
2. 只暂存本次任务涉及的文件。
3. 创建语义明确的提交。
4. 推送到 GitHub。
5. 在回复中报告提交哈希和验证结果。

不要静默提交与当前任务无关的用户改动。

## 生产部署

服务器信息：

- 云服务：腾讯云轻量应用服务器
- 系统：Ubuntu 24.04.4 LTS
- CPU：2 核
- 内存：约 2 GiB
- Swap：约 2 GiB
- 系统盘：40 GiB
- SSH 用户：`ubuntu`
- 项目目录：`/opt/clover-blog`
- 虚拟环境：`/opt/clover-blog/.venv`
- Gunicorn 服务：`clover-blog.service`
- Nginx 服务：`nginx`
- MySQL 服务：`mysql`
- Cloudflare 服务：`cloudflared-quick-tunnel.service`
- 备份服务：`clover-blog-backup.service`
- 备份定时器：`clover-blog-backup.timer`
- 一键部署命令：`sudo clover-blog-deploy`
- 一键部署脚本：`/usr/local/sbin/clover-blog-deploy`
- 仓库内脚本源文件：`scripts/deploy_production.sh`

当前部署结构：

```text
浏览器
  -> Cloudflare Quick Tunnel（HTTPS）
  -> Nginx 127.0.0.1:80
  -> Gunicorn 127.0.0.1:8000
  -> Django
  -> MySQL 127.0.0.1:3306
```

本地完成修改、测试、提交和推送后：

```powershell
git push origin main
```

登录服务器并运行一键部署：

```bash
ssh ubuntu@111.230.11.5
sudo clover-blog-deploy
```

一键部署脚本会：

- 使用文件锁避免两个部署同时执行。
- 检查服务器是否存在未提交的受 Git 跟踪文件改动。
- 通过仓库只读 Deploy Key 和 `ssh.github.com:443` 获取 `origin/main`。
- 单次 Git 获取最多等待 30 秒，网络失败时最多尝试 3 次。
- 只允许从当前提交快进到 `origin/main`，不会执行强制重置。
- 同步 Python 依赖。
- 执行数据库迁移和 `collectstatic`。
- 执行 Django 部署检查和 Nginx 配置检查。
- 重启 Gunicorn，重新加载 Nginx。
- 检查 Gunicorn、Nginx、MySQL、Cloudflare Tunnel 和本机首页。
- 成功拉取新版脚本后，自动更新 `/usr/local/sbin/clover-blog-deploy`。

脚本执行失败时会返回非零状态并指出失败阶段。GitHub 连续连接失败时不会改变当前
线上提交。

服务器 GitHub 拉取配置：

- 只读 Deploy Key 标题：`clover-blog-production-readonly`
- 私钥路径：`/home/ubuntu/.ssh/clover_blog_github_ed25519`
- SSH 别名：`github-clover-blog`
- SSH 主机：`ssh.github.com`
- SSH 端口：`443`
- Git 远程地址：`github-clover-blog:saitoasukakuku/clover-blog.git`

Deploy Key 只有仓库读取权限，不能向 GitHub 推送代码。

当前服务器已知成功执行迁移到 `0009`，并已创建超级用户。GitHub `main` 和本地
SQLite 已验证到 `0011_registrationrequest.py`；生产服务器是否已执行 `0010` 和
`0011`，需要用户在服务器运行一键部署后通过输出确认。

当前 Cloudflare Quick Tunnel 使用：

```bash
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:80
```

当前 Tunnel 使用 systemd 后台运行，退出 SSH 不会停止。Quick Tunnel 无可用性保证，
重启服务会生成新的随机地址。长期稳定部署需要自有域名和 Named Tunnel。

服务器 `.env` 已配置数据库、Django、DeepSeek、Pexels 和自动发文作者相关变量。
SMTP 邮件变量是否已配置，需要用户在生产服务器确认。只记录变量名，禁止读取、输出或
提交真实值。

## 服务器备份

- MySQL 备份脚本：`/usr/local/sbin/clover-blog-backup`
- 备份目录：`/var/backups/clover-blog`
- MySQL 客户端认证文件：`/root/.my.cnf`，权限应保持 `600`。
- 备份服务：`clover-blog-backup.service`
- 备份定时器：`clover-blog-backup.timer`
- 执行时间：每天服务器时间 `03:00`。
- `Persistent=true`，错过执行时间后会在开机时补执行。
- 备份内容：MySQL 压缩 SQL 和整个 `media/` 压缩包。
- 已手动验证数据库导出、媒体压缩和 `gzip -t` 完整性检查成功。
- 备份脚本启用了 `set -e` 和 `set -o pipefail`。
- `mysqldump` 使用 `--no-tablespaces`，不需要授予数据库用户 `PROCESS` 权限。
- 不要把数据库凭据写入仓库。

截至 2026-06-27，备份脚本已配置保留策略和磁盘告警：

- 自动删除 `/var/backups/clover-blog` 第一层目录下超过 14 天的
  `mysql_*.sql.gz` 和 `media_*.tar.gz` 备份文件。
- 自动删除范围仅限上述备份目录和文件名模式，这是用户明确授权的备份脚本例外。
- 备份前磁盘使用率达到 `80%` 时写入警告，但继续执行。
- 备份前磁盘使用率达到 `90%` 时停止备份并返回失败，避免继续写满磁盘。
- 备份后磁盘使用率达到 `80%` 时再次写入警告。

截至 2026-06-27，备份恢复演练已完成：

- 最新 MySQL 备份已通过 `gzip -t` 完整性检查。
- 最新 MySQL 备份已恢复到临时数据库验证，正式库未被覆盖。
- 临时数据库恢复后包含 16 张表、27 条 Django 迁移记录，`blog` 最新迁移为
  `0009_privatemessage_friendship_friendrequest_and_more`。
- 最新媒体备份已通过 `tar -tzf` 完整性检查。
- 最新媒体备份已解压到 `/tmp/clover-blog-media-restore-test` 验证，恢复出 2 个文件。
- 临时恢复数据库已删除；临时媒体目录保留在 `/tmp`，大小约 148K，可忽略。

## 当前线上验证状态

截至 2026-06-27 已确认：

- Nginx、Gunicorn、MySQL 和 Cloudflare Tunnel 能正常提供网站。
- HTTP 根路径当时返回 `302` 并跳转到 `/index/`；2026-07-01 本地代码已把 `/` 改为
  沉浸式欢迎页，生产是否已更新需以一键部署后的验证为准。
- Cloudflare HTTPS 首页返回 `200 OK`。
- 首页、文章详情、静态文件、头像和文章封面可以正常加载。
- Django Admin 可以登录。
- DeepSeek 和 Pexels 环境变量能被 Django 读取。
- AI 写作入口和 Pexels 封面功能已实际生成过文章和封面。
- 每日 AI 自动发文定时器已启用，下一次自动运行由 systemd timer 管理。
- 数据库迁移在线上已知执行到 `0009`；本地和 GitHub `main` 最新迁移为 `0011`，
  生产执行状态待一键部署确认。
- 管理员账号已创建。

## 尚未完成的生产事项

- 恢复 Codex SSH 部署公钥或由用户手动登录服务器运行 `sudo clover-blog-deploy`，
  部署最新 GitHub `main` 并确认迁移 `0010` 和 `0011` 已执行。
- 在生产 `/opt/clover-blog/.env` 配置 SMTP 邮件变量，并确认注册审核通过后能发送
  注册码邮件。
- Quick Tunnel 地址不稳定；长期使用应配置自有域名和 Cloudflare Named Tunnel。
- 腾讯云公网 `80` 端口仍可直接访问，后续使用稳定 Tunnel 后可评估关闭。
- Ubuntu `ufw` 当前未启用，腾讯云防火墙规则仍需在控制台单独管理。
- SSH 当前仍允许密码登录；完成密钥登录方案后可评估关闭密码登录和 root 登录。
- Django 仍为 4.2，已经超出官方支持周期；升级 Django 5.2 LTS 前必须完整测试。
- HTTPS 安全设置没有按稳定自有域名完成 HSTS 配置。

## 前端约定

- 模板主要继承 `base.html`。
- 大量页面样式仍位于模板内联 `<style>` 中。
- 修改前先确认模板继承关系和页面级脚本。
- 优先局部修改，不进行无关的大规模模板重排。
- 不要直接修改 `blog/static/plugins/` 中的第三方文件。
- 封面裁剪使用 Cropper.js，并尽量保留 PNG/WebP；JPEG 使用高质量导出。
- `/` 欢迎页背景图来自 `media/index_img/`，不要把该媒体目录提交到 Git。
- `/index/` 文章列表封面使用稳定宽高比，避免固定低分辨率拉伸造成锯齿。
- `/index/` 搜索框不再包含日期输入；不要重新添加按日期筛选，除非重新设计完整筛选体验。
- 评论和回复当前使用一层嵌套布局，不要未经设计改成无限层级。

## 已知注意事项

- `delete_post` 和 `delete_draft` 只接受 POST 请求，模板使用带 CSRF 令牌的表单；
  GET 请求返回 `405 Method Not Allowed`，不会删除文章或草稿。
- AI 写作和 AI 封面会消耗第三方 API 额度，需要保留登录限制和调用冷却。
- Pexels 图片下载必须继续限制可信域名，避免服务端请求伪造风险。
- 模板仍依赖部分 CDN；离线或网络受限时 Cropper.js 或部分图标可能不可用。
- 本地和线上数据库不同，新增迁移必须同时验证 SQLite 和线上 MySQL。
- 迁移 `0010_postfavorite_notification_and_more` 新增文章收藏和通知相关表；生产部署时必须
  确认该迁移已执行。
- 迁移 `0011_registrationrequest` 新增注册申请表；生产部署时必须确认该迁移已执行。
- 注册审核通过会发送邮件；生产环境必须配置 SMTP，不要把授权码或邮箱密码提交到 Git。
- 注册码明文只出现在发送给用户的邮件中，数据库只保存哈希；后续改动不得记录或展示
  明文注册码。
- 首页已移除日期筛选，旧的 `date` 查询参数会被后端忽略。
- 评论总数包含主评论与回复；主评论列表按时间倒序，回复按时间正序。
- 删除主评论会级联删除其全部回复。
- 好友关系使用排序后的用户 ID 保存，修改时不得破坏 `user_low < user_high` 约束。
- 私信只能在好友之间发送，不能只依赖前端隐藏按钮，必须保留后端校验。
- 收藏和通知操作不能只依赖前端显示状态，必须保留后端登录、可见性和接收者校验。
- Quick Tunnel 的 URL 不能写死到业务代码、数据库或长期文档入口中。
- 当前 `ALLOWED_HOSTS` 允许 `.trycloudflare.com` 和公网 IP。
- 当前 `CSRF_TRUSTED_ORIGINS` 允许 `https://*.trycloudflare.com`。
- 2026-06-27 当前 Codex SSH 批处理检查返回
  `Permission denied (publickey,password)`；在部署公钥或认证恢复前，助手无法直接 SSH
  登录服务器部署。

## 协作与安全规则

- 修改业务前先阅读相关模型、视图、模板、路由和测试。
- 保持现有 Django 函数视图、模板和命名风格。
- 用户希望学习时，必须明确指出修改文件、搜索位置和具体改动字段。
- 给用户发送示例代码时，每一行都要仔细解释。
- 变量命名必须前后一致、语义明确。
- 原始数据、中间值和最终值使用不同变量名。
- 不为追求简短而压缩适合初学者阅读的代码。
- 不删除或重建 `db.sqlite3`、`media/` 或服务器数据，除非用户明确要求。
- 禁止批量删除文件或目录。
- 禁止使用：
  - `del /s`
  - `rd /s`
  - `rmdir /s`
  - `Remove-Item -Recurse`
  - `rm -rf`
- 删除文件时一次只能删除一个明确路径的文件。
- 如果确实需要批量删除，应停止并让用户手动处理。
- 不覆盖、还原或删除用户已有的无关修改。
- 不提交 `.env`、密钥、密码、数据库、媒体文件或备份文件。
- 模型变更必须创建迁移，并运行迁移一致性检查。
- 功能改动应根据风险补充测试；权限、可见范围、评论和外部 API 逻辑必须测试。
