# assets 目录

| 文件 | 说明 |
|---|---|
| `source_deepseek_girl.jpg` | **当前**图标素材原图（1536×1536，豆包 AI 生成） |
| `source_keqing_user.jpg` | 旧版图标素材（已停用，仅作历史保留） |
| `tagcraft.ico` | 多尺寸图标：16/20/24/32/40/48/64/128 用 BMP 条目，256 用 PNG |
| `tagcraft.png` / `tagcraft_512.png` / `tagcraft_256.png` | 成品图 |
| `preview_sizes.png` | 16/20/32/48/64 小尺寸预览 |

## 换一套图标

1. 把自己的图放进本目录，改 `make_icon_from_image.py` 里的 `SRC`
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

## 版权 / 商标说明

- 当前图标 `source_deepseek_girl.jpg` 由 **豆包 AI 生成**，画面包含 **DeepSeek** 的名称与形象。
  图片版权、DeepSeek 名称与商标归各自权利人所有，**不在 MIT 许可证范围内**；
  本项目只把它作为本地图标使用，**请勿公开分发或商用**，也不代表与 DeepSeek 官方有任何关联。
- 旧素材 `source_keqing_user.jpg` 是《原神》角色「刻晴」的同人插画，**版权归原作者所有**，
  已停用，仅作历史保留；同样不在 MIT 授权范围内。
