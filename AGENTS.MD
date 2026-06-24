# AGENTS.md

## 项目概览

- 项目名：白车轴草
- 项目类型：Django 4.2 多用户博客
- 本地工作区：`E:\projects\django-projects\白车轴草`
- Django 工程根目录：`E:\projects\django-projects\白车轴草\白车轴草`
- GitHub 仓库：`https://github.com/saitoasukakuku/clover-blog`
- 默认分支：`main`
- 线上服务器：`192.168.20.100`
- 服务器项目目录：`/opt/clover-blog`
- 线上访问地址：`http://192.168.20.100/`

仓库根目录用于 Git、依赖清单和协作文档，真正的 Django 工程位于
`白车轴草/` 子目录。核心业务集中在单个 `blog` 应用中。

## 当前技术栈

- Python 3
- Django 4.2
- 本地数据库：SQLite
- 线上数据库：MySQL 8，通过 `.env` 配置
- Gunicorn
- Nginx
- WhiteNoise
- Pillow
- python-dotenv
- Bootstrap 5
- jQuery
- Font Awesome
- Cropper.js
- DeepSeek API
- Pexels API

依赖清单位于仓库根目录的 `requirements.txt`。

## 目录结构

- `AGENTS.md`：项目状态、协作规则和运维说明。
- `requirements.txt`：Python 依赖清单。
- `白车轴草/manage.py`：Django 管理命令入口。
- `白车轴草/白车轴草/settings.py`：项目配置和数据库选择逻辑。
- `白车轴草/白车轴草/urls.py`：项目路由。
- `白车轴草/blog/models.py`：文章、用户资料、评论和回复模型。
- `白车轴草/blog/forms.py`：注册、登录、用户资料和评论表单。
- `白车轴草/blog/views.py`：主要业务视图。
- `白车轴草/blog/admin.py`：文章、用户资料和评论后台配置。
- `白车轴草/blog/site_owner.py`：站点博主账号选择逻辑。
- `白车轴草/blog/context_processors.py`：页脚社交链接上下文。
- `白车轴草/blog/templates/`：全部 Django 模板。
- `白车轴草/blog/static/`：本地 JS、Bootstrap、Font Awesome 等资源。
- `白车轴草/blog/management/commands/create_startup_post.py`：AI 自动发文命令。
- `白车轴草/blog/migrations/`：数据库迁移，当前最新为
  `0008_comment_parent.py`。
- `白车轴草/media/`：本地上传文件，已被 Git 忽略。
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

## 用户与文章权限

### 首页可见范围

- 游客只能看到 `published + public` 的文章。
- 登录用户可以看到所有公开已发布文章，以及自己的全部已发布文章。
- 登录用户可以点击“只看我的文章”筛选自己的文章。
- 首页支持作者、日期、分类和关键词组合筛选。
- 文章卡片中的作者、发布日期和分类均可点击筛选。
- 作者筛选也会同步影响右侧“最近文章”和分类统计。

### 文章详情

- 游客只能访问公开且已发布的文章。
- 登录用户可以访问公开已发布文章，以及自己的文章。
- 只有文章作者可以编辑或删除自己的文章。
- 编辑表单支持修改状态、可见范围、分类、标签、正文和封面。

### 评论与回复

- 只有公开且已发布的文章开放评论和回复。
- 只有登录用户可以发表评论或回复。
- 私密文章和草稿不显示评论区，也不能通过伪造请求评论。
- 评论者可以删除自己的评论或回复。
- 文章作者可以删除自己文章下的任意评论或回复。
- 无关用户不能删除他人的评论。
- 删除评论使用 POST 请求，GET 请求会被拒绝。
- 评论和回复总数显示在文章详情页。

## 已实现功能

### 账号与资料

- 用户注册、登录、退出。
- 用户中心。
- 头像上传和裁剪。
- 昵称、简介、邮箱、GitHub、微博资料。
- 页脚社交链接使用站点博主账号资料。

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
- 首页关键词搜索。
- 日期、分类、作者筛选。
- “只看我的文章”筛选。
- 分页。
- 最近文章。
- 浏览量统计。
- RSS。

### 首页“关于博主”

