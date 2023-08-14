import hashlib
import os
import pickle
import re
from evernote.api.client import EvernoteClient
from evernote.edam.notestore import NoteStore
from base64 import b16encode
import ipfshttpclient

"""
Evernote To IPFS

Author: zjuchenyuan

将印象笔记的笔记导出到IPFS

# 前置要求
1. 印象笔记API token： https://app.yinxiang.com/api/DeveloperToken.action
  但现在印象笔记和Evernote都关闭了新用户申请，你可能需要发多次工单以请求开放此功能
2. IPFS daemon
  本程序会调用子进程ipfs add，所以你需要正在运行ipfs daemon
3. 本代码需要python3 并 安装evernote-sdk-python3
  注意pip上的版本不支持中国china参数
  以下安装方法供参考：
  pip3 install evernote3 -i https://pypi.doubanio.com/simple/ --trusted-host pypi.doubanio.com
  pip3 uninstall -y evernote3
  git clone --depth 1 https://github.com/evernote/evernote-sdk-python3
  cd evernote-sdk-python3
  python3 setup.py install

# 直接运行
请在config.py中给出你的auth_token
然后直接运行本代码，将列出最新的10条笔记名称
输入你想导出的笔记id
等待笔记下载（缓存将写入__pycache__文件夹） 和 笔记处理(en-media转为img标签)
最后将输出ipfs的id

## More

安全性讨论 与 ipfs数据持久性讨论 见README

"""

def safefilename(filename):
    """
    convert a string to a safe filename
    :param filename: a string, may be url or name
    :return: special chars replaced with _
    """
    for i in "\\/:*?\"<>|$":
        filename=filename.replace(i,"_")
    return filename

class Evernote2IPFS():
    def __init__(self, auth_token, sandbox=False, china=True, cachedir=None):
        """
        auth_token 印象笔记开发者API Token
        sandbox 是否沙箱 应设置为False
        china 是印象笔记而不是Evernote 应设置为True
        cachedir 笔记下载后将写入缓存，缓存应该存到哪 默认不设置则使用"__pycache__/"
        """
        self.auth_token = auth_token
        self.client = EvernoteClient(token=auth_token, sandbox=sandbox, china=china)
        # self.user_store = self.client.get_user_store()
        self.note_store = self.client.get_note_store()
        if cachedir is None:
            cachedir = "__pycache__/"
        else:
            cachedir = cachedir.replace("\\","/")
            if not cachedir.endswith("/"):
                cachedir = cachedir+"/"
        if not os.path.exists(cachedir):
            os.mkdir(cachedir)
        self.cachedir = cachedir
        
    def getusage(self):
        amount_used = self.note_store.getSyncState().uploaded/1024/1024
        #used_per = amount_used/(10 * 1024)
        #print("Usage: {used_per:.3f}% {amount_used:.1f}MB / 10GB".format(**locals()))
        return amount_used

    def getlatest(self, limit=5):
        """
        获取最新的limit条笔记
        返回 [(id, 标题), ...]
        """
        filter = NoteStore.NoteFilter()
        filter.ascending = False
         
        spec = NoteStore.NotesMetadataResultSpec()
        spec.includeTitle = True
         
        ourNoteList = self.note_store.findNotesMetadata(self.auth_token, filter, 0, limit, spec)

        return [(item.guid, item.title) for item in ourNoteList.notes]

    def getnote(self, guid, cache=True):
        """
        有缓存设计的获取笔记
        缓存文件名为guid+".pickle"
        默认cache=True, 先查询文件缓存，不存在再调用API获取笔记
        若cache=False 则不读缓存 直接调用API获取笔记 还是会写缓存文件
        """
        try:
            if not cache:
                raise Exception
            note = pickle.load(open(self.cachedir+ guid + ".pickle","rb"))
        except:
            note = self.note_store.getNote(auth_token, guid, True, True, True, True) 
            open(self.cachedir + guid + ".pickle","wb").write(pickle.dumps(note))
        return note

    @staticmethod
    def getfilepath(note, targetdir=None):
        """
        返回文件夹路径
        如果没有给定targetdir, 使用笔记标题
        如果文件夹不存在，使用os.mkdir创建
        """
        if targetdir is None:
            filepath = safefilename(note.title)
        else:
            filepath = targetdir
        if not filepath.endswith("/"):
            filepath += "/"
        if not os.path.exists(filepath):
            os.mkdir(filepath)
        return filepath

    @staticmethod
    def write_image_files(note, targetdir=None):
        """
        从笔记导出其所有资源文件
        TODO: 并非所有资源文件都是图片
        """
        filepath = __class__.getfilepath(note, targetdir)
        resources = note.resources
        for item in resources:
            hash = b16encode(item.data.bodyHash).decode().lower()
            open(filepath + hash + ".jpg","wb").write(item.data.body)
            
    @staticmethod
    def modifyhtml(html):
        """
        修改html文件，将en-media标签改为img
        TODO: 并非所有资源文件都是图片
        """
        def udf(m):
            extra1, extra2 = m.group('extra1'), m.group('extra2')
            hash = m.group('hash')
            return r'<img {extra1} src="{hash}.jpg" {extra2}>'.format(**locals())
        
        newhtml = re.sub(r'''<en-media (?P<extra1>[^<>]*?) hash="(?P<hash>[a-z0-9]{32})"(?P<extra2>[^<>]*?)>''', udf, html)
        newhtml = newhtml.replace("</en-media>", "</img>").replace("&amp;quot;", "'")
        return newhtml

    @staticmethod
    def _note2dir(note, targetdir=None, withimg=True):
        """
        将note笔记转为文件夹
        如果不指定targetdir，则默认使用笔记标题
        如果withimg为False 则不导出图片
        """
        filepath = __class__.getfilepath(note, targetdir)
        open(filepath + "index.html","w", encoding="utf-8").write(__class__.modifyhtml(note.content))
        if withimg:
            __class__.write_image_files(note)
        return filepath
    
    def note2dir(self, guid, targetdir=None):
        """
        输入笔记guid 获取笔记 并 转换得到index.html和图片文件
        可选参数targetdir 指定将使用的文件夹 如未提供则默认为笔记标题
        返回写入的文件夹名称
        """
        note = self.getnote(guid)
        return self._note2dir(note, targetdir=targetdir)

    def ipfsdir(self, targetdir):
        """
        Use the IPFS Python library to add the directory to IPFS.
        """
        client = ipfshttpclient.connect('/ip4/127.0.0.1/tcp/5001/http')
        res = client.add(targetdir, recursive=True)
        return res['Hash']


if __name__=="__main__":
    from pprint import pprint
    from config import auth_token
    e2i = Evernote2IPFS(auth_token)
    id = 0
    latest = e2i.getlatest(10)
    for guid, item in latest:
        print("[{id:2d}] {item}".format(**locals()))
        id += 1
    while True:
        print("Input id:")
        choice = input()
        try:
            i = int(choice)
            guid = latest[i][0]
            break
        except:
            print("Input error, please try again")
    
    dir = e2i.note2dir(guid)
    cid = e2i.ipfsdir(dir)
    print("Dump finished, you can visit:\n\nhttp://127.0.0.1:8080/ipfs/{cid}\n\nor:\n\nhttps://ipfs.io/ipfs/{cid}\n\n{cid}".format(cid=cid))
    