# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Period combinations listed on the publisher's book-ranking pages."""
PERIOD_PATHS = {"周榜": "week", "月榜": "month", "总榜": "total"}
BOARDS = {
    "月票榜": ("yp", ("月榜", "总榜")),
    "新书榜": ("yp_new", ("月榜",)),
    "点击榜": ("no-vip-click", ("周榜", "月榜")),
    "收藏榜": ("favor", ("周榜", "月榜", "总榜")),
    "推荐榜": ("recommend", ("周榜", "月榜", "总榜")),
    "订阅榜": ("buy", ("周榜", "月榜", "总榜")),
    "吐槽榜": ("tsukkomi", ("周榜", "月榜", "总榜")),
    "刀片榜": ("blade", ("月榜", "总榜")),
    "更新榜": ("get-update-most", ("周榜", "月榜", "总榜")),
}

PLATFORM = {
    "id": "ciweimao", "name": "刺猬猫", "mode": "long", "status": "available",
    "message": "按原站榜型和周期查看作品",
    "channels": ["综合"], "categories": ["全部"], "boards": list(BOARDS),
    "periods": list(PERIOD_PATHS),
    "periods_by_board": {name: list(periods) for name, (_, periods) in BOARDS.items()},
    "default_options": {"channel": "综合", "category": "全部", "board": "月票榜", "period": "月榜"},
}


def rank_scope(board, period):
    if board not in BOARDS:
        raise ValueError("请选择刺猬猫提供的榜型。")
    slug, periods = BOARDS[board]
    period = period or periods[0]
    if period not in periods:
        raise ValueError("此刺猬猫榜型仅提供" + "、".join(periods) + "。")
    return period, f"https://www.ciweimao.com/rank-index/{slug}-{PERIOD_PATHS[period]}"
