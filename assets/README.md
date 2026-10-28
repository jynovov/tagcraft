# assets 目录

| 文件 | 说明 |
|---|---|
| `tagcraft.ico` | 多尺寸图标：16/20/24/32/40/48/64/128 用 BMP 条目，256 用 PNG；编译时已嵌进 `TagCraft.exe` |
| `tagcraft_256.png` | 256px 成品图，README 顶部的徽标用它 |

## 关于图标

图标是本项目自绘的（蓝底 + 白色 TC 字标），**随代码一起按 MIT 授权**，不含任何第三方角色形象。

图标是编译期资源：`build.bat` 用 `/win32icon:assets\tagcraft.ico` 写进 exe 的 PE 资源，
程序运行时不读这个目录。如需换图标，替换 `tagcraft.ico` 后重跑 `build.bat` 即可。
