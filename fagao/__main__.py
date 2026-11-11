# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""WorkBuddy-facing deterministic CLI. Analysis itself is done by the chat model."""
import argparse
import json
import sys
from pathlib import Path
from .store import AppError
from . import network
from .runtime import WorkbenchRuntime

def emit(value): print(json.dumps(value,ensure_ascii=False,indent=2))

def main(argv=None, *, runtime_factory=None):
    parser=argparse.ArgumentParser(description='发糕扫榜：上下文、作品定位、正文和报告登记')
    parser.add_argument('--data-dir'); parser.add_argument('--session',help='本轮 WorkBuddy 对话的稳定标识')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('context'); sub.add_parser('state')
    for name in ('resolve','prepare'):
        p=sub.add_parser(name); p.add_argument('query')
    p=sub.add_parser('translation-context'); p.add_argument('query',nargs='?',default='翻译这本'); p.add_argument('--snapshot'); p.add_argument('--book')
    p=sub.add_parser('prepare-book'); p.add_argument('--snapshot',required=True); p.add_argument('--book',required=True); p.add_argument('--request',default='分析已定位作品')
    p=sub.add_parser('import-snapshot'); p.add_argument('file')
    p=sub.add_parser('import-ranking-html'); p.add_argument('file'); p.add_argument('--platform',choices=['scribblehub'],default='scribblehub'); p.add_argument('--source-url')
    p=sub.add_parser('import-text'); p.add_argument('--snapshot',required=True); p.add_argument('--book',required=True); p.add_argument('--file',required=True); p.add_argument('--scope',required=True); p.add_argument('--complete',action='store_true')
    p=sub.add_parser('save-report'); p.add_argument('--task',required=True); p.add_argument('--file',required=True)
    p=sub.add_parser('text'); p.add_argument('id')
    p=sub.add_parser('report'); p.add_argument('id')
    p=sub.add_parser('task'); p.add_argument('id')
    p=sub.add_parser('resume'); p.add_argument('id')
    p=sub.add_parser('collect'); p.add_argument('platform'); p.add_argument('--options',default='{}')
    p=sub.add_parser('collect-all'); p.add_argument('platform',choices=['fanqie']); p.add_argument('--options',default='{}')
    p=sub.add_parser('search'); p.add_argument('query'); p.add_argument('--limit',type=int,default=100)
    p=sub.add_parser('catalog'); p.add_argument('--task',required=True)
    p=sub.add_parser('insights'); p.add_argument('--snapshot',required=True)
    p=sub.add_parser('config'); p.add_argument('--points-site',required=True)
    p=sub.add_parser('network-config'); p.add_argument('--mode',choices=['system','direct','proxy'],required=True); p.add_argument('--proxy-url')
    sub.add_parser('network-check')
    p=sub.add_parser('membership',help='查看、刷新会员状态或退出本机账号')
    p.add_argument('action',choices=['status','refresh','logout'],nargs='?',default='status')
    p=sub.add_parser('legacy',help='查看、明确认领或跳过旧版匿名资料')
    p.add_argument('action',choices=['inspect','claim','skip'],nargs='?',default='inspect')
    p.add_argument('--confirm',action='store_true',help='明确确认认领到当前账号，或保留旧资料并跳过')
    p=sub.add_parser('fetch-text'); p.add_argument('--task',required=True); p.add_argument('--start',type=int,default=1)
    group=p.add_mutually_exclusive_group(); group.add_argument('--chapters',type=int,default=3); group.add_argument('--all',action='store_true')
    args=parser.parse_args(argv)
    try:
        runtime=(runtime_factory or WorkbenchRuntime)(args.data_dir)
        network.configure(runtime.root_dir)
        if args.command=='network-config':
            emit(network.save_settings(runtime.root_dir,args.mode,args.proxy_url))
            return 0
        if args.command=='network-check':
            from .collectors import probe_network
            emit({'probe':probe_network(),'network':network.status()})
            return 0
        if args.command=='membership':
            result=(runtime.membership.logout() if args.action=='logout'
                    else runtime.membership.status(refresh=args.action=='refresh'))
            emit(result)
            return 0
        if args.command=='legacy':
            from .legacy_claim import legacy_decision, claim_legacy, skip_legacy
            if args.action=='inspect':
                result=legacy_decision(runtime)
            elif args.action=='claim':
                result=claim_legacy(runtime,confirmed=args.confirm)
            else:
                result=skip_legacy(runtime,confirmed=args.confirm)
            emit(result)
            return 0
        read_commands={'state','context','resolve','text','report','task','search','insights'}
        context=runtime.capture(operation='read' if args.command in read_commands else 'use')
        if args.command not in read_commands and hasattr(context,'account_id'):
            from .legacy_claim import require_legacy_decision
            require_legacy_decision(runtime,context.account_id,context.generation)
        # Reading saved history never permits a fresh external source request.
        def require_new_request():
            if hasattr(context,'account_id'):
                return runtime.membership.require(account_id=context.account_id,
                    generation=context.generation,operation='use')
            return context.require()  # Explicit trusted unit-test injection.
        with network.authorized_requests(require_new_request):
            result=run_command(args,context.store)
            context.require()
            emit(result)
    except Exception as e:
        emit({'error':{'code':getattr(e,'code','operation_failed'),'message':getattr(e,'message','操作未完成，请检查工作台状态。'),'candidates':getattr(e,'candidates',[])}})
        return 1
    return 0


