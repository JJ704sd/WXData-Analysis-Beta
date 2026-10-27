"""Fetch one user-requested WeCom video from its authorized official endpoint."""
import hashlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 100 * 1024 * 1024
PART_SIZE = 512 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_wecom_video(attributes):
    from media_container import aes_decrypt, media_type
    url=attributes.get('tpvideourl','');ticket=attributes.get('tpauthkey','')
    key=attributes.get('aeskey','');digest=attributes.get('md5','').lower()
    try:
        address=urllib.parse.urlsplit(url)
        allowed=(address.scheme=='https' and address.hostname=='wwfile.work.weixin.qq.com'
                 and address.port in (None,443) and not address.username and not address.password and not address.fragment)
        size=int(attributes.get('length') or attributes.get('tplength') or 0)
    except (ValueError,TypeError):
        raise ValueError('视频下载信息无效。') from None
    if not allowed:raise ValueError('此视频没有当前支持的官方下载地址，可在 Linux 微信中下载后重试。')
    if not ticket or len(ticket)>8192 or not ticket.isascii() or any(ord(c)<32 or ord(c)==127 for c in ticket):
        raise ValueError('视频下载授权信息无效，请在微信中重新接收视频。')
    if not re.fullmatch(r'[0-9a-fA-F]{32}',key) or not re.fullmatch(r'[0-9a-f]{32}',digest):
        raise ValueError('视频缺少解密或完整性校验信息。')
    if not 0<size<=LIMIT:raise ValueError('视频大小无效或超过本版 100 MB 上限。')

    encrypted_size=(size//16+1)*16
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    body=bytearray();total=None;deadline=time.monotonic()+55
    while total is None or len(body)<total:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ValueError('视频下载超时，请重试。')
        start=len(body);end=min(start+PART_SIZE-1,(total or encrypted_size)-1)
        request=urllib.request.Request(url,headers={'authkey':ticket,'Range':f'bytes={start}-{end}'})
        try:
            with opener.open(request,timeout=min(15,remaining)) as response:
                # The official service can return one partial block even without a Range request.
                if response.status==206:
                    match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                    if not match:raise ValueError('视频下载分段信息不完整。')
                    left,right,reported=map(int,match.groups())
                    if left!=start or right<left or right>end or reported not in (size,encrypted_size) or (total is not None and total!=reported):
                        raise ValueError('视频下载分段校验失败，请重试。')
                    total=reported;expected=right-left+1
                    block=response.read(expected+1)
                    if len(block)!=expected:raise ValueError('视频下载分段尚未完整。')
                elif response.status==200 and start==0:
                    block=response.read(encrypted_size+1)
                    if len(block) not in (size,encrypted_size):raise ValueError('视频下载内容大小不匹配。')
                    total=len(block)
                else:raise ValueError('视频服务器没有返回有效内容。')
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403):raise ValueError('这条视频的下载授权已失效，请在微信中重新接收或下载后重试。') from None
            raise ValueError('微信文件服务器暂未完成下载，请重试。') from None
        except (urllib.error.URLError,OSError,TimeoutError):
            raise ValueError('无法连接微信文件服务器，请稍后重试。') from None
        body.extend(block)

    clear=bytes(body)
    if media_type(clear)[0]!='video/mp4':
        try:clear=aes_decrypt(clear,bytes.fromhex(key))
        except ValueError:raise ValueError('视频解密校验失败，请重新加载。') from None
    if len(clear)!=size or hashlib.md5(clear).hexdigest()!=digest or media_type(clear)[0]!='video/mp4':
        raise ValueError('完整视频校验失败，未提供给播放器。')
    return clear
