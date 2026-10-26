# assets 目录

| 文件 | 说明 |
|---|---|
| `tagcraft.ico` | 多尺寸图标：16/20/24/32/40/48/64/128 用 BMP 条目，256 用 PNG；编译时已嵌进 `TagCraft.exe` |
| `tagcraft_256.png` | 256px 成品图，README 顶部的徽标用它 |

## 关于图标

图标是**编译期**资源：`build.bat` 用 `/win32icon:assets\tagcraft.ico` 把它写进 exe 的 PE 资源，
程序运行时并不读这个目录。

如需换图标：准备一张 512×512 以上的方图，用任意工具生成多尺寸 ICO
（小尺寸必须是 BMP 条目；Pillow 直接 `save("x.ico", sizes=[...])` 会把所有尺寸写成 PNG，
资源管理器加载 16/32 时会显示成白纸），替换 `tagcraft.ico` 后重跑 `build.bat` 即可。

## 版权说明

图标为 AI 生成的 DeepSeek 形象图，「DeepSeek」名称与商标归其权利人所有。
它只作为本项目的图标使用，**不在 MIT 许可证的授权范围内**，请勿单独提取用于其他项目或商业用途。
