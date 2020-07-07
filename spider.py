"""DN42搜索引擎爬虫"""

import json
import threading
import time
import urllib

# TODO: 关键词不区分大小写
# import jieba  # 中文分词库
# TODO: import asyncio
import requests
from bs4 import BeautifulSoup
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

start_url = "http://dn42.us/"
# start_url = "http://wiki.dn42.us/"

SAVE_INTERVAL = 100  # 多少个循环保存一次
THREAD_NUM = 20  # 一次循环并发多少个线程
EXCEPT_DOMAIN = ["git.dn42.us", "git.dn42", "git.dn42.dev", "ftp.c3d2.de", "img.dn42", "invidious.doxz.dn42"]  # 不爬取这些域名
EXCEPT_URL_WORD = ["git.dn42"]  # URL包含这些字符串则排除

# 加载数据 TODO: 改用数据库存储。json仅适合用作demo。
try:
    with open("global_url_list.json", "r") as f:
        global_url_list = json.load(f)
except FileNotFoundError:
    global_url_list = []  # 已经爬取过的URL
try:
    with open("todo_url_list.json", "r") as f:
        todo_url_list = json.load(f)
except FileNotFoundError:
    todo_url_list = [start_url]  # 待爬取的URL，如果为空，会重新爬取global_url_list
try:
    with open("forward_index.json", "r") as f:
        forward_index = json.load(f)
except FileNotFoundError:
    forward_index = {}  # 正向索引
try:
    with open("inverted_index.json", "r") as f:
        inverted_index = json.load(f)
except FileNotFoundError:
    inverted_index = {}  # 倒排索引

index_lock = threading.Lock()  # 索引的锁，并发的多线程需要修改索引时需要这个锁


class Thread(threading.Thread):
    """有返回值的线程类"""

    def __init__(self, target, name, args=()):
        threading.Thread.__init__(self)
        self.name = name  # 线程名（分析模块名）
        self.target = target  # 线程执行的函数名
        self.result = None  # 线程的返回值
        self.args = args  # 函数参数

    def run(self):
        # 运行函数，得到返回值
        self.result = self.target(*self.args)


def crawl_page(crawl_url: str) -> (str, list):
    """爬一个网页，返回网页内容和url列表"""
    global forward_index, inverted_index

    # 检查域名
    _domain = urllib.request.splithost(urllib.request.splittype(crawl_url)[1])[0]
    if _domain in EXCEPT_DOMAIN:
        print("Skip: " + crawl_url)
        return "", []

    # 检查url
    for _except_word in EXCEPT_URL_WORD:
        if crawl_url.find(_except_word) != -1:
            print("Skip: " + crawl_url)
            return "", []

    # 查看网页头部
    try:
        _resp = requests.head(crawl_url, verify=False, timeout=10, allow_redirects=True, headers={"Connection": "close"})
    except requests.exceptions.ConnectionError:
        print("ConnectionError: " + crawl_url)
        return "", []
    except requests.exceptions.ReadTimeout:
        print("ReadTimeout: " + crawl_url)
        return "", []
    # _status_code = str(_resp.status_code)
    # if not _status_code.startswith("2"):
    #     print("Status Code " + _status_code + ": " + crawl_url)
    #     return "", []
    if "Content-Length" in _resp.headers:
        if int(_resp.headers["Content-Length"]) > 2 * 1024 * 1024:  # 2M
            print("Content-Length too large: " + crawl_url)
            return "", []
    if "Content-Type" in _resp.headers:
        content_type = _resp.headers["Content-Type"]
        if content_type.find("text/html") == -1:
            print("Content-Type " + content_type + " not supported: " + crawl_url)
            return "", []
    else:
        # print("No Content-Type: " + crawl_url)
        if "Content-Length" not in _resp.headers:
            print("No Content-Type and no Content-Length: " + crawl_url)
            return "", []

    # 下载网页
    try:
        _resp = requests.get(crawl_url, verify=False, timeout=30, headers={"Connection": "close"})
    except requests.exceptions.ConnectionError:
        print("ConnectionError: " + crawl_url)
        return "", []
    except requests.exceptions.ReadTimeout:
        print("ReadTimeout: " + crawl_url)
        return "", []
    _status_code = str(_resp.status_code)
    if not _status_code.startswith("2"):
        print("Status Code " + _status_code + ": " + crawl_url)
        return "", []

    # 读取网页
    _html = _resp.text
    if crawl_url.find("dn42") == -1 and crawl_url.find("DN42") == -1 and _html.find("dn42") == -1 and _html.find("DN42") == -1:
        print("Not DN42 relation: " + crawl_url)
        return "", []  # 仅爬dn42相关内容
    print("Crawl: " + crawl_url)
    _soup = BeautifulSoup(_html, 'html.parser')

    # 获取标题
    _title = _soup.find('title')
    if not _title:
        _title = "[No Title]"
    else:
        _title = _title.get_text()

    # 获取链接
    _links = _soup.find_all('a')
    _url_list_tmp = []
    for _item in _links:
        _url = _item.get('href')
        _url_list_tmp.append(_url)
    _url_list = []
    for _url in _url_list_tmp:
        if not _url:
            continue
        if _url.startswith("http://") or _url.startswith("https://"):
            _url_list.append(_url.split("#")[0].split("?")[0])
        else:
            _new_url = urllib.parse.urljoin(crawl_url, _url)
            if _new_url.startswith("http"):
                _url_list.append(_new_url.split("#")[0].split("?")[0])

    # 获取文字内容
    for _script in _soup(["script", "style"]):
        _script.decompose()
    _words = _soup.get_text().split()

    # 修改索引
    # _words = jieba.cut_for_search(" ".join(_words)) # 中文分词
    index_lock.acquire()
    forward_index[crawl_url] = {  # 正向索引
        "words": _words,
        "title": _title,
        "time": time.time(),
        "html": _html,
        "domain": _domain
    }
    for _word in set(_words):
        _word_position = [i for i, x in enumerate(_words) if x == _word]
        if _word not in inverted_index:
            inverted_index[_word] = {}
        inverted_index[_word][crawl_url] = {  # 倒排索引
            "word_position": _word_position,
            "times": len(_word_position)
        }
    index_lock.release()

    return _html, _url_list


