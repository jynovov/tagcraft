# 50 条评测集

给「改之前 vs 改之后」用的。没有它，任何优化都只能靠感觉。

## 组成

50 条，5 组各 10 条：

| 组 | 编号 | 考什么 |
|---|---|---|
| 单人基础 | A01–A10 | 主体/发/眼/表情/动作，多人时人数 tag 必须对 |
| 服装配饰 | B11–B20 | 制服、女仆、兽耳、婚纱、泳装、冬季穿搭 |
| 场景背景 | C21–C30 | 樱花、图书馆、神社、城市、雪山、雨中 |
| 光照构图 | D31–D40 | 逆光、景深、视角、景别、留白（触发构图冲突） |
| 复杂中文词 | E41–E50 | 白墙黛瓦、太湖石、油纸伞、赛博朋克、情绪词、风格词 |

每条三个字段：

- `required` —— **必须出现**的 tag（gold），算覆盖率
- `optional` —— 出现更好，算加分
- `notes` —— 这条故意考什么

## 指标

| 指标 | 含义 |
|---|---|
| cover | required 命中率（最主要的质量分） |
| opt | optional 命中率 |
| valid | 输出里「词表不存在」的比例，越低越好 |
| order | 质量→构图→人物→动作→场景→背景→光照→风格 是否单调 |
| dup | 正向里完全重复的 tag 数 |
| calls | 模型调用次数（平均每条） |
| ms | 端到端耗时 |

## 用法

```powershell
cd C:\Users\Lenovo\Documents\deepseek-harness\default-workspace\tagcraft
$upy = "C:\Users\Lenovo\AppData\Local\Programs\Python\Python314\python.exe"

# 0) 词表校验：gold 里每个 tag 必须真的存在
& $upy -X utf8 eval\check_dataset.py

# 1) 不花钱自检（假结果验打分逻辑）
& $upy -X utf8 eval\score.py --selftest

# 2) 不花钱联调（假模型跑前 3 条，验链路）
& $upy -X utf8 eval\score.py --mock 3

# 3) 跑基线（要 API Key，50 条 × 约 10~40 s）
& $upy -X utf8 eval\score.py --tag 基线

# 4) 改完某功能再跑一次
& $upy -X utf8 eval\score.py --tag 改后 --no-reflect

# 5) 对比
& $upy -X utf8 eval\score.py --compare 基线 改后
```

结果存在 `eval/runs/<tag>.json`，`--compare` 读这两个文件。

## 注意

- **`cover` 是硬指标**，但 gold 是「必须有」而不是「只能有」，所以别拿 valid/cover 去指责风格差异。
- 复杂中文词组（E41–E50）的 gold 故意留得少，因为这些词现在大概率映射不到；做完「未命中兜底」后这一组的 cover 应该明显上升。
- 跑之前确认服务在 `127.0.0.1:8848`，`config.json` 里填了 Key。
