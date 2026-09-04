# 项目开发约束

## 界面图标

- 新增或修改界面时统一使用 Lucide 图标库，不使用 Emoji、Unicode 字符或手绘 SVG 代替功能图标。
- 本项目通过 `app/templates/icons/lucide.html` 复用官方 `lucide-static` SVG 子集，固定版本并保留同目录的许可证；新增图标从同版本官方包导入。
- Jinja 模板使用 `icon(name)` 和 `sprite()`，避免重复定义图标路径。图标随项目本地提供，不依赖运行时外部 CDN。
- 图标使用 `currentColor` 和统一线宽，适配深浅主题。装饰图标设置 `aria-hidden="true"`，纯图标按钮提供中文 `aria-label`，点击区域至少 44×44px。
