import json
import threading
import time
from datetime import timedelta

import numpy as np
from flask import Flask, request

# 初始化Flask
app = Flask(__name__)
app.send_file_max_age_default = timedelta(seconds=30)
app.secret_key = "s1f3ha9q3sdahfkgu3p094oyi4r89pt0ua"

data_lock = threading.Lock()  # 数据锁，防止多个load_data并发
data_time = 0  # 上一次数据更新的时间
DATA_EXPIRE = 30  # 数据过期时间（过期时间内load_data会直接返回）


def load_data():
    """重新加载json索引 TODO：json改为数据库"""
    global forward_index, inverted_index, page_recorded, data_time
    if time.time() - data_time < DATA_EXPIRE:
        return
    data_time = time.time()
    data_lock.acquire()
    with open("forward_index.json", "r") as f:
        forward_index = json.load(f)
    with open("inverted_index.json", "r") as f:
        inverted_index = json.load(f)
    page_recorded = len(forward_index)
    data_lock.release()


def center_value(data):
    """求数据去除大偏差后的平均值"""
    for i in range(100):  # 循环100次去除大偏差
        if not data:
            return 0
        finish = True
        mean = np.mean(data)
        std = np.std(data)
        for item in data:
            # 数值超过平均值2个标准差则算过大
            if item < mean - 2 * std or item > mean + 2 * std:
                # 删除一个数据后重新循环
                data.remove(item)
                finish = False
                break
        if finish:  # 全部都没偏差了
            # 如果标准差依然很大，则返回中位数
            if std > 50:
                return np.median(data)
            # 否则返回平均值
            return np.mean(data)


@app.route("/")
def index():
    """返回主页"""
    return app.send_static_file("index.html")


@app.route("/snapshot")
def snapshot():
    """返回页面快照"""
    global forward_index
    url = request.args.get("url")
    return forward_index[url]["html"]


@app.route("/api/loadData")
def api_load_data():
    """重新加载json索引"""
    load_data()
    return "OK"


@app.route("/api/pageRecorded", methods=['GET'])
def api_page_recorded():
    """返回收录的页面总数"""
    global page_recorded
    return json.dumps({
        "page_recorded": page_recorded
    })


@app.route("/api/search", methods=['POST'])
def api_search():
    """执行搜索并返回搜索结果"""
    global forward_index, inverted_index

    # 开始搜索的时间
    start_time = time.time()

    # 获取用户输入的关键词
    data = json.loads(request.get_data(as_text=True))
    if not data["text"]:
        return json.dumps({
            "error": "Empty keywords.",
        })
    text = data["text"].split()

    # 获取符合关键词的url
    url_result_list = set()
    for word in text:
        # 如果没有这个词直接返回错误信息
        if word not in inverted_index:
            return json.dumps({
                "error": "Nothing match: " + word,
            })
        # 用集合筛选出符合所有word的url
        if not url_result_list:
            url_result_list = set(inverted_index[word].keys())
        else:
            url_result_list = url_result_list & set(inverted_index[word].keys())  # 求交集

    # 匹配太多url则报错
    # if len(url_result_list) > 100:
    #     return json.dumps({
    #         "error": "Too much result, please add more keywords. (limit: 100)",
    #     })

    # 计算url权重（权重计算方法：sum(单词在这个网页出现的次数/单词出现总次数)）
    url_weight_dict = {}
    for word in text:
        word_times_total = 0
        for url, values in inverted_index[word].items():
            word_times_total += values["times"]
        for url in url_result_list:
            word_times_this = inverted_index[word][url]["times"]
            weight = word_times_this / word_times_total
            if url not in url_weight_dict:
                url_weight_dict[url] = weight
            else:
                url_weight_dict[url] += weight

    # 对url按权重从大到小排序
    url_weight_list = sorted(url_weight_dict.items(), key=lambda x: x[1], reverse=True)

    # 如果数据太多，则只返回前100条
    if len(url_weight_list) > 100:
        url_weight_list = url_weight_list[:100]

    # 返回结果
    result = []
    for url, weight in url_weight_list:
        # 如果两个索引数据不一致则跳过该URL
        if url not in forward_index:
            continue
        # 生成摘要
        word_positions = []
        if len(forward_index[url]["words"]) <= 80:
            abstract = " ".join(forward_index[url]["words"])
        else:
            for word in text:
                word_positions.extend(inverted_index[word][url]["word_position"])
            average_position = int(round(center_value(word_positions)))
            if average_position < 40:
                abstract = " ".join(forward_index[url]["words"][:80]) + " ..."
            elif average_position > len(forward_index[url]["words"]) - 40:
                abstract = "... " + " ".join(forward_index[url]["words"][-80:])
            else:
                abstract = "... " + " ".join(forward_index[url]["words"][average_position - 40:average_position + 40]) + " ..."
        # 对关键词加上颜色
        for word in text:
            abstract = abstract.replace(" " + word, ' <font color="red"><strong>' + word + '</strong></font>')
        # 添加url信息到结果列表
        result.append({
            "url": url,
            "weight": round(weight * 10, 5),
            "domain": forward_index[url]["domain"],
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(forward_index[url]["time"])),
            "abstract": abstract,
            "title": forward_index[url]["title"],
        })

    # 结束搜索的时间
    end_time = time.time()

    return json.dumps({
        "result": result,  # 搜索结果列表
        "match_records": len(url_result_list),  # 匹配到的页面数量
        "time_used": round((end_time - start_time) * 1000, 2)  # 搜索耗时，单位ms
    })


if __name__ == '__main__':
    load_data()
    app.run('0.0.0.0', port=8080)
