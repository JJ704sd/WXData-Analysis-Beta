"""Portable bootstrap: handle spawn workers first and keep fatal errors visible."""
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1]
if not getattr(sys,'frozen',False):sys.path.insert(0,str(PROJECT/'web_mvp'))


def run():
    from offline_startup import begin,error,path
    def argument(flag,default=None):
        try:return sys.argv[sys.argv.index(flag)+1]
        except (ValueError,IndexError):return default
    from offline_profiles import ClientBindingError,default_directory,resolve_client
    try:
        if '--capabilities' in sys.argv:
            from local_chat_app import main
            return main()
        client=resolve_client(argument('--client'))
        directory=argument('--data-dir') or default_directory(client)
        begin(directory,client)
        from local_chat_app import main
        return main()
    except Exception as failure:
        if path() is None:begin(None,'startup')
        error('fatal_startup_error',type(failure),failure.__traceback__)
        if '--task-json' not in sys.argv and '--launch-desktop-json' not in sys.argv:
            import ctypes
            detail=str(failure) if isinstance(failure,ClientBindingError) else '程序未能启动，错误类型：'+type(failure).__name__+'。'
            ctypes.windll.user32.MessageBoxW(None,detail+'\n\n诊断日志：'+str(path() or '日志目录不可写')+'\n\n请保留此提示并反馈日志；不要据此判断账号或密钥错误。','本地聊天解析：启动失败',0x10)
        return 1


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(run())
