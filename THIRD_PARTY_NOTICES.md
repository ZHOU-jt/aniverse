# 数据、素材与依赖说明

1. **番剧元数据**：来自 [AniList](https://anilist.co/) 的公开 [GraphQL API](https://docs.anilist.co/guide/graphql/)。`src/data/anime_seed.json` 是 2026-10-22 提取的公开番剧表快照；`classics-anilist.json` 是此前抓取的 200 条经典/热门作品响应。它们不包含用户、邮件验证码、帖子或评价表。封面保留远程 URL，没有批量下载或转存封面。
2. **中文译名**：来自 [bangumi-data](https://github.com/bangumi-data/bangumi-data)，数据采用 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。`title_names.json` 保留来源与许可说明，按精确 MyAnimeList ID 提取了 197 条匹配译名；未分发完整原始数据集。网页页脚已署名。本项目对这部分数据的处理为筛选与字段映射。
3. **界面图片**：`src/static/images/` 保留项目现有展示插画。素材与动画作品、角色、远程封面的权利归相应权利人；本作业包不将第三方素材或数据统一声明为原创代码许可。
4. **Python 依赖**：Flask、Werkzeug、Jinja2、Requests、python-dotenv、Waitress 等由各自项目许可约束；它们通过包管理器安装，未将虚拟环境复制入 ZIP。完整复现版本见 `src/requirements-lock.txt`。
5. **演示与开发工具**：随包账号、评价、帖子与回复由 `setup_demo.py` 独立生成，不来自线上用户。开发、检查和材料整理使用过 AI 辅助编程；测试结果与截图均来自实际运行，未补造小组成员或协作照片。
6. **官方观看渠道**：`src/data/watch_channels.json` 按精确 MAL ID 筛选 bangumi-data 中的站点映射（CC BY 4.0），并在平台官方页面核对作品、季别或年份，补充核验日期和依据链接。该处理属于筛选、字段转换和人工核对；原始映射不代表当前授权或可用性。链接仅前往第三方页面，平台标识文字不表示本项目与平台存在合作关系。会员、地区和版权状态以平台实际展示为准。

参考文档：

- [Flask / Waitress 部署说明](https://flask.palletsprojects.com/en/stable/deploying/waitress/)
- [Python smtplib](https://docs.python.org/3/library/smtplib.html)
- [AniList GraphQL 入门](https://docs.anilist.co/guide/graphql/)
- [bangumi-data 数据与许可](https://github.com/bangumi-data/bangumi-data)