def run_command(args,store):
    """Execute within the caller's fixed, authorized account context."""
    def collection_progress(message, *unused, snapshot=None, **details):
        # A completed page belongs to this captured account even if permission
        # expires before the next request. Preserve it before the collector
        # checks its next batch; never move it to a newly logged-in account.
        if snapshot is not None:
            store.save_snapshot(snapshot)
        print(message,file=sys.stderr)
    if args.command=='context': result=store.context(args.session)
    elif args.command=='state': result=store.state()
    elif args.command in ('resolve','prepare'):
        from .resolver import resolve_reference
        result=resolve_reference(args.query,store.context(args.session))
        if args.command=='prepare': result=store.prepare_task(result,args.query,args.session)
    elif args.command=='translation-context': result=store.translation_context(args.query,args.session,args.snapshot,args.book)
    elif args.command=='prepare-book': result=store.prepare_task({'snapshot_id':args.snapshot,'book_id':args.book},args.request,args.session)
    elif args.command=='import-snapshot': result=store.save_snapshot(json.loads(Path(args.file).read_text(encoding='utf-8-sig')),imported=True)
    elif args.command=='import-ranking-html':
        from .scribble_import import parse_saved_ranking
        source=Path(args.file)
        if source.stat().st_size>8*1024*1024: raise AppError('too_large','榜单 HTML 文件不得超过 8 MB。')
        result=store.save_snapshot(parse_saved_ranking(source.read_text(encoding='utf-8-sig'),args.source_url),imported=True)
    elif args.command=='import-text': result=store.import_text(args.snapshot,args.book,Path(args.file).read_text(encoding='utf-8-sig'),args.scope,args.complete)
    elif args.command=='save-report': result=store.save_report(args.task,Path(args.file).read_text(encoding='utf-8-sig'))
    elif args.command=='text': result=store.text_record(args.id)
    elif args.command=='report': result=store.report(args.id)
    elif args.command=='task': result=store.task(args.id)
    elif args.command=='resume':
        previous=store.task(args.id)
        result=store.prepare_task(previous,previous['request'],args.session or previous.get('session'))
    elif args.command=='config': result=store.update_config({'points_site':args.points_site})
    elif args.command=='fetch-text':
        from .collectors import fetch_text
        t=store.task(args.task)
        raw=fetch_text(t['platform'],t['book_id'],store.directory,max_chapters=None if args.all else args.chapters,start_chapter=args.start,progress=lambda m,*a,**k:print(m,file=sys.stderr))
        result=store.save_fetched_text(t['snapshot_id'],t['book_id'],raw)
    elif args.command=='catalog':
        from .collectors import fetch_catalog
        t=store.task(args.task)
        result=fetch_catalog(t['platform'],t['book_id'],store.directory)
    elif args.command=='insights':
        from .insights import build_insights
        result=build_insights(store.snapshot(args.snapshot),store.state(full=True)['snapshots'])
    elif args.command=='search':
        from .batch import search_snapshots
        result=search_snapshots(store.state(full=True)['snapshots'],args.query,args.limit)
    elif args.command=='collect-all':
        from .batch import collect_all_categories
        result=collect_all_categories(store,json.loads(args.options),collection_progress)
    elif args.command=='collect':
        from .collectors import collect
        result=store.save_snapshot(collect(args.platform,json.loads(args.options),store.directory,collection_progress))
    return result
if __name__=='__main__': sys.exit(main())
