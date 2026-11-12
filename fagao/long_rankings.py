# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Official domestic ranking pages that supply book metadata on demand."""
from . import faloo_rank, jjwxc_rank, qimao_rank, zongheng_rank


MAX_BOOKS = 100
COLLECTORS = {module.PLATFORM['id']: module for module in (faloo_rank, qimao_rank, jjwxc_rank, zongheng_rank)}
PLATFORMS = {
    key: {**module.PLATFORM, 'max_limit': MAX_BOOKS,
          'default_options': {'limit': MAX_BOOKS, 'max_pages': 100, **module.PLATFORM['default_options']}}
    for key, module in COLLECTORS.items()
}


def collection_options(platform, supplied):
    """Use identical effective options for direct CLI calls and shared background jobs."""
    from .store import AppError

    spec = PLATFORMS[platform]
    if not isinstance(supplied, dict):
        raise AppError('invalid_options', '采集条件应为一个对象。')
    if supplied.get('mode') not in (None, 'long'):
        raise AppError('invalid_mode', '此平台提供长篇榜单。')
    defaults = spec['default_options']
    options = {'mode': 'long'}
    for field, choices in (('channel', 'channels'), ('category', 'categories'), ('board', 'boards')):
        value = supplied.get(field)
        if value is None or value == '':
            value = defaults[field]
        if not isinstance(value, str) or value not in spec[choices]:
            raise AppError('invalid_options', f"请选择{spec['name']}提供的频道、分类和榜型。")
        options[field] = value

    fixed_period = spec.get('board_periods', {}).get(options['board'])
    period = supplied.get('period')
    if period is None or period == '':
        period = fixed_period or defaults.get('period')
    if period is not None:
        choices = [fixed_period] if fixed_period else spec.get('periods', [defaults.get('period')])
        if not isinstance(period, str) or period not in choices:
            raise AppError('invalid_options', f"请选择{spec['name']}该榜提供的周期。")
        options['period'] = period
    for field, cap in (('limit', MAX_BOOKS), ('max_pages', 100)):
        value = supplied.get(field)
        if value is None:
            value = defaults[field]
        if type(value) is not int or not 1 <= value <= 10000:
            raise AppError('invalid_options', '采集数量和页数应为 1 到 10000 的整数。')
        options[field] = min(value, cap)
    return options
