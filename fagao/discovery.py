# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Registered public story and drama lists using the standard snapshot workflow."""
from . import dianzhong_rank, fanqie_recommend, hongguo_rank, shuqi_rank
from .long_rankings import options_for_spec


COLLECTORS = {module.PLATFORM['id']: module for module in
              (hongguo_rank, fanqie_recommend, dianzhong_rank, shuqi_rank)}
PLATFORMS = {
    key: {**module.PLATFORM, 'max_limit': module.PLATFORM.get('max_limit', 100),
          'max_pages': module.PLATFORM.get('max_pages', module.PLATFORM['default_options'].get('max_pages', 10)),
          'default_options': {'limit': module.PLATFORM.get('max_limit', 100), 'max_pages': 10,
                              **module.PLATFORM['default_options']}}
    for key, module in COLLECTORS.items()
}


def collection_options(platform, supplied):
    return options_for_spec(PLATFORMS[platform], supplied)
