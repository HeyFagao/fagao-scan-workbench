# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""The mobile ranking menu exposes these male-channel boards and subject filters."""

BOARDS = {
    "月票榜": "yuepiao", "畅销榜": "hotsales", "签约新书榜": "sign", "公众新书榜": "newbook",
    "新人新书榜": "newauthor", "推荐榜": "rec", "阅读榜": "readindex", "书友榜": "newfans", "更新榜": "update",
}
CATEGORIES = {
    "全部": -1, "玄幻": 21, "奇幻": 1, "武侠": 2, "仙侠": 22, "都市": 4, "现实": 15,
    "军事": 6, "历史": 5, "游戏": 7, "体育": 8, "科幻": 9, "悬疑灵异": 10, "诸天无限": 20109, "轻小说": 12,
}
PLATFORM = {
    "id": "qidian", "name": "起点中文网", "mode": "long", "status": "available",
    "channels": ["男频"], "categories": list(CATEGORIES), "boards": list(BOARDS),
    "message": "男频移动榜单，按原站题材和名次展示",
    "default_options": {"channel": "男频", "category": "全部", "board": "月票榜"},
}