- 登录后显示当前登录用户的头像、昵称、简介、已发布数量和总浏览量。
- 未登录时显示站点博主资料。
- 站点博主优先使用用户名为 `root` 的账号；不存在时使用第一个超级用户。

### 评论

- 公开文章评论。
- 一层评论回复。
- 评论和回复头像、昵称、时间显示。
- 评论者和文章作者删除权限。
- Django Admin 评论管理。

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

服务器定时任务当前设计为每天 09:00 执行一次：

- `clover-blog-startup-post.service`
- `clover-blog-startup-post.timer`

定时任务属于服务器配置，不在本地仓库内。

## 路由

主要路由位于 `白车轴草/白车轴草/urls.py`：

- `/`：跳转首页。
- `/admin/`：Django Admin。
- `/index/`：首页。
- `/register/`：注册。
- `/login/`：登录。
- `/logout/`：退出。
- `/user_center/`：用户中心。
- `/index/create_post/`：创建文章。
- `/index/create_post/ai/`：AI 写作 POST 接口。
- `/drafts/`：草稿箱。
- `/edit_post/<post_id>/`：编辑文章。
- `/delete_draft/<post_id>/`：删除草稿。
- `/post/<post_id>/`：文章详情。
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

文档只能记录变量名，不得记录真实密钥或密码。

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

- `Found 48 test(s)`
- `Ran 48 tests`
- `OK`

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

- 系统：CentOS 7
- 项目目录：`/opt/clover-blog`
- 虚拟环境：`/opt/clover-blog/.venv38`
- Gunicorn 服务：`clover-blog.service`
- Nginx 服务：`nginx`
- MySQL 服务：`mysqld.service`

部署流程：

```bash
ssh root@192.168.20.100
cd /opt/clover-blog
source .venv38/bin/activate
/opt/clover-blog/deploy.sh
```

部署脚本预期执行：

```text
git pull
pip install -r requirements.txt
python 白车轴草/manage.py migrate
python 白车轴草/manage.py collectstatic --noinput
systemctl restart clover-blog
```

评论回复功能新增了 `0008_comment_parent.py`，服务器部署时必须成功执行
`migrate`。

Nginx 曾因 SELinux 无法连接 Gunicorn 出现 502，服务器已通过以下设置修复：

```bash
setsebool -P httpd_can_network_connect 1
```

## 服务器备份

- MySQL 备份脚本：`/usr/local/sbin/clover-blog-backup`
- 备份目录：`/var/backups/clover-blog`
- 已配置 systemd 定时备份任务。
- 备份脚本应从服务器安全配置读取 MySQL 登录信息。
- 不要把数据库凭据写入仓库。

服务器曾保留 SQLite 迁移备份：

- `/root/clover-blog-db.sqlite3.backup`
- `/root/clover-blog-data.json`

## 前端约定

- 模板主要继承 `base.html`。
- 大量页面样式仍位于模板内联 `<style>` 中。
- 修改前先确认模板继承关系和页面级脚本。
- 优先局部修改，不进行无关的大规模模板重排。
- 不要直接修改 `blog/static/plugins/` 中的第三方文件。
- 封面裁剪使用 Cropper.js，并尽量保留 PNG/WebP；JPEG 使用高质量导出。
- 首页封面使用稳定宽高比，避免固定低分辨率拉伸造成锯齿。
- 评论和回复当前使用一层嵌套布局，不要未经设计改成无限层级。

## 已知注意事项

- `delete_post` 和 `delete_draft` 当前仍通过普通链接触发删除，不是 POST 表单；
  后续可改进为只接受 POST。
- AI 写作和 AI 封面会消耗第三方 API 额度，需要保留登录限制和调用冷却。
- Pexels 图片下载必须继续限制可信域名，避免服务端请求伪造风险。
- 模板仍依赖部分 CDN；离线或网络受限时 Cropper.js 或部分图标可能不可用。
- 本地和线上数据库不同，新增迁移必须同时验证 SQLite 和线上 MySQL。
- 评论总数包含主评论与回复；主评论列表按时间倒序，回复按时间正序。
- 删除主评论会级联删除其全部回复。

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
