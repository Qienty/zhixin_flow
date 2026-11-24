"""HTTP API（纯标准库实现，无第三方依赖）。

接口清单见 README.md；路由按领域拆在 routes/ 下，导入顺序就是匹配顺序。
本文件只做对外出口，保持 `from app.api import Request, Response, dispatch, parse_query` 不变。
"""

from __future__ import annotations

from .core import ApiError, PUBLIC_PATHS, Request, Response, Router, WINDOW_DAYS, dispatch, parse_query, router
from .helpers import _csv_cell, _dates, _normalize_time

# 顺序敏感：下面这些导入的先后就是路由匹配顺序（先注册的先用）。
# 例如 /api/entries 必须排在 /api/entries/{id} 之前；不要排序、不要改成一行括号导入。
from .routes import meta  # noqa: F401  启动 / 首页 / 导入 / 健康 / 元数据
from .routes import entries  # noqa: F401  情绪记录增删改查
from .routes import interventions  # noqa: F401  引导内容、引导结果、就诊摘要、模型自检
from .routes import expression_ai  # noqa: F401  表达空间的 AI 整理与下一步
from .routes import practices  # noqa: F401  自助练习与每周证据卡
from .routes import insights  # noqa: F401  统计、规则归因与 AI 分析
from .routes import account  # noqa: F401  注册登录、兑换码、账号设置、危机资源
from .routes import wheel  # noqa: F401  缓解转盘
from .routes import exporting  # noqa: F401  导出
from .routes import demo  # noqa: F401  演示数据

__all__ = [
    "ApiError",
    "PUBLIC_PATHS",
    "Request",
    "Response",
    "Router",
    "WINDOW_DAYS",
    "dispatch",
    "parse_query",
    "router",
]