def crawl_round(crawl_url_list: list, force: bool = False) -> list:
    """爬取一次URL列表，内部多线程循环 TODO：线程池或协程"""
    global global_url_list
    _task_url_list = []  # 实际需要爬取的URL
    _url_list_return = []  # 爬完传入的URL列表后，返回下一次待爬取的URL
    for _url in crawl_url_list:
        if force:  # 如果设置了force，则所有URL都会爬取
            _task_url_list.append(_url)
            if _url not in global_url_list:
                global_url_list.append(_url)
        elif _url not in global_url_list:  # 否则只会爬取不在global_url_list的URL
            global_url_list.append(_url)
            _task_url_list.append(_url)

    print("Global URL num: %d" % len(global_url_list))  # global_url_list的URL数量
    print("Crawl URL num: %d" % len(crawl_url_list))  # 传入的crawl_url_list的URL数量
    print("Actually crawl URL num: %d" % len(_task_url_list))  # 实际需要爬取的URL数量
    print("Total task times: %d" % (len(_task_url_list) // THREAD_NUM + 1))  # 需要的循环次数

    # 循环爬取，每次并发THREAD_NUM个线程
    for _task_times in range(len(_task_url_list) // THREAD_NUM + 1):
        if _task_times == len(_task_url_list) // THREAD_NUM:
            _task_list_tmp = _task_url_list[_task_times * THREAD_NUM:]
        else:
            _task_list_tmp = _task_url_list[_task_times * THREAD_NUM:(_task_times + 1) * THREAD_NUM]
        print("    Task times: %d / %d  Thread num: %d" % (_task_times + 1, len(_task_url_list) // THREAD_NUM + 1, len(_task_list_tmp)))
        _thread_list = []
        for _url in _task_list_tmp:
            _thread = Thread(target=crawl_page, name=_url, args=(_url,))
            _thread_list.append(_thread)
        for _thread in _thread_list:
            _thread.start()
        for _thread in _thread_list:
            _thread.join()

        for _thread in _thread_list:
            if _thread.result:
                _url_list_return.extend(_thread.result[1])

        # 每隔SAVE_INTERVAL次循环或爬完最后一次则保存
        if _task_times == len(_task_url_list) // THREAD_NUM or (_task_times + 1) % SAVE_INTERVAL == 0:
            print("Saving...")
        with open("global_url_list.json", "w") as f:
            json.dump(global_url_list, f)
        with open("forward_index.json", "w") as f:
            json.dump(forward_index, f)
        with open("inverted_index.json", "w") as f:
            json.dump(inverted_index, f)
            print("Saved.")
        # 让搜索引擎重新加载数据
        # try:
        #     requests.get("http://127.0.0.1:8080/api/loadData", timeout=3)
        # except:
        #     pass

    # 返回下一次待爬取的URL
    _url_list_return = list(set(_url_list_return))
    print("Return URL num: %d" % len(_url_list_return))

    return _url_list_return


# 如果有待爬取的URL，则爬取，否则重新爬取整个global_url_list
if todo_url_list:
    todo_url_list = crawl_round(todo_url_list)
else:
    todo_url_list = crawl_round(global_url_list, force=True)

# 保存todo_url_list
print("Save todo_url_list.")
with open("todo_url_list.json", "w") as f:
    json.dump(todo_url_list, f)

print("Finish.")
