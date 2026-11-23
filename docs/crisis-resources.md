# 危机资源：来源、核对与变更规则

危机资源是这个产品里唯一不能出错的内容：一条拨不通的号码，比没有这条号码更糟。本文件记录每一地区资源的可核对来源、最近核对日期和变更规则。

数据位置：`backend/app/content.py` 的 `CRISIS`、`CRISIS_SOURCES`、`CRISIS_VERIFIED_AT`。接口 `GET /api/crisis` 会带出 `verified_at` 与 `source`，界面上显示核对日期。

## 当前清单（核对于 2026-11-22）

| 地区 | 紧急号码 | 危机资源 | 来源 |
|---|---|---|---|
| CN 中国大陆 | 110 / 120 | 12356 全国心理援助热线（24h 免费）；北京心理危机研究与干预中心 010-82951332 / 800-810-1117；12355 青少年服务台 | [国家卫健委](https://www.nhc.gov.cn/) |
| US 美国 | 911 | 988 Suicide & Crisis Lifeline（电话或短信 988） | [988lifeline.org](https://988lifeline.org/) |
| CA 加拿大 | 911 | 988 Suicide Crisis Helpline（电话或短信 988） | [988.ca](https://988.ca/) |
| UK 英国 | 999 | Samaritans 116 123（24h 免费） | [samaritans.org](https://www.samaritans.org/) |
| AU 澳大利亚 | 000 | Lifeline Australia 13 11 14 | [lifeline.org.au](https://www.lifeline.org.au/) |
| EU 欧盟/欧洲 | 112 | 按国家查询本地热线 | [findahelpline.com](https://findahelpline.com/) |
| OTHER 其他 | 当地急救号码 | 按国家查询本地热线 | [findahelpline.com](https://findahelpline.com/) |

12356 自 2025-05-01 起全国统一启用（国家卫生健康委协调工业和信息化部设置），截至 2025 年底已接通超过 70 万通电话。findahelpline.com 由 ThroughLine 维护，覆盖 175 个以上国家，是目前最可靠的"按国家查找"入口。

## 不变式（测试会检查）

1. **每条资源必须可执行**：要么有可拨号 `number`，要么有 `url`，不能两者都没有。
2. **每个地区必须有来源**：`CRISIS_SOURCES` 必须覆盖 `REGION_KEYS` 的全部键，且为 https 链接。
3. **中英文文案同时存在**：`contact.zh` 与 `contact.en` 都不能为空。
4. **核对日期随数据下发**：`verified_at` 出现在接口响应里，界面可见。

对应测试：`tests/test_regressions.py::test_crisis_resources_carry_provenance_and_every_region_is_actionable`。

## 变更规则

- 每 **6 个月**至少复核一次，或任一号码变更时立即更新；更新后同步改 `CRISIS_VERIFIED_AT`。
- 只收公开可核实的号码；不接受第三方转述、社交媒体截图、未注明来源的聚合站。
- 新增地区时同时补：`CRISIS` 条目、`CRISIS_SOURCES` 来源、中英文文案、`REGION_KEYS` 自动跟随。
- 号码下线或无法确认时，**宁可删掉这条，也不要留一个可能打不通的号码**。
- 与产品定位相关：这里只给"可以打给谁"，不给诊断、不给用药建议、不承诺效果。

## 已知边界

- 欧盟没有统一危机热线，只能按国家查询；`EU` 条目因此只给目录，不编造号码。
- 中国大陆的 12356 由各地坐席承接，接通质量存在地区差异；北京中心号码作为补充保留。
- 第三方目录（findahelpline）本身可能调整结构，链接失效时以官方入口替代。
