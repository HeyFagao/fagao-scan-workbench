# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Registered public story and drama lists using the standard snapshot workflow."""
from . import dianzhong_rank, fanqie_recommend, hongguo_rank, shuqi_rank, zhihu_story_rank
from .long_rankings import options_for_spec


COLLECTORS = {module.PLATFORM['id']: module for module in
              (hongguo_rank, fanqie_recommend, dianzhong_rank, shuqi_rank, zhihu_story_rank)}
PLATFORMS = {
    key: {**module.PLATFORM, 'max_limit': module.PLATFORM.get('max_limit', 100),
          'max_pages': module.PLATFORM.get('max_pages', module.PLATFORM['default_options'].get('max_pages', 10)),
          'default_options': {'limit': module.PLATFORM.get('max_limit', 100), 'max_pages': 10,
                              **module.PLATFORM['default_options']}}
    for key, module in COLLECTORS.items()
}


def collection_options(platform, supplied):
    return options_for_spec(PLATFORMS[platform], supplied)
