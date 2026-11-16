# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Official domestic ranking pages that supply book metadata on demand."""
from . import faloo_rank, jjwxc_rank, qimao_rank, zongheng_rank


MAX_BOOKS = 100
COLLECTORS = {module.PLATFORM['id']: module for module in (faloo_rank, qimao_rank, jjwxc_rank, zongheng_rank)}
PLATFORMS = {
    key: {**module.PLATFORM, 'max_limit': module.PLATFORM.get('max_limit', MAX_BOOKS),
          'default_options': {'limit': module.PLATFORM.get('max_limit', MAX_BOOKS),
                              'max_pages': module.PLATFORM.get('max_pages', 100),
                              **module.PLATFORM['default_options']}}
    for key, module in COLLECTORS.items()
}


def collection_options(platform, supplied):
    """Use identical effective options for direct CLI calls and shared background jobs."""
    return options_for_spec(PLATFORMS[platform], supplied)


def options_for_spec(spec, supplied):
    """Validate selected source filters before a server or CLI job is started."""
    from .store import AppError

    if not isinstance(supplied, dict):
        raise AppError('invalid_options', '采集条件应为一个对象。')
    if supplied.get('mode') not in (None, spec['mode']):
        raise AppError('invalid_mode', '作品类型与采集平台不一致。')
    defaults = spec['default_options']
    options = {'mode': spec['mode']}
    for field in ('channel', 'board'):
        if field == 'channel':
            choices = spec['channels']
        else:
            choices = spec.get('boards_by_channel', {}).get(options['channel'], spec['boards'])
        value = supplied.get(field)
        if value is None or value == '':
            value = defaults[field] if defaults[field] in choices else choices[0]
        if not isinstance(value, str) or value not in choices:
            raise AppError('invalid_options', f"请选择{spec['name']}提供的频道、分类和榜型。")
        options[field] = value

    board = options['board']
    categories = spec.get('categories_by_channel', {}).get(options['channel'], spec['categories'])
    categories = spec.get('categories_by_board', {}).get(board, categories)
    category = supplied.get('category')
    if category is None or category == '':
        category = defaults['category'] if defaults['category'] in categories else categories[0]
    if not isinstance(category, str) or category not in categories:
        raise AppError('invalid_options', f"请选择{spec['name']}该榜提供的分类。")
    options['category'] = category

    fixed_period = spec.get('board_periods', {}).get(board)
    periods = spec.get('periods_by_board', {}).get(board)
    if periods is None:
        periods = [fixed_period] if fixed_period else spec.get('periods', [])
    period = supplied.get('period')
    if period is None or period == '':
        period = defaults.get('period') if defaults.get('period') in periods else next(iter(periods), None)
    if period is not None:
        if not isinstance(period, str) or period not in periods:
            raise AppError('invalid_options', f"请选择{spec['name']}该榜提供的周期。")
        options['period'] = period
    for field, cap in (('limit', spec.get('max_limit', MAX_BOOKS)), ('max_pages', spec.get('max_pages', 100))):
        value = supplied.get(field)
        if value is None:
            value = defaults[field]
        if type(value) is not int or not 1 <= value <= 10000:
            raise AppError('invalid_options', '采集数量和页数应为 1 到 10000 的整数。')
        options[field] = min(value, cap)
    return options
