"""UI translations.

English text is the key. To add a language: add it to LANGUAGES and add a
dictionary to CATALOGS with the same keys (missing keys fall back to English).
"""

LANGUAGES = {"en": "English", "zh": "中文"}
DEFAULT_LANG = "en"

# Strings used by static/forms.js and inline scripts (sent to the page as window.I18N).
JS_STRINGS = [
    "Your browser can't preview this format, so no frame was captured. Pick an image below, or a placeholder will be shown.",
    "Play or seek the video first, then capture a frame.",
    "Type a name for the new category.",
    "Uploading… {pct}%",
    "Processing…",
    "Something went wrong (HTTP {status}).",
    "Network error - the upload did not finish.",
    "Starting…",
    "Upload in progress",
    "Show",
    "Hide",
    "Copy",
    "Copied",
]

ZH = {
    # header / navigation
    "Search videos…": "搜索视频…",
    "Upload": "上传",
    "Categories": "分类",
    "Log out": "退出登录",
    "Admin login": "管理员登录",
    "Language": "语言",

    # library page
    "All": "全部",
    "Uncategorized": "未分类",
    "All videos": "全部视频",
    "matching “{q}”": "匹配“{q}”",
    "Sort": "排序",
    "Newest first": "最新上传",
    "Oldest first": "最早上传",
    "Title A–Z": "标题 A–Z",
    "Upload date": "上传日期",
    "No videos found.": "没有找到视频。",
    "Show all videos": "显示全部视频",
    "Your library is empty.": "视频库还是空的。",
    "Upload your first video": "上传第一个视频",
    "No videos yet.": "还没有视频。",

    # video page
    "Your browser can't play this format ({ext}). Download the file to watch it.":
        "你的浏览器无法播放此格式（{ext}）。请下载文件后观看。",
    "Uploaded {date}": "上传于 {date}",
    "Download": "下载",
    "Edit": "编辑",
    "Delete": "删除",
    "Delete this video permanently? This cannot be undone.": "确定永久删除这个视频吗？此操作无法撤销。",
    "File: {name}": "文件：{name}",
    "More in {category}": "更多「{category}」视频",

    # login
    "Log in": "登录",
    "Log in to upload, edit, and delete videos.": "登录后可以上传、编辑和删除视频。",
    "Username": "用户名",
    "Password": "密码",

    # upload / edit
    "Category": "分类",
    "New category…": "新建分类…",
    "New category name": "新分类名称",
    "e.g. Mathematics": "例如：数学",
    "Upload a video": "上传视频",
    "Choose a video file": "选择视频文件",
    "or drag it here": "或拖放到这里",
    "Max {n} GB": "最大 {n} GB",
    "Title": "标题",
    "Defaults to the file name": "默认使用文件名",
    "Description": "简介",
    "Optional notes about this video": "关于此视频的说明（可选）",
    "Thumbnail": "缩略图",
    "A frame from the video is captured automatically.": "会自动从视频中截取一帧作为缩略图。",
    "Or use your own image": "或使用自己的图片",
    "Edit video": "编辑视频",
    "No thumbnail yet.": "还没有缩略图。",
    "Use current frame as thumbnail": "用当前画面作为缩略图",
    "Or upload an image": "或上传图片",
    "Remove thumbnail": "移除缩略图",
    "Save changes": "保存修改",
    "Cancel": "取消",

    # categories page
    "Add": "添加",
    "Rename": "重命名",
    "1 video": "1 个视频",
    "{n} videos": "{n} 个视频",
    "Delete category “{name}”? Its videos will become uncategorized.": "确定删除分类“{name}”吗？其中的视频将变为未分类。",
    "No categories yet. Add one above, or create one while uploading.": "还没有分类。可以在上方添加，或在上传时新建。",

    # misc pages
    "Back to library": "返回视频库",
    "Not found": "未找到",
    "That page or video doesn't exist.": "该页面或视频不存在。",

    # API page
    "Upload API": "上传 API",
    "Upload videos directly from scripts or other programs, without opening the website.":
        "无需打开网站，即可通过脚本或其他程序直接上传视频。",
    "Your API token": "你的 API 令牌",
    "Keep it secret - anyone with it can upload and delete videos.": "请妥善保管——拿到令牌的人可以上传和删除视频。",
    "Create a new token? The current token will stop working.": "确定生成新令牌吗？当前令牌将失效。",
    "The API is off.": "API 已关闭。",
    "Turn it on to get a randomly generated secret API URL and token. Nothing is reachable through the API until then.":
        "开启后会随机生成一个保密的 API 地址和令牌。开启之前，无法通过 API 访问任何内容。",
    "Turn on API and generate URL": "开启 API 并生成地址",
    "Your API URL": "你的 API 地址",
    "This address is random and secret. Anyone who has it can see the video list through the API, so share it only with programs you trust.":
        "这个地址是随机生成且保密的。拿到地址的人可以通过 API 查看视频列表，请只提供给你信任的程序。",
    "Generate a new URL? The current URL will stop working.": "确定生成新地址吗？当前地址将失效。",
    "New URL": "生成新地址",
    "Uploading, editing and deleting also need this token in the header": "上传、编辑和删除还需要在请求头中带上此令牌",
    "New token": "生成新令牌",
    "Show real URL and token in examples": "在示例中显示真实地址和令牌",
    "All paths are relative to your API URL.": "以下路径都接在你的 API 地址后面。",
    "Turn off API": "关闭 API",
    "Turning the API off deletes the URL and token. Turning it on again creates new ones.":
        "关闭 API 会删除当前地址和令牌，重新开启时会生成新的。",
    "Turn off the API? Programs using it will stop working.": "确定关闭 API 吗？正在使用它的程序将无法工作。",

    # password page
    "Password": "密码",
    "Change password": "修改密码",
    "You are still using the default password. Please change it.": "你仍在使用默认密码，请尽快修改。",
    "Username: {user}": "用户名：{user}",
    "Current password": "当前密码",
    "New password": "新密码",
    "Repeat new password": "再次输入新密码",
    "At least {n} characters. Changing it logs out all other devices.": "至少 {n} 个字符。修改后，其他设备会被退出登录。",
    "Only the file is required. A category that does not exist yet is created automatically.":
        "只有文件是必填项。不存在的分类会自动创建。",
    "Thumbnail and duration are generated on the server.": "缩略图和时长由服务器自动生成。",
    "ffmpeg is not installed, so send a thumbnail image yourself if you want one (the Docker image includes ffmpeg).":
        "服务器未安装 ffmpeg，如需缩略图请自行上传图片（Docker 镜像已包含 ffmpeg）。",
    "All endpoints": "全部接口",
    "Request": "请求",
    "Token": "令牌",
    "What it does": "说明",
    "Upload (multipart form): file, title, description, category or category_id, thumbnail":
        "上传（multipart 表单）：file、title、description、category 或 category_id、thumbnail",
    "List videos. Optional: ?category=&lt;id&gt;|none, q, sort=new|old|title, limit, offset":
        "视频列表。可选参数：?category=&lt;id&gt;|none、q、sort=new|old|title、limit、offset",
    "One video, with stream / download / thumbnail links": "单个视频，包含播放 / 下载 / 缩略图链接",
    "Change title, description, category or category_id (JSON)": "修改 title、description、category 或 category_id（JSON）",
    "Delete a video and its files": "删除视频及其文件",
    "List categories with video counts": "分类列表及视频数量",
    'Create a category: {"name": "..."}': '创建分类：{"name": "..."}',
    "Example response": "响应示例",

    # flash / error messages
    "Please log in again.": "请重新登录。",
    "Form expired - reload the page and try again.": "表单已过期，请刷新页面后重试。",
    "Please choose a video file.": "请选择一个视频文件。",
    "'.{ext}' is not a supported video type.": "不支持“.{ext}”格式的视频。",
    "Logged in. You can now upload and edit videos.": "登录成功，现在可以上传和编辑视频了。",
    "Wrong username or password.": "用户名或密码错误。",
    "Logged out.": "已退出登录。",
    "Uploaded “{title}”.": "已上传“{title}”。",
    "Title can't be empty.": "标题不能为空。",
    "Changes saved.": "修改已保存。",
    "Deleted “{title}”.": "已删除“{title}”。",
    "Category name can't be empty.": "分类名称不能为空。",
    "Added category “{name}”.": "已添加分类“{name}”。",
    "A category named “{name}” already exists.": "名为“{name}”的分类已存在。",
    "Category renamed.": "分类已重命名。",
    "Category deleted. Its videos are now uncategorized.": "分类已删除，其中的视频已变为未分类。",
    "New API token created. The old token no longer works.": "已生成新的 API 令牌，旧令牌已失效。",
    "API turned on with a new random URL and token.": "API 已开启，并生成了新的随机地址和令牌。",
    "New API URL created. The old URL no longer works.": "已生成新的 API 地址，旧地址已失效。",
    "API turned off.": "API 已关闭。",
    "Current password is wrong.": "当前密码不正确。",
    "The new password must be at least {n} characters.": "新密码至少需要 {n} 个字符。",
    "The two new passwords don't match.": "两次输入的新密码不一致。",
    "The new password is the same as the current one.": "新密码与当前密码相同。",
    "Password changed. Other devices have been logged out.": "密码已修改，其他设备已退出登录。",
    "File is too large (limit is {gb} GB).": "文件太大（上限为 {gb} GB）。",

    # JavaScript
    "Your browser can't preview this format, so no frame was captured. Pick an image below, or a placeholder will be shown.":
        "浏览器无法预览此格式，未能截取画面。可以在下方选择图片，否则将显示占位图。",
    "Play or seek the video first, then capture a frame.": "请先播放或拖动视频，再截取画面。",
    "Type a name for the new category.": "请输入新分类的名称。",
    "Uploading… {pct}%": "上传中… {pct}%",
    "Processing…": "处理中…",
    "Something went wrong (HTTP {status}).": "出错了（HTTP {status}）。",
    "Network error - the upload did not finish.": "网络错误，上传未完成。",
    "Starting…": "准备中…",
    "Upload in progress": "正在上传",
    "Show": "显示",
    "Hide": "隐藏",
    "Copy": "复制",
    "Copied": "已复制",
}

CATALOGS = {"zh": ZH}


def translate(text, lang, **kwargs):
    out = CATALOGS.get(lang, {}).get(text, text)
    return out.format(**kwargs) if kwargs else out
