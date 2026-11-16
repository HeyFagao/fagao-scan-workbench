# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
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
