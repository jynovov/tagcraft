# assets 目录

| 文件 | 说明 |
|---|---|
| `tagcraft.ico` | 多尺寸图标：16~128 用 BMP 条目，256 用 PNG；编译时嵌进 `TagCraft.exe` |
| `tagcraft_256.png` | 256px 成品图，README 顶部的徽标用它 |

## 关于图标

图标是 AI 生成的 DeepSeek 形象图，「DeepSeek」名称与商标归其权利人所有，
**不在 MIT 授权范围内**，请勿单独提取商用。它是编译期资源：`build.bat` 通过
`/win32icon` 写进 exe，运行时并不读取本目录。
