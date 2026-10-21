# 运行依赖与来源

- 核心项目：<https://github.com/mcncarl/jianying-headless>。公开源码预览；
  Skill 总目录或安装器不会自动提供核心运行环境。
- 剪映专业版是用户单独安装的闭源运行依赖。官方库、字体、效果包、缓存媒体和
  账号权益不随本 Skill 分发。内部接口调用不代表官方 SDK 或集成许可。
- 当前核心桥接头文件参考了 MIT 许可的
  <https://github.com/wenshui330/jy-draftc>；历史适配器曾使用 Apache-2.0 的
  <https://github.com/GuanYixuan/pyJianYingDraft>。完整声明和许可证保留在核心项目，
  不把这条历史关系描述为完全独立原创。
- FFmpeg、Python 和 Xcode 是外部工具，各自条款仍适用。
- 显式指定新字体时需在执行脚本的 Python 环境中安装核心项目的
  `requirements-fonts.txt`（fontTools 4.60.2，MIT）。默认字体流程、普通编辑和
  已有快照验证不需要此可选解析器；字体文件不随源码分发。
- 可选 ASR 需要单独安装的执行器/Skill、服务访问权限和本次调用授权；本 Skill
  不包含令牌，也不从聊天、剪贴板或浏览器自动寻找凭据。
- 可选的 `scripts/openverse_sfx.py` 会联网：经 Openverse API（`api.openverse.org`）
  检索 Freesound 来源的音效，并从 `cdn.freesound.org` 下载预览 MP3。请求只含英文
  关键词和筛选条件，不上传素材、草稿或账号信息；Openverse 与 Freesound 的服务条款
  和限流规则另行适用。不需要自动音效时不运行它。
- 这些第三方音效属于各自作者，不随本项目分发，也不在本项目许可证范围内；每个音效
  保留作者选定的 Creative Commons 许可（CC0、CC BY 或 CC BY-NC）。CC BY 与 CC BY-NC
  须按 `credits.md` 署名；CC BY-NC 禁止商用，商单、广告、带货、引流或开通收益的视频
  不能使用，须替换或加 `--commercial` 重跑。Openverse 汇总的许可信息未经核验，
  技术上取得音效不等于获得许可；发布前可打开 `credits.md` 中的原始页面复核。

代码许可证只覆盖明示有权许可的部分，不授予剪映程序库、账号或媒体素材的权利。
本 Skill 的原创部分沿用个人学习和非商业使用条款，见 [LICENSE](../LICENSE)。
商业用途须取得作者明确书面授权；第三方内容保留原有许可。单独收录本 Skill，
不改变总仓库其他目录的许可证，也不把本 Skill 标为 MIT / Apache-2.0 整包开源。
