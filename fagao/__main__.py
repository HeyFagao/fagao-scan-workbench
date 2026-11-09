"""WorkBuddy-facing deterministic CLI. Analysis itself is done by the chat model."""
import argparse
import json
import sys
from pathlib import Path
from .store import Store, AppError

def emit(value): print(json.dumps(value,ensure_ascii=False,indent=2))

def main():
    parser=argparse.ArgumentParser(description='发糕扫榜：上下文、作品定位、正文和报告登记')
    parser.add_argument('--data-dir'); parser.add_argument('--session',help='本轮 WorkBuddy 对话的稳定标识')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('context'); sub.add_parser('state')
    for name in ('resolve','prepare'):
        p=sub.add_parser(name); p.add_argument('query')
    p=sub.add_parser('translation-context'); p.add_argument('query',nargs='?',default='翻译这本'); p.add_argument('--snapshot'); p.add_argument('--book')
    p=sub.add_parser('prepare-book'); p.add_argument('--snapshot',required=True); p.add_argument('--book',required=True); p.add_argument('--request',default='分析已定位作品')
    p=sub.add_parser('import-snapshot'); p.add_argument('file')
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
    p=sub.add_parser('fetch-text'); p.add_argument('--task',required=True); p.add_argument('--start',type=int,default=1)
    group=p.add_mutually_exclusive_group(); group.add_argument('--chapters',type=int,default=3); group.add_argument('--all',action='store_true')
    args=parser.parse_args(); store=Store(args.data_dir)
    try:
        if args.command=='context': result=store.context(args.session)
        elif args.command=='state': result=store.state()
        elif args.command in ('resolve','prepare'):
            from .resolver import resolve_reference
            result=resolve_reference(args.query,store.context(args.session))
            if args.command=='prepare': result=store.prepare_task(result,args.query,args.session)
        elif args.command=='translation-context': result=store.translation_context(args.query,args.session,args.snapshot,args.book)
        elif args.command=='prepare-book': result=store.prepare_task({'snapshot_id':args.snapshot,'book_id':args.book},args.request,args.session)
        elif args.command=='import-snapshot': result=store.save_snapshot(json.loads(Path(args.file).read_text(encoding='utf-8-sig')),imported=True)
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
            from .fanqie_content import catalog
            t=store.task(args.task)
            if t['platform']!='fanqie': raise AppError('unsupported_platform','此平台尚未接入章节目录。')
            result=catalog(t['book_id'],store.directory)
        elif args.command=='insights':
            from .insights import build_insights
            result=build_insights(store.snapshot(args.snapshot),store.state(full=True)['snapshots'])
        elif args.command=='search':
            from .batch import search_snapshots
            result=search_snapshots(store.state(full=True)['snapshots'],args.query,args.limit)
        elif args.command=='collect-all':
            from .batch import collect_all_categories
            result=collect_all_categories(store,json.loads(args.options),lambda m,*a,**k:print(m,file=sys.stderr))
        elif args.command=='collect':
            from .collectors import collect
            result=store.save_snapshot(collect(args.platform,json.loads(args.options),store.directory,lambda m,*a,**k:print(m,file=sys.stderr)))
        emit(result)
    except Exception as e:
        emit({'error':{'code':getattr(e,'code','operation_failed'),'message':getattr(e,'message',str(e)),'candidates':getattr(e,'candidates',[])}})
        return 1
    return 0
if __name__=='__main__': sys.exit(main())
