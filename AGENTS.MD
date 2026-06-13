# AGENTS.md

## 项目概览

这是一个 Django 4.2 博客项目，项目名为“白车轴草”。项目当前以单个 `blog` 应用为核心，提供文章发布、草稿管理、文章详情、编辑和删除等基础博客功能。

仓库根目录下真正的 Django 工程位于 `白车轴草/` 子目录。根目录目前主要用于承载项目说明和外层工作区文件。

## 目录结构

- `白车轴草/manage.py`：Django 管理命令入口。
- `白车轴草/白车轴草/`：Django 项目配置包，包含 `settings.py`、`urls.py`、`asgi.py`、`wsgi.py`。
- `白车轴草/blog/`：博客业务应用，包含模型、视图、模板、迁移和静态资源。
- `白车轴草/blog/templates/`：页面模板，包括首页、文章详情、草稿箱和文章创建/编辑页。
- `白车轴草/blog/static/`：本地静态资源，包含图片、jQuery、Bootstrap、Font Awesome 等插件资源。
- `白车轴草/db.sqlite3`：开发用 SQLite 数据库，当前已存在。
- `白车轴草/media/`：上传媒体文件目录，封面图默认存放在 `media/covers/`。

## 主要功能

- 文章列表：首页 `/index/` 展示状态为 `published` 的文章，并按创建时间倒序排列。
- 文章详情：`/post/<id>/` 展示单篇文章内容、分类、标签和时间信息。
- 创建文章：`/index/create_post/` 支持填写标题、分类、标签、正文和封面图。
- 草稿箱：`/drafts/` 展示状态为 `draft` 的文章，并按更新时间倒序排列。
- 编辑文章：`/edit_post/<id>/` 可更新标题、分类、标签、正文、封面和发布状态。
- 删除草稿：`/delete_draft/<id>/` 只删除草稿状态文章。
- 删除已发布文章：`/post/<id>/delete/` 只删除已发布状态文章。
- 封面上传与裁剪：创建和编辑页面使用 Cropper.js 将封面裁剪后以 base64 提交到后端保存。

## 数据模型

核心模型是 `blog.models.Post`：

- `title`：文章标题，最大长度 200。
- `category`：文章分类，最大长度 50。
- `tags`：文章标签，最大长度 200，可为空。
- `cover`：封面图片，上传到 `covers/`，可为空。
- `content`：文章正文。
- `status`：文章状态，可选值为 `draft`（草稿）和 `published`（已发布），默认 `draft`。
- `created_at`：创建时间，自动写入。
- `updated_at`：更新时间，自动更新。

## 路由说明

主要路由集中在 `白车轴草/白车轴草/urls.py`：

- `/admin/`：Django 管理后台。
- `/index/`：文章首页，路由名 `index`。
- `/index/create_post/`：创建文章，路由名 `create_post`。
- `/drafts/`：草稿箱，路由名 `drafts`。
- `/edit_post/<int:post_id>/`：编辑文章，路由名 `edit_post`。
- `/delete_draft/<int:post_id>/`：删除草稿，路由名 `delete_draft`。
- `/post/<int:post_id>/`：文章详情，路由名 `post_detail`。
- `/post/<int:post_id>/delete/`：删除已发布文章，路由名 `delete_post`。

`DEBUG=True` 时，项目会通过 `static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)` 暴露媒体文件。

## 前端与静态资源

- 页面主要使用 Django Template 编写。
- 基础样式大量写在模板内联 `<style>` 中，整体视觉是绿色系博客风格。
- 本地静态资源包括：
  - `blog/static/js/jquery-3.7.1.js`
  - `blog/static/plugins/bootstrap-5.3.8-dist/`
  - `blog/static/plugins/fontawesome-free-7.1.0-web/`
- 模板中还通过 CDN 引入：
  - Font Awesome 6.4.0
  - Cropper.js 1.5.13
- `base.html` 提供全站导航、页脚和通用样式；部分页面仍包含重复的完整 HTML 或页面级样式，修改前应先确认当前模板继承关系。

## 开发运行

从仓库根目录运行 Django 命令：

```powershell
python .\白车轴草\manage.py check
python .\白车轴草\manage.py runserver
```

当前环境中 `python .\白车轴草\manage.py check` 可以通过，输出为 `System check identified no issues (0 silenced).`

## 已知注意事项

- 当前仓库内没有发现 `requirements.txt`、`pyproject.toml`、`Pipfile`、`poetry.lock` 或 `setup.py` 等依赖清单文件。
- 当前已安装/可用的 Django 版本为 4.2。
- SQLite 数据库 `白车轴草/db.sqlite3` 已存在，并且已有文章数据。
- `Post` 没有注册到 `blog/admin.py`，因此默认管理后台不会直接显示该模型。
- 部分 PowerShell 命令直接读取中文文件时可能显示乱码；按 UTF-8 读取文件内容时中文正常。
- 模板中存在外部 CDN 依赖，离线或网络受限时 Cropper.js、部分 Font Awesome CDN 资源可能不可用。
- 视图中多处使用 `Post.objects.get(id=post_id)`，不存在对应文章时会抛出异常；当前文档只记录现状，不修复该行为。

## 协作约定

- 修改业务功能前，先阅读 `blog/models.py`、`blog/views.py` 和相关模板，保持现有 Django 函数视图与模板风格。
- 不要随意改动 `blog/static/plugins/` 下的第三方插件文件，除非任务明确要求升级或替换依赖。
- 不要随意删除或重建 `db.sqlite3`、`media/`、`media/covers/` 中的数据文件，除非用户明确要求。
- 进行前端改动时注意模板内联 CSS 较多，优先局部调整，避免无关的大规模重排。
- 新增模型字段或修改字段定义时，需要同步创建/更新迁移文件，并确认 SQLite 开发库可正常迁移。
