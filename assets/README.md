# assets 目录

| 文件 | 说明 |
|---|---|
| `source_keqing_user.jpg` | 图标素材原图（732×1200） |
| `tagcraft.ico` | 多尺寸图标：16/20/24/32/40/48/64/128 用 BMP 条目，256 用 PNG |
| `tagcraft.png` / `tagcraft_512.png` / `tagcraft_256.png` | 成品图 |
| `preview_sizes.png` | 16/20/32/48/64 小尺寸预览 |

## 换一套图标

1. 把自己的图放进本目录，命名 `source_keqing_user.jpg`（或改 `make_icon_from_image.py` 里的 `SRC`）
2. 改 `CROP = (左, 上, 右, 下)` 决定裁哪块方形区域，一般对准人脸
3. 运行：

   ```bat
   python make_icon_from_image.py
   ```

4. 重新编译 exe 把新图标嵌进去：

   ```bat
   build.bat
   ```

> ⚠️ 小尺寸**必须**写成 BMP 条目（见 `ico_util.py`）。如果用 Pillow 的
> `img.save("x.ico", sizes=[...])`，它会全部存成 PNG 格式，资源管理器加载
> 16/32 像素图标时会失败、显示成白纸。

## 版权说明

`source_keqing_user.jpg` 是《原神》角色「刻晴」的同人插画，**版权归原作者所有**。
它只作为本项目的图标使用，**不在 MIT 许可证的授权范围内**，请勿单独提取用于其他项目或商业用途。
如果原作者有异议，会立即移除并换成无版权素材。
