#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三角洲行动出号监控系统 — 定时运行脚本
抓取各平台 → 口径映射 → 好号规则筛选 → results.json + result.html

好号规则（用户口径，逐字保留）：
  ① 租金+押金合计 ≤ 500 元
  ② 纯币资产 ≥ 80M（8000万哈夫币，1M=100万）
  ③ 9格保险（顶级安全箱 3*3 = 9格）
  ④ 7级负重（靶场等级 7 级）
  ⑤ 7级体力（训练中心等级 7 级）
  ⑥ AWM 子弹 ≥ 50 发

售号平台（DD373/盼之/换号吧）：deposit=0，rent=售价，price_type=售价。
租号平台（虚贝 lease_list）：rent=advanceRent（预收租金），deposit=有效押金，price_type=租金。
"""
import os, sys, re, json, time, traceback, datetime, html as htmlmod
from urllib.parse import quote as _urlquote

BASE_DIR = os.environ.get("MONITOR_BASE_DIR") or "/home/user/Doubao/chats/456465693868546"
RESULTS_JSON = os.path.join(BASE_DIR, "results.json")
PREV_JSON = os.path.join(BASE_DIR, "results_prev.json")
RESULT_HTML = os.path.join(BASE_DIR, "result.html")
LOG_FILE = os.path.join(BASE_DIR, "monitor.log")
WEBHOOK_FILE = os.path.join(BASE_DIR, "webhook.txt")  # 微信群机器人 webhook，留空则不推送
PUSHPLUS_FILE = os.path.join(BASE_DIR, "pushplus.txt")  # PushPlus token，留空则不推送

os.chdir(BASE_DIR)

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


# ---------------------------------------------------------------------------
# 好号规则
# ---------------------------------------------------------------------------
GOOD_RULES = [
    "① 9格保险（顶级安全箱 3×3 = 9格）",
    "② 负重 ≥ 6级（靶场等级 6级及以上）",
    "③ 体力 ≥ 6级（训练中心等级 6级及以上）",
    "④ AWM 子弹 ≥ 50 发",
]

def is_good(acc):
    """4 条规则全部满足才叫好号（负重/体力 6级及以上）。任一字段缺失(None)则不判定为好号（不伪造）。"""
    try:
        if acc.get("insurance_grids") != 9: return False
        if acc.get("weight_level") is None or acc["weight_level"] < 6: return False
        if acc.get("stamina_level") is None or acc["stamina_level"] < 6: return False
        if acc.get("awm_bullets") is None or acc["awm_bullets"] < 50: return False
        return True
    except Exception:
        return False


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 已失效标注：对比上一轮结果，本轮消失的好号标记为"已失效"
# ---------------------------------------------------------------------------
def load_prev_good():
    """读取上一轮 results.json 的好号列表（url -> 账号）。"""
    try:
        with open(PREV_JSON, "r", encoding="utf-8") as f:
            prev = json.load(f)
        return {a["url"]: a for a in prev.get("good_accounts", [])}
    except Exception:
        return {}


def mark_gone(current_good, prev_good_map):
    """本轮仍存在的好号正常展示；上轮有、本轮没有的 → status='已失效'。"""
    current_urls = {a["url"] for a in current_good}
    gone = []
    for url, acc in prev_good_map.items():
        if url not in current_urls:
            item = dict(acc)
            item["status"] = "已失效"
            item["gone_at"] = now_str()
            gone.append(item)
    return gone


# ---------------------------------------------------------------------------
# 微信群机器人 webhook 推送（有 webhook 地址才启用）
# ---------------------------------------------------------------------------
def send_wechat_webhook(new_good, gone):
    """向企业微信群机器人推送新好号/失效号（markdown：带图片+链接）。无 webhook 文件或内容为空时跳过。
    优先读环境变量 WEBHOOK_URL（GitHub Actions 场景），否则读 webhook.txt。"""
    webhook = os.environ.get("WEBHOOK_URL", "").strip()
    if not webhook:
        try:
            with open(WEBHOOK_FILE, "r", encoding="utf-8") as f:
                webhook = f.read().strip()
        except Exception:
            webhook = ""
    if not webhook or not webhook.startswith("https://"):
        return False
    lines = ["**【三角洲好号监控】**"]
    if new_good:
        lines.append(f"发现 **{len(new_good)}** 个新好号：")
        for a in new_good[:5]:
            img = a.get("image_url")
            price_part = (f"租金{a.get('rent')}元/押金{a.get('deposit')}元"
                          if a.get("price_type") == "租金"
                          else f"售价{a.get('rent')}元/押金{a.get('deposit')}元")
            lines.append(f"> **单号 {a.get('goods_code')}** | {a['platform']} 纯币{a.get('pure_coins_M')}M "
                         f"AWM{a.get('awm_bullets')}发 {a.get('insurance_grids')}格 "
                         f"负重{a.get('weight_level')} 体力{a.get('stamina_level')} "
                         f"{price_part}")
            if img:
                lines.append(f"> ![号截图]({img})")
            lines.append(f"> [查看原页]({a['url']})")
    else:
        lines.append("本轮无新好号")
    if gone:
        lines.append(f"另有 **{len(gone)}** 个号已失效（被租走/下架）：")
        for a in gone[:3]:
            lines.append(f"> 单号 {a.get('goods_code')} | {a['platform']} 纯币{a.get('pure_coins_M')}M 失效 "
                         f"[原页]({a['url']})")
    text = "\n".join(lines)
    try:
        r = requests.post(webhook, json={"msgtype": "markdown", "markdown": {"content": text}},
                          timeout=10)
        log(f"webhook 推送: HTTP {r.status_code} {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        log(f"webhook 推送失败: {e}")
        return False


# ---------------------------------------------------------------------------
# PushPlus 微信推送（个人微信直接收消息）
# ---------------------------------------------------------------------------
def send_pushplus(new_good, gone):
    """通过 PushPlus 推送到个人微信（markdown：带图片+链接）。token 在 pushplus.txt，为空则跳过。
    优先读环境变量 PUSHPLUS_TOKEN（GitHub Actions 场景），否则读 pushplus.txt。
    有变化（新好号或失效号）才推送，无变化不发。"""
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        try:
            with open(PUSHPLUS_FILE, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except Exception:
            token = ""
    if not token:
        return False
    # 无变化不打扰
    if not new_good and not gone:
        return False
    lines = []
    if new_good:
        lines.append(f"发现 **{len(new_good)}** 个新好号：")
        for a in new_good[:8]:
            price_part = (f"租金{a.get('rent')}元/押金{a.get('deposit')}元"
                          if a.get("price_type") == "租金"
                          else f"售价{a.get('rent')}元/押金{a.get('deposit')}元")
            lines.append(
                f"**单号 {a.get('goods_code')}** | {a['platform']} 纯币{a.get('pure_coins_M')}M "
                f"AWM{a.get('awm_bullets')}发 {a.get('insurance_grids')}格 "
                f"负重{a.get('weight_level')} 体力{a.get('stamina_level')} "
                f"{price_part}")
            if a.get("image_url"):
                lines.append(f"![号截图]({a['image_url']})")
            lines.append(f"[查看原页]({a['url']})")
    if gone:
        lines.append(f"另有 **{len(gone)}** 个号已失效（被租走/下架）：")
        for a in gone[:5]:
            lines.append(f"- 单号 {a.get('goods_code')} | {a['platform']} 纯币{a.get('pure_coins_M')}M 失效")
    content = "\n".join(lines)
    try:
        r = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": "三角洲好号提醒",
                  "content": content, "template": "markdown"},
            timeout=15)
        log(f"PushPlus 推送: HTTP {r.status_code} {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        log(f"PushPlus 推送失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 虚贝 lease_list 结构化 API（主筛选源）
# ---------------------------------------------------------------------------
def fetch_xubei():
    info = {"name": "虚贝 lease_list", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Referer": "https://sjzweb.xubei.com/lease_list",
            "Origin": "https://sjzweb.xubei.com",
        }
        base = {
            "businessNo": "xubei", "loginPhone": "",
            "safeBoxType": "4",
            "trainingCenterLevelBegin": "6", "trainingCenterLevelEnd": "7",
            "shootingRangeLevelBegin": "6", "shootingRangeLevelEnd": "7",
        }
        rows = []
        for page in (1, 2):
            body = dict(base, pageSize=20, pageNo=page)
            r = requests.post(
                "https://api8.xubei.com/xubei-goods-search/anon/sjzFindGoodsList",
                headers=headers, json=body, timeout=TIMEOUT)
            j = r.json()
            if not j.get("success"):
                raise RuntimeError(f"API success=false: {j.get('message')}")
            data = (j.get("result") or {}).get("data") or []
            rows.extend(data)
            time.sleep(1.0)
        info["fetched_count"] = len(rows)
        for d in rows:
            try:
                rent = float(d.get("advanceRent") or 0)
                dep = float(d.get("deposit") or 0)
                rel = float(d.get("reliefDeposit") or 0)
                eff_dep = round(dep - rel, 2)
                coins = d.get("hafuCoins")
                coins_M = round(float(coins), 1) if coins is not None else None
                sb = d.get("safeBoxType")
                grids = {1: 2, 2: 4, 3: 6, 4: 9}.get(sb)
                code = d.get("sjzGoodsCode")
                # 截图 URL（首张用于推送/网页展示），http 转 https 防止部分客户端拦截
                shots = [s for s in (d.get("screenshotUrls") or "").split(",") if s.strip()]
                img = None
                if shots:
                    img = shots[0].strip().replace("http://", "https://")
                accounts.append({
                    "platform": "虚贝",
                    "url": f"https://sjzweb.xubei.com/sjz_goods_details?sjzGoodsCode={code}",
                    "goods_code": code,
                    "title": (d.get("customTitle") or "")[:120],
                    "pure_coins_M": coins_M,
                    "awm_bullets": d.get("awmCount"),
                    "insurance_grids": grids,
                    "weight_level": d.get("shootingRangeLevel"),
                    "stamina_level": d.get("trainingCenterLevel"),
                    "rent": round(rent, 2),
                    "deposit": eff_dep,
                    "price_type": "租金",
                    "publish_time": d.get("updateTime"),
                    "fetch_time": now_str(),
                    "image_url": img,
                })
            except Exception as e:
                log(f"  xubei row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (f"服务端已筛 9格/体力6-7/负重6-7，"
                                 f"客户端复判 4 条规则；有效押金=deposit-reliefDeposit")
        log(f"虚贝 lease_list: fetched={len(rows)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"虚贝 lease_list FAILED: {e}")
        traceback.print_exc()
    return info, accounts


# ---------------------------------------------------------------------------
# 虚贝租号大厅 API（兜底展示源，不做好号判定）
# ---------------------------------------------------------------------------
def fetch_xubei_renthall():
    info = {"name": "虚贝租号大厅(兜底)", "status": "部分接入", "status_detail": "",
            "price_type": "元/小时", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    try:
        r = requests.get(
            "http://api.zzshq.xubei.com/renthall/gamelist?gameId=14890&page=1",
            headers={"User-Agent": UA}, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("ul.good-list > li")
        info["fetched_count"] = len(items)
        info["status_detail"] = ("兜底数据：租金为元/小时口径，与规则①（预收租金+押金）不符，"
                                 "不参与好号判定，仅作展示")
        log(f"虚贝租号大厅(兜底): {len(items)} 条（不参与判定）")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        info["status_detail"] = "兜底源不可用"
        log(f"虚贝租号大厅 FAILED: {e}")
    return info, []


# ---------------------------------------------------------------------------
# 工具：从文本里提取哈夫币 → M
# ---------------------------------------------------------------------------
def extract_coins_to_M(text):
    """从自由文本提取哈夫币资产并换算为 M（百万）。
    支持：哈夫币18.7亿、哈夫币57719.1w、哈夫币1710.7万、哈夫币932万、XXW、XX万。
    优先匹配'哈夫币'关键字后的数字。"""
    if not text:
        return None
    # 显式"哈夫币 + 数字 + 单位"
    for m in re.finditer(r"哈夫币[^\d]{0,6}(\d+(?:\.\d+)?)\s*(亿|万|w|W)", text):
        val = float(m.group(1)); unit = m.group(2)
        if unit == "亿":
            return round(val * 100, 2)
        else:  # 万 / w / W
            return round(val / 100.0, 3)
    # 裸 "XXXX万" / "XXXXw" 哈夫币语境（兜底）
    m = re.search(r"哈夫币[^\d]{0,6}(\d+(?:\.\d+)?)\s*(万|w|W)?", text)
    if m and m.group(2):
        val = float(m.group(1)); unit = m.group(2)
        if unit == "万" or unit.lower() == "w":
            return round(val / 100.0, 3)
    return None


def parse_price(text):
    if not text:
        return None
    m = re.search(r"[¥￥]\s*([\d]+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def parse_lv(text):
    """从 'Lv.7' / 'LV.4' / 'Lv.6' 提取等级数字。"""
    if text is None:
        return None
    m = re.search(r"[Ll][Vv]\.?\s*(\d+)", str(text))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# DD373（售号平台，price_type=售价）
# ---------------------------------------------------------------------------
DD373_LIST_URL = ("https://www.dd373.com/s-gp1gaj-f4wdcm-0-0-0-0-04q04w-"
                  "0-0-0-0-0-1-0-3-0.html")

def fetch_dd373():
    info = {"name": "DD373", "status": "部分接入", "status_detail": "",
            "price_type": "售价", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    candidates = []
    try:
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        })
        s.get("https://www.dd373.com/", timeout=TIMEOUT)  # 过 WAF 拿 cookie
        time.sleep(1.5)
        r = s.get(DD373_LIST_URL, timeout=TIMEOUT,
                  headers={"Referer": "https://www.dd373.com/"})
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("div.goods-list-item.zh-goods-item")
        info["fetched_count"] = len(cards)
        for c in cards:
            try:
                attrs = {}
                for p in c.select("p.line-height14"):
                    lab = p.select_one(".label-title")
                    val = p.select_one(".font12")
                    if lab and val:
                        attrs[lab.get_text(strip=True).rstrip(":：")] = val.get_text(strip=True)
                href = c.select_one("a[href*='/detail-']")
                price_el = c.select_one(".goods-price")
                price = parse_price(price_el.get_text()) if price_el else None
                # 哈夫币
                hf = attrs.get("哈夫币数量", "")
                coins_M = None
                mh = re.match(r"(\d+(?:\.\d+)?)\s*([W万亿])", hf)
                if mh:
                    v = float(mh.group(1)); u = mh.group(2)
                    if u == "亿": coins_M = v * 100
                    else: coins_M = v / 100.0  # W/万 -> M
                pub = None
                lbl = c.find(string=re.compile("发布时间"))
                if lbl:
                    nxt = lbl.find_next("span")
                    if nxt: pub = nxt.get_text(strip=True)
                if not href or not href.get("href"):
                    continue
                url = "https://www.dd373.com" + href["href"]
                a = {
                    "platform": "DD373", "url": url,
                    "title": (c.get_text(" ", strip=True))[:120],
                    "pure_coins_M": coins_M,
                    "awm_bullets": None,
                    "insurance_grids": None,
                    "weight_level": parse_lv(attrs.get("靶场")),
                    "stamina_level": parse_lv(attrs.get("训练中心")),
                    "rent": price, "deposit": 0, "price_type": "售价",
                    "publish_time": pub, "fetch_time": now_str(),
                }
                # 粗筛：纯币≥80M 且 售价≤500
                if coins_M is not None and price is not None and coins_M >= 80 and price <= 500:
                    candidates.append(a)
            except Exception as e:
                log(f"  dd373 card parse err: {e}")
        # 详情页补全（上限10条）
        detail_filled = 0
        for a in candidates[:10]:
            try:
                time.sleep(1.2)
                rd = s.get(a["url"], timeout=TIMEOUT,
                           headers={"Referer": DD373_LIST_URL})
                ds = BeautifulSoup(rd.text, "html.parser")
                dmap = {}
                for ai in ds.select("div.goods-item-attr div.attr-item"):
                    ps = ai.select("p")
                    if len(ps) >= 2:
                        dmap[ps[1].get_text(strip=True)] = ps[0].get_text(strip=True)
                if "靶场" in dmap:
                    a["weight_level"] = parse_lv(dmap.get("靶场"))
                if "训练中心" in dmap:
                    a["stamina_level"] = parse_lv(dmap.get("训练中心"))
                if "哈夫币数量" in dmap:
                    mh = re.match(r"(\d+(?:\.\d+)?)\s*([W万亿])", dmap["哈夫币数量"])
                    if mh:
                        v = float(mh.group(1)); u = mh.group(2)
                        a["pure_coins_M"] = v * 100 if u == "亿" else v / 100.0
                detail_filled += 1
            except Exception as e:
                log(f"  dd373 detail err {a['url']}: {e}")
        good = [a for a in candidates if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"列表+详情可获：纯币/靶场(负重)/训练中心(体力)/售价/发布时间；"
            f"实测详情页HTML无安全箱格数与AWM子弹数字段（在商品截图中，文本不可得）；"
            f"候选粗筛{len(candidates)}条，详情补全{detail_filled}条。"
            f"故安全箱格数、AWM子弹无法脚本判定→不能产出确认好号")
        log(f"DD373: fetched={len(cards)}, candidates={len(candidates)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"DD373 FAILED: {e}")
        traceback.print_exc()
    return info, candidates


# ---------------------------------------------------------------------------
# 盼之 pzds.com（售号平台，price_type=售价）
# ---------------------------------------------------------------------------
def fetch_pzds():
    info = {"name": "盼之代售", "status": "部分接入", "status_detail": "",
            "price_type": "售价", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    candidates = []
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        r = s.get("https://www.pzds.com/goodsList/391/6", timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("div.newGoodsList_box")
        # 按文档顺序配对 goodsDetails 链接
        detail_links = re.findall(r"/goodsDetails/([A-Z0-9]+)/", r.text)
        info["fetched_count"] = len(cards)
        for idx, c in enumerate(cards):
            try:
                tt = c.select_one(".title-text")
                pr = c.select_one(".newGoodsList_box_pir")
                title = tt.get_text(strip=True) if tt else ""
                price = parse_price(pr.get_text()) if pr else None
                coins_M = extract_coins_to_M(title)
                gn = detail_links[idx] if idx < len(detail_links) else None
                if not gn:
                    continue
                url = f"https://www.pzds.com/goodsDetails/{gn}/6"
                # 相对时间（"x分钟前发布"）不精确，详情页可取 onStandTime
                a = {
                    "platform": "盼之", "url": url, "title": title[:120],
                    "pure_coins_M": coins_M, "awm_bullets": None,
                    "insurance_grids": None, "weight_level": None,
                    "stamina_level": None, "rent": price, "deposit": 0,
                    "price_type": "售价", "publish_time": None,
                    "fetch_time": now_str(),
                }
                if coins_M is not None and price is not None and coins_M >= 80 and price <= 500:
                    candidates.append(a)
            except Exception as e:
                log(f"  pzds card parse err: {e}")
        # 详情页补全（上限10条）
        detail_filled = 0
        for a in candidates[:10]:
            try:
                time.sleep(1.0)
                rd = s.get(a["url"], timeout=TIMEOUT)
                # onStandTime
                m = re.search(r'onStandTime:\\?"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})', rd.text)
                if m:
                    a["publish_time"] = m.group(1).replace("T", " ")
                # 详情页实测：靶场/训练中心结构化字段(single4/5 等)在多数上架中为空，
                # 安全箱格数/AWM子弹数无对应文本字段（在截图中）。best-effort 提取。
                detail_filled += 1
            except Exception as e:
                log(f"  pzds detail err {a['url']}: {e}")
        good = [a for a in candidates if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            "列表可获：哈夫币(标题正则→M)/售价/详情链接；"
            f"实测详情页：靶场/训练中心结构化字段多为空，安全箱格数与AWM子弹数无文本字段（以游戏内数据为准，详见图）；"
            f"候选粗筛{len(candidates)}条，详情补全{detail_filled}条。缺字段→不能产出确认好号")
        log(f"盼之: fetched={len(cards)}, candidates={len(candidates)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"盼之 FAILED: {e}")
        traceback.print_exc()
    return info, candidates


# ---------------------------------------------------------------------------
# 换号吧 huanhaoba.com（售号平台，price_type=售价）
# ---------------------------------------------------------------------------
def fetch_hhb():
    info = {"name": "换号吧", "status": "部分接入", "status_detail": "",
            "price_type": "售价", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    candidates = []
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        r = s.get("https://www.huanhaoba.com/gameid/2735/0-0-0-0-0-0-0-0.shtml",
                  timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in soup.select("a[href*='/product.shtml?id=']"):
            href = a["href"]
            # 去掉跟踪参数
            href = href.split("&adminType")[0]
            if href not in links:
                links.append(href)
        info["fetched_count"] = len(links)
        for href in links:
            try:
                # 标题在 h4.productTitile
                a_tag = soup.find("a", href=lambda h: h and h.startswith(href))
                title = ""
                price = None
                if a_tag:
                    li = a_tag.find_parent("li") or a_tag.parent
                    if li:
                        h4 = li.select_one("h4.productTitile, h4")
                        if h4: title = h4.get_text(strip=True)
                        h3 = li.select_one("h3.hhb-list-price")
                        if h3: price = parse_price(h3.get_text())
                coins_M = extract_coins_to_M(title)
                url = "https://www.huanhaoba.com" + href
                acc = {
                    "platform": "换号吧", "url": url, "title": title[:120],
                    "pure_coins_M": coins_M, "awm_bullets": None,
                    "insurance_grids": None, "weight_level": None,
                    "stamina_level": None, "rent": price, "deposit": 0,
                    "price_type": "售价", "publish_time": None,
                    "fetch_time": now_str(),
                }
                if coins_M is not None and price is not None and coins_M >= 80 and price <= 500:
                    candidates.append(acc)
            except Exception as e:
                log(f"  hhb item parse err: {e}")
        detail_filled = 0
        for a in candidates[:10]:
            try:
                time.sleep(1.0)
                rd = s.get(a["url"], timeout=TIMEOUT)
                # 实测：详情页同样写"安全箱等时效性道具以游戏内数据为准，详情请看图"，无结构化字段
                detail_filled += 1
            except Exception as e:
                log(f"  hhb detail err {a['url']}: {e}")
        good = [a for a in candidates if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            "列表可获：哈夫币(标题正则→M)/售价/详情链接；"
            "实测详情页：安全箱/靶场/训练中心/AWM均写'以游戏内数据为准，详情请看图'，HTML文本无结构化字段；"
            f"候选粗筛{len(candidates)}条，详情补全{detail_filled}条。缺字段→不能产出确认好号")
        log(f"换号吧: fetched={len(links)}, candidates={len(candidates)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"换号吧 FAILED: {e}")
        traceback.print_exc()
    return info, candidates


# ---------------------------------------------------------------------------
# 结构化 API 通用抓取（ysc9雅俗 / 61xm星铭 同构后端）
# 接口：POST {api_url}/prod-api/web/goods/info/list
# 字段：insurance(保险"9格")/shootingRangeLevel(负重"6级")/trainingCenterLevel(体力"6级")
#       awNum(AWM子弹)/haffronNum(哈夫币,单位=万)/salePrice(售价)/deposit(押金)
#       imgUrl(截图,逗号分隔)/code(商品单号)/title
# ---------------------------------------------------------------------------
def fetch_struct_api(name, base_url, h5_url, max_pages=3,
                     price_field="salePrice", price_type="售价",
                     detail_path="/#/pages/goods/index"):
    info = {"name": name, "status": "已接入", "status_detail": "",
            "price_type": price_type, "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Origin": base_url,
            "Referer": base_url + "/",
        }
        all_rows = []
        for page in range(1, max_pages + 1):
            body = {"pageNum": page, "pageSize": 20}
            r = requests.post(f"{base_url}/prod-api/web/goods/info/list",
                              headers=headers, json=body, timeout=TIMEOUT)
            j = r.json()
            if j.get("code") != 200:
                raise RuntimeError(f"code={j.get('code')} msg={j.get('msg')}")
            rows = j.get("rows") or []
            all_rows.extend(rows)
            if len(rows) < 20:
                break
            time.sleep(1.0)
        info["fetched_count"] = len(all_rows)
        for d in all_rows:
            try:
                # 纯币：haffronNum 单位是"万"（例 46000 = 46000万 = 460M）
                coins = d.get("haffronNum")
                coins_M = round(float(coins) / 100.0, 1) if coins not in (None, "") else None
                # 保险箱：insurance "9格" → 9；"4格"→4
                ins = str(d.get("insurance") or "")
                m_ins = re.search(r"(\d+)\s*格", ins)
                grids = int(m_ins.group(1)) if m_ins else None
                # 负重(靶场)/体力(训练中心)：'6级'/'7级'
                weight = parse_lv("Lv." + str(d.get("shootingRangeLevel") or "")) \
                    if d.get("shootingRangeLevel") else None
                stamina = parse_lv("Lv." + str(d.get("trainingCenterLevel") or "")) \
                    if d.get("trainingCenterLevel") else None
                if weight is None and d.get("shootingRangeLevel"):
                    mw = re.search(r"(\d+)", str(d.get("shootingRangeLevel")))
                    if mw: weight = int(mw.group(1))
                if stamina is None and d.get("trainingCenterLevel"):
                    ms = re.search(r"(\d+)", str(d.get("trainingCenterLevel")))
                    if ms: stamina = int(ms.group(1))
                awm = d.get("awNum")
                try:
                    awm = int(awm)
                except (TypeError, ValueError):
                    awm = None
                price = d.get(price_field)
                price = float(price) if price not in (None, "") else None
                dep = d.get("deposit")
                dep = float(dep) if dep not in (None, "") else 0.0
                code = d.get("code") or str(d.get("id") or "")
                shots = [s.strip() for s in (d.get("imgUrl") or "").split(",") if s.strip()]
                img = shots[0].replace("http://", "https://") if shots else None
                gid = d.get("id")
                accounts.append({
                    "platform": name,
                    "url": f"{h5_url}{detail_path}?id={gid}" if gid is not None
                           else f"{h5_url}{detail_path}",
                    "goods_code": code,
                    "title": (d.get("title") or "")[:120],
                    "pure_coins_M": coins_M,
                    "awm_bullets": awm,
                    "insurance_grids": grids,
                    "weight_level": weight,
                    "stamina_level": stamina,
                    "rent": price,
                    "deposit": dep,
                    "price_type": price_type,
                    "publish_time": d.get("createTime"),
                    "fetch_time": now_str(),
                    "image_url": img,
                })
            except Exception as e:
                log(f"  {name} row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"结构化API：保险/负重/体力/AWM/纯币/{price_type}/押金/截图/单号全字段可解析；"
            f"已抓{len(all_rows)}条")
        log(f"{name}: fetched={len(all_rows)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"{name} FAILED: {e}")
        traceback.print_exc()
    return info, accounts


def fetch_ysc9():
    """ysc9.com 雅俗平台"""
    return fetch_struct_api("ysc9雅俗", "https://ysc9.com", "https://ysc9.com")


def fetch_61xm():
    """61xm.cn 星铭代售平台"""
    return fetch_struct_api("61xm星铭", "https://61xm.cn", "https://61xm.cn")


def fetch_ys9d():
    """ys9d.com 艺术代售（哈夫租售/租号列表，租金= sellerPrice）"""
    return fetch_struct_api(
        "ys9d艺术", "https://h5.ys9d.com", "https://h5.ys9d.com",
        price_field="sellerPrice", price_type="租金",
        detail_path="/pages/rent/goodsDetail")


def fetch_98lj():
    """98lj.com 凌绝代售（三角洲租号专区，租金= sellerPrice，与 ys9d 同套系统）"""
    return fetch_struct_api(
        "98lj凌绝", "https://h5.98lj.com", "https://h5.98lj.com",
        price_field="sellerPrice", price_type="租金",
        detail_path="/pages/rent/goodsDetail")


# ---------------------------------------------------------------------------
# 叮当商行 dingdangsjz.com（公开接口 tt_goods_api.php，租金+押金，字段齐全）
# ---------------------------------------------------------------------------
def fetch_dingdang():
    info = {"name": "叮当商行", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {"User-Agent": UA}
        all_rows = []
        for offset in range(0, 800, 40):
            url = ("https://dingdangsjz.com/tt_goods_api.php?action=list&fast=1"
                   "&status=approved&lite=1&limit=40&offset=" + str(offset))
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            j = r.json()
            items = j.get("items") or []
            all_rows.extend(items)
            if len(items) < 40:
                break
            time.sleep(0.8)
        info["fetched_count"] = len(all_rows)

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def _lv(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        for it in all_rows:
            try:
                p = it.get("payload") or {}
                hafu = str(p.get("hafuText") or "")
                mh = re.search(r"(\d+(?:\.\d+)?)", hafu)
                coins_M = float(mh.group(1)) if mh else None
                ins = str(p.get("insurance") or "")
                m_ins = re.search(r"(\d+)\s*格", ins)
                grids = int(m_ins.group(1)) if m_ins else None
                weight = _lv(p.get("burden"))
                stamina = _lv(p.get("physicalStrength"))
                awm = _lv(p.get("bullet"))
                rent = _f(p.get("rent"))
                dep = _f(p.get("deposit")) or 0.0
                code = it.get("id") or ""
                shots = p.get("images") or []
                img = shots[0].replace("http://", "https://") if shots else None
                accounts.append({
                    "platform": "叮当商行",
                    "url": f"https://dingdangsjz.com/#/detail?id={code}",
                    "goods_code": code,
                    "title": (p.get("title") or "")[:120],
                    "pure_coins_M": coins_M,
                    "awm_bullets": awm,
                    "insurance_grids": grids,
                    "weight_level": weight,
                    "stamina_level": stamina,
                    "rent": rent,
                    "deposit": dep,
                    "price_type": "租金",
                    "publish_time": it.get("created_date"),
                    "fetch_time": now_str(),
                    "image_url": img,
                })
            except Exception as e:
                log(f"  叮当 row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"公开接口 tt_goods_api.php：纯币/保险/体力/负重/AWM/租金/押金全字段；"
            f"已抓{len(all_rows)}条")
        log(f"叮当商行: fetched={len(all_rows)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"叮当 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


# ---------------------------------------------------------------------------
# 皓星商行 xinxinsjz88.com（公开接口 /api/front/accounts，租金+押金，字段齐全）
# ---------------------------------------------------------------------------
def fetch_xinhe():
    info = {"name": "皓星商行", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {"User-Agent": UA}
        all_rows = []
        page = 1
        while page <= 40:
            url = ("https://xinxinsjz88.com/api/front/accounts?page_no=" + str(page)
                   + "&page_size=25&platform_code=platform_b&sort_key=default&sort_direction=desc")
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            j = r.json()
            data = j.get("data") or {}
            items = data.get("list") or []
            all_rows.extend(items)
            total = data.get("total") or 0
            if not items or len(all_rows) >= total:
                break
            page += 1
            time.sleep(0.8)
        info["fetched_count"] = len(all_rows)

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def _lv(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        for d in all_rows:
            try:
                coins_M = _f(d.get("coinAmountText"))
                grids = _lv(d.get("insuranceText"))
                weight = _lv(d.get("carryWeightText"))
                stamina = _lv(d.get("staminaText"))
                awm = _lv(d.get("awmText"))
                rent = _f(d.get("coinRentPrice"))
                dep = _f(d.get("depositAmount")) or 0.0
                code = str(d.get("accountNo") or "")
                accounts.append({
                    "platform": "皓星商行",
                    "url": "https://xinxinsjz88.com/?accountNo=" + code,
                    "goods_code": code,
                    "title": (f"纯币{d.get('coinAmountText')}M 保险{grids}格 段位{d.get('rankName')} "
                             f"体力{stamina}级 负重{weight}级 AWM{awm} 皮肤{d.get('skinText')}")[:120],
                    "pure_coins_M": coins_M,
                    "awm_bullets": awm,
                    "insurance_grids": grids,
                    "weight_level": weight,
                    "stamina_level": stamina,
                    "rent": rent,
                    "deposit": dep,
                    "price_type": "租金",
                    "publish_time": d.get("lastShineAt"),
                    "fetch_time": now_str(),
                    "image_url": None,
                })
            except Exception as e:
                log(f"  皓星 row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"公开接口 /api/front/accounts：纯币/保险/体力/负重/AWM/租金/押金全字段；"
            f"已抓{len(all_rows)}条")
        log(f"皓星商行: fetched={len(all_rows)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"皓星 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


# ---------------------------------------------------------------------------
# 准星代售 zxfps.com（公开接口 /api/goods/getGoodsList，资产号租赁：租金+押金）
# ---------------------------------------------------------------------------
def fetch_zxfps():
    info = {"name": "准星代售", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {"User-Agent": UA}
        all_rows = []
        page = 1
        while page <= 40:
            url = ("https://zxfps.com/api/goods/getGoodsList?pageSize=20"
                   "&gameId=177&goodsTypeId=24&pages=" + str(page))
            # 准星代售接口对连续分页请求存在间歇性超时（约20%概率），单页失败重试2次
            j = None
            for attempt in range(3):
                try:
                    r = requests.get(url, headers=headers, timeout=TIMEOUT)
                    j = r.json()
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(1.0)
            data = j.get("data") or {}
            items = data.get("goodsList") or []
            all_rows.extend(items)
            total = data.get("total") or 0
            if not items or len(all_rows) >= total or page >= 40:
                break
            page += 1
            time.sleep(0.8)
        info["fetched_count"] = len(all_rows)

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def _lv(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                mm = re.search(r"(\d+)", str(v or ""))
                return int(mm.group(1)) if mm else None

        for d in all_rows:
            try:
                coins_M = _f(d.get("currencyqufuLevelItn"))
                weight = _lv(d.get("rangeName"))
                stamina = _lv(d.get("centerName"))
                awm = _lv(d.get("mattributeLevel"))
                rent = _f(d.get("price"))
                dep = _f(d.get("depositAmount")) or 0.0
                code = str(d.get("goodsSn") or "")
                safety = str(d.get("safetyName") or "")
                m_s = re.search(r"(\d+)\s*[*xX×]\s*(\d+)", safety)
                grids = (int(m_s.group(1)) * int(m_s.group(2))) if m_s else None
                days = d.get("leaseDay")
                accounts.append({
                    "platform": "准星代售",
                    "url": "https://zxfps.com/?goodsSn=" + code,
                    "goods_code": code,
                    "title": (f"纯币{d.get('currencyqufuLevelItn')}M 总资产{d.get('warehousequfuLevelItn')}M "
                             f"保险{safety} 体力{stamina}级 负重{weight}级 AWM{awm} 段位{d.get('danName')} "
                             f"租期{days}天 {d.get('knifeNames')}")[:120],
                    "pure_coins_M": coins_M,
                    "awm_bullets": awm,
                    "insurance_grids": grids,
                    "weight_level": weight,
                    "stamina_level": stamina,
                    "rent": rent,
                    "deposit": dep,
                    "price_type": "租金",
                    "publish_time": d.get("createTime"),
                    "fetch_time": now_str(),
                    "image_url": None,
                })
            except Exception as e:
                log(f"  准星 row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"公开接口 /api/goods/getGoodsList（资产号租赁）：纯币/保险/体力/负重/AWM/租金/押金全字段；"
            f"已抓{len(all_rows)}条")
        log(f"准星代售: fetched={len(all_rows)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"准星 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


# ---------------------------------------------------------------------------
# 三一商行 3f1.com（服务端渲染 /zuhao 租号大厅，租金+押金，字段全）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 夏日商行 xiar7.com（Nuxt API：/api/game_account/lists，租金+押金，字段全）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 泓星代售 hxfps.com（Vite SPA：api.hxfps.com/app-api/product/sanjiaozhou-spu/page）
# 纯币haver_coin(万) / 保险season_slot / 体力stamina / 负重burden / AWM / 租金price(分) / 押金deposit(分)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 哈夫克基地 hafuke.com.cn（服务端渲染 rental.php，卡片内嵌 data-info JSON）
# 纯币pure_coins(万) / 保险season_insurance / 体力负重stamina_load("ab"=a体b负) / AW / 租金rent_price
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 阿龙商行 vip.along888.cn（Vite SPA，REST API account-rentals 按天租：租金+押金）
# 字段：hafCoinAmount(M) / safeBoxItems(安全箱N格) / specialItems(体力X级,负重X级)
#       awAmmoCount(AW) / rentPrice租金 / deposit押金 / totalAmount合计 / rentDays租期天
# ---------------------------------------------------------------------------
def fetch_along888():
    info = {"name": "阿龙商行", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        url = ("https://vip.along888.cn/api/account-rentals"
               "?page=1&limit=100&sortBy=createdAt&sortOrder=DESC")
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError("HTTP " + str(r.status_code))
        j = r.json()
        rows = (j.get("data") or {}).get("rows") or []
        for row in rows:
            try:
                if str(row.get("status") or "") != "on_shelf":
                    continue
                title = str(row.get("title") or "")
                pn = str(row.get("productNumber") or "")
                special = str(row.get("specialItems") or "")
                box = str(row.get("safeBoxItems") or "")
                m_box = re.search(r"\u5b89\u5168\u7bb1\s*(\d+)\s*\((\d+)x(\d+)\)", box)
                m_sp = re.search(r"(\d+)\u7ea7", special)
                m_st = re.search(r"\u4f53\u529b(\d+)\u7ea7", special)
                m_wt = re.search(r"\u8d1f\u91cd(\d+)\u7ea7", special)
                def _f(v, d=0.0):
                    try: return float(v)
                    except Exception: return d
                haf = _f(row.get("hafCoinAmount"))
                accounts.append({
                    "platform": "阿龙商行",
                    "url": "https://vip.along888.cn/rent",
                    "goods_code": pn,
                    "title": (title or pn)[:120],
                    "pure_coins_M": haf if haf else None,
                    "awm_bullets": int(row.get("awAmmoCount") or 0) or None,
                    "insurance_grids": int(m_box.group(1)) if m_box else None,
                    "weight_level": int(m_wt.group(1)) if m_wt else None,
                    "stamina_level": int(m_st.group(1)) if m_st else None,
                    "rent": _f(row.get("rentPrice")) if row.get("rentPrice") else None,
                    "deposit": _f(row.get("deposit")),
                    "price_type": "租金",
                    "publish_time": row.get("createdAt"),
                    "fetch_time": now_str(),
                    "image_url": None,
                })
            except Exception as e:
                log(f"  阿龙 row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["fetched_count"] = len(accounts)
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"REST API account-rentals（按天租）：纯币M/安全箱格/体力负重/AW/租金/押金/租期天全字段；共{len(accounts)}条在售")
        log(f"阿龙商行: fetched={len(accounts)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"阿龙 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


def fetch_hafuke():
    info = {"name": "哈夫克基地", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {"User-Agent": UA}
        r = requests.get("https://hafuke.com.cn/rental.php", headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError("HTTP " + str(r.status_code))
        s = r.text
        items = re.findall(r"data-info='(\{.*?\})'", s, re.S)
        if not items:
            items = re.findall(r'data-info="(\{.*?\})"', s, re.S)
        import html as _h
        for it in items:
            try:
                d = json.loads(_h.unescape(it))
                sl = str(d.get("stamina_load") or "0")
                m_sl = re.search(r"(\d)(\d)", sl)
                try:
                    pc = float(d.get("pure_coins") or 0)
                except Exception:
                    pc = 0.0
                rtype = d.get("rent_type")
                price = 0.0
                try:
                    price = float(d.get("rent_price") or 0)
                except Exception:
                    price = 0.0
                if rtype == "time" and not price:
                    try:
                        price = float(d.get("price_per_hour") or 0) * 24
                    except Exception:
                        price = 0.0
                dep = 0.0
                try:
                    dep = float(d.get("deposit_amount") or 0)
                except Exception:
                    dep = 0.0
                accounts.append({
                    "platform": "哈夫克基地",
                    "url": "https://hafuke.com.cn/rental.php",
                    "goods_code": str(d.get("serial_no") or d.get("id")),
                    "title": ("%s %sM %s格 %s体%s负" % (
                        d.get("rank_info") or "", pc / 100.0,
                        d.get("season_insurance") or 0,
                        m_sl.group(1) if m_sl else "0",
                        m_sl.group(2) if m_sl else "0"))[:120],
                    "pure_coins_M": pc / 100.0 if pc else None,
                    "awm_bullets": int(d.get("aw_bullets") or 0) or None,
                    "insurance_grids": int(d.get("season_insurance") or 0) or None,
                    "weight_level": int(m_sl.group(2)) if m_sl else None,
                    "stamina_level": int(m_sl.group(1)) if m_sl else None,
                    "rent": price if price else None,
                    "deposit": dep,
                    "price_type": "租金",
                    "publish_time": d.get("created_at"),
                    "fetch_time": now_str(),
                    "image_url": None,
                })
            except Exception as e:
                log(f"  哈夫克 row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["fetched_count"] = len(accounts)
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"服务端渲染 rental.php 卡片JSON：纯币/保险/体力负重/AW/租金/押金全字段；共{len(accounts)}条")
        log(f"哈夫克基地: fetched={len(accounts)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"哈夫克 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


def fetch_hxfps():
    info = {"name": "泓星代售", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {"User-Agent": UA, "tenant-id": "1", "Accept-Language": "zh-CN"}
        seen = set()
        total = None
        for page_no in range(1, 12):
            url = ("https://api.hxfps.com/app-api/product/sanjiaozhou-spu/page"
                   "?categoryId=84&pageNo=%d&pageSize=20" % page_no)
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code != 200:
                break
            d = r.json()
            if d.get("code") != 0:
                break
            data = d.get("data") or {}
            if total is None:
                total = data.get("total")
            ls = data.get("list") or data.get("records") or []
            if not ls:
                break
            for x in ls:
                gid = str(x.get("id"))
                if gid in seen:
                    continue
                seen.add(gid)
                try:
                    av = x.get("attrValues") or {}
                    def _num(v):
                        try:
                            return int(v)
                        except Exception:
                            return None
                    pc = _num(av.get("haver_coin"))
                    ins = _num(av.get("season_slot"))
                    stm = _num(av.get("stamina"))
                    bur = _num(av.get("burden"))
                    awm = _num(av.get("awm_bullets"))
                    price = x.get("price") or 0
                    dep = x.get("deposit") or 0
                    accounts.append({
                        "platform": "泓星代售",
                        "url": "https://hxfps.com/account-trade?id=" + gid,
                        "goods_code": x.get("productNo") or gid,
                        "title": (x.get("name") or "")[:120],
                        "pure_coins_M": pc / 100.0 if pc else None,
                        "awm_bullets": awm,
                        "insurance_grids": ins,
                        "weight_level": bur,
                        "stamina_level": stm,
                        "rent": price / 100.0 if price else None,
                        "deposit": dep / 100.0 if dep else 0.0,
                        "price_type": "租金",
                        "publish_time": None,
                        "fetch_time": now_str(),
                        "image_url": None,
                    })
                except Exception as e:
                    log(f"  泓星 row parse err: {e}")
            if len(ls) < 20 or (total and len(seen) >= total):
                break
            time.sleep(0.5)
        good = [a for a in accounts if is_good(a)]
        info["fetched_count"] = len(accounts)
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"SPA API product/sanjiaozhou-spu/page：纯币/保险/体力/负重/AWM/租金/押金全字段；"
            f"共{total or 0}条")
        log(f"泓星代售: fetched={len(accounts)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"泓星 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


def fetch_xiar():
    info = {"name": "夏日商行", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {"User-Agent": UA}
        seen = set()
        for page_no in range(1, 4):
            url = ("https://xiar7.com/api/game_account/lists?page_no=%d&page_size=100"
                   "&keyword=&region=&order_by=&order_type=desc" % page_no)
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code != 200:
                break
            d = r.json()
            ls = (d.get("data") or {}).get("lists") or []
            if not ls:
                break
            for x in ls:
                gid = str(x.get("id"))
                if gid in seen:
                    continue
                seen.add(gid)
                try:
                    safe = str(x.get("safe_box") or "")
                    m_ins = re.search(r"(\d+)", safe)
                    stm = str(x.get("stamina") or "")
                    m_sta = re.search(r"(\d+)", stm)
                    wei = str(x.get("weight") or "")
                    m_wei = re.search(r"(\d+)", wei)
                    pc = x.get("pure_coins") or 0
                    try:
                        pc = float(pc)
                    except Exception:
                        pc = 0.0
                    accounts.append({
                        "platform": "夏日商行",
                        "url": "https://xiar7.com/pages/account/detail?id=" + gid,
                        "goods_code": gid,
                        "title": ("%s %sM %s 租%d天" % (
                            x.get("rank") or "", pc / 100.0, safe, x.get("rental_days") or 0))[:120],
                        "pure_coins_M": pc / 100.0 if pc else None,
                        "awm_bullets": int(x.get("awm_bullets") or 0) or None,
                        "insurance_grids": int(m_ins.group(1)) if m_ins else None,
                        "weight_level": int(m_wei.group(1)) if m_wei else None,
                        "stamina_level": int(m_sta.group(1)) if m_sta else None,
                        "rent": float(x.get("sale_price") or 0) or None,
                        "deposit": float(x.get("deposit") or 0) or 0.0,
                        "price_type": "租金",
                        "publish_time": x.get("shelf_time"),
                        "fetch_time": now_str(),
                        "image_url": None,
                    })
                except Exception as e:
                    log(f"  夏日 row parse err: {e}")
            if len(ls) < 100:
                break
            time.sleep(0.6)
        good = [a for a in accounts if is_good(a)]
        info["fetched_count"] = len(accounts)
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"Nuxt API /api/game_account/lists：纯币/保险/体力/负重/AWM/租金/押金/租期全字段；"
            f"共{(d.get('data') or {}).get('count') or 0}条")
        log(f"夏日商行: fetched={len(accounts)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"夏日 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


def fetch_3f1():
    info = {"name": "三一商行", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {"User-Agent": UA}
        html_all = []
        for page in range(1, 14):
            url = "https://www.3f1.com/zuhao?sort=new&page=" + str(page)
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code != 200:
                break
            s = r.text
            cards = re.findall(
                r'<div class="dl-acc-card">(.*?)(?=<div class="dl-acc-card">|\s*<div class="dl-pagination"|$)', s, re.S)
            html_all.extend(cards)
            if not cards:
                break
            time.sleep(0.8)
        info["fetched_count"] = len(html_all)
        for c in html_all:
            try:
                m_id = re.search(r'href="/zuhao/(\d+)"', c)
                m_aria = re.search(r'aria-label="([^"]+)"', c)
                m_desc = re.search(r'am-desc">\s*<span>([^<]+)</span>', c)
                m_pure = re.search(r'<b>([\d.]+)M</b><span>纯币</span>', c)
                m_ins = re.search(r'<b>(\d+)格</b><span>保险</span>', c)
                m_awm = re.search(r'<b>(\d+)发</b><span>AW</span>', c)
                m_sta = re.search(r'<b>(\d+)</b><span>体力</span>', c)
                m_wei = re.search(r'<b>(\d+)</b><span>负重</span>', c)
                m_rent = re.search(r'<span>租金</span><b>¥([\d.]+)</b>', c)
                m_dep = re.search(r'保证金<b>¥([\d.]+)</b>', c)
                m_days = re.search(r'租期(\d+)天', c)
                title = (m_aria.group(1) if m_aria else (m_desc.group(1) if m_desc else ""))
                code = m_id.group(1) if m_id else ""
                accounts.append({
                    "platform": "三一商行",
                    "url": "https://www.3f1.com/zuhao/" + code,
                    "goods_code": code,
                    "title": title[:120],
                    "pure_coins_M": float(m_pure.group(1)) if m_pure else None,
                    "awm_bullets": int(m_awm.group(1)) if m_awm else None,
                    "insurance_grids": int(m_ins.group(1)) if m_ins else None,
                    "weight_level": int(m_wei.group(1)) if m_wei else None,
                    "stamina_level": int(m_sta.group(1)) if m_sta else None,
                    "rent": float(m_rent.group(1)) if m_rent else None,
                    "deposit": float(m_dep.group(1)) if m_dep else 0.0,
                    "price_type": "租金",
                    "publish_time": None,
                    "fetch_time": now_str(),
                    "image_url": None,
                })
            except Exception as e:
                log(f"  三一 row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"服务端渲染 /zuhao 租号大厅：纯币/保险/体力/负重/AWM/租金/押金全字段；"
            f"已抓{len(html_all)}条")
        log(f"三一商行: fetched={len(html_all)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"三一 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


# ---------------------------------------------------------------------------
# 安易游 ayy66.com（H5 租号平台「安易游」，Taro 框架，公开接口 /api/client/product/list）
# 需带浏览器 UA + Origin/Referer；filters.game_type="delta_force" 只抓三角洲行动；
# safe_box_type: basic=2格/advanced=4格/premium=6格/top=9格；租金=total_sale_price_external，
# 押金=external_deposit_amount；每页上限 50。
# ---------------------------------------------------------------------------
def fetch_ayy66():
    info = {"name": "安易游", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {
            "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                           "Mobile/15E148 Safari/604.1"),
            "Content-Type": "application/json",
            "Origin": "https://www.ayy66.com",
            "Referer": "https://www.ayy66.com/h5/?88",
        }
        SAFE_BOX_GRIDS = {"basic": 2, "advanced": 4, "premium": 6, "top": 9}
        all_rows = []
        page = 1
        page_size = 50
        while page <= 40:
            body = {"page": page, "pageSize": page_size,
                    "filters": {"sold": False, "game_type": "delta_force"}}
            r = requests.post("https://www.ayy66.com/api/client/product/list",
                              headers=headers, json=body, timeout=TIMEOUT)
            j = r.json()
            if j.get("code") != 1:
                log(f"  安易游 page{page} code={j.get('code')} msg={j.get('message')}")
                break
            data = j.get("data") or {}
            items = data.get("list") or []
            all_rows.extend(items)
            total = (data.get("pagination") or {}).get("total") or 0
            if len(items) < page_size or len(all_rows) >= total:
                break
            page += 1
            time.sleep(0.8)
        info["fetched_count"] = len(all_rows)
        for it in all_rows:
            try:
                grids = SAFE_BOX_GRIDS.get(it.get("safe_box_type"))
                rent = float(it.get("total_sale_price_external") or 0)
                dep = float(it.get("external_deposit_amount") or 0)
                shots = it.get("carousel_images") or []
                img = shots[0] if shots else None
                code = it.get("product_no") or str(it.get("id"))
                accounts.append({
                    "platform": "安易游",
                    "url": ("https://www.ayy66.com/h5/pages-sub/product-detail-v3/index?id="
                            + str(it.get("id"))),
                    "goods_code": code,
                    "title": (it.get("display_product_title") or code)[:120],
                    "pure_coins_M": it.get("pure_coin_count"),
                    "awm_bullets": it.get("aw_bullet_count"),
                    "insurance_grids": grids,
                    "weight_level": it.get("shooting_range_level"),
                    "stamina_level": it.get("training_center_level"),
                    "rent": rent,
                    "deposit": dep,
                    "price_type": "租金",
                    "publish_time": it.get("create_time"),
                    "fetch_time": now_str(),
                    "image_url": img,
                })
            except Exception as e:
                log(f"  安易游 row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"公开接口 /api/client/product/list：纯币/保险/训练中心/靶场/AWM/租金/押金全字段；"
            f"已抓{len(all_rows)}条(仅delta_force)")
        log(f"安易游: fetched={len(all_rows)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"安易游 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


def fetch_pangmao():
    info = {"name": "胖猫商行", "status": "已接入", "status_detail": "",
            "price_type": "租金", "fetched_count": 0, "good_count": 0,
            "fetch_time": now_str(), "error": None}
    accounts = []
    try:
        headers = {
            "User-Agent": UA,
            "Referer": "https://pmcm8.com/",
        }
        INSURANCE_GRIDS = {"3*3": 9, "2*3": 6, "2*2": 4, "2*1": 2}
        all_rows = []
        page = 1
        page_size = 50
        while page <= 20:
            url = (f"https://www.pmcm8.com/server/api/missions.php?action=list"
                   f"&client=pc&page={page}&pageSize={page_size}")
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            j = r.json()
            if j.get("code") != 200:
                log(f"  胖猫商行 page{page} code={j.get('code')} msg={j.get('message')}")
                break
            data = j.get("data") or {}
            items = data.get("list") or []
            all_rows.extend(items)
            total = data.get("total") or 0
            if len(items) < page_size or len(all_rows) >= total:
                break
            page += 1
            time.sleep(0.5)
        info["fetched_count"] = len(all_rows)
        for it in all_rows:
            try:
                ins = it.get("insurance_status") or ""
                grids = INSURANCE_GRIDS.get(ins)
                rent = float(it.get("price") or 0)
                dep = float(it.get("deposit_requirement") or 0)
                code = it.get("display_no") or ""
                pure_m = float(it.get("pure_currency_wan") or 0)
                awm = it.get("aw_num") or 0
                stamina = it.get("stamina")
                weight = it.get("weight_capacity")
                accounts.append({
                    "platform": "胖猫商行",
                    "url": f"https://pmcm8.com/#/property/{code}",
                    "goods_code": code,
                    "title": (it.get("title") or code)[:120],
                    "pure_coins_M": pure_m,
                    "awm_bullets": awm,
                    "insurance_grids": grids,
                    "weight_level": weight,
                    "stamina_level": stamina,
                    "rent": rent,
                    "deposit": dep,
                    "price_type": "租金",
                    "publish_time": None,
                    "fetch_time": now_str(),
                    "image_url": it.get("image_url"),
                })
            except Exception as e:
                log(f"  胖猫商行 row parse err: {e}")
        good = [a for a in accounts if is_good(a)]
        info["good_count"] = len(good)
        info["status_detail"] = (
            f"公开接口 /server/api/missions.php?action=list：纯币/保险/体力/负重/AWM/租金/押金全字段；"
            f"已抓{len(all_rows)}条")
        log(f"胖猫商行: fetched={len(all_rows)}, good={len(good)}")
    except Exception as e:
        info["status"] = "暂未接入"
        info["error"] = f"{type(e).__name__}: {e}"
        log(f"胖猫商行 FAILED: {e}")
        traceback.print_exc()
    return info, accounts


# ---------------------------------------------------------------------------
# 暂未接入平台（静态登记）
# ---------------------------------------------------------------------------
NOT_CONNECTED = []


def build_not_connected():
    out = []
    for name, status, reason in NOT_CONNECTED:
        out.append({"name": name, "status": status, "status_detail": reason,
                    "price_type": "-", "fetched_count": 0, "good_count": 0,
                    "fetch_time": now_str(), "error": None})
    return out


# ---------------------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------------------
FAVICON = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
           "<rect width='100' height='100' rx='18' fill='#0d1117'/>"
           "<path d='M28 62 L42 30 L54 52 L62 40 L74 62 Z' fill='#f0b429'/>"
           "<circle cx='50' cy='50' r='40' fill='none' stroke='#f0b429' stroke-width='3' opacity='.4'/></svg>")
FAVICON_URI = "data:image/svg+xml," + _urlquote(FAVICON)


def esc(s):
    return htmlmod.escape(str(s)) if s is not None else ""


def fmt(v, suffix=""):
    if v is None:
        return '<span class="na">—</span>'
    if isinstance(v, float):
        return f"{v:g}{suffix}"
    return f"{v}{suffix}"


# ===========================================================================
# 分区表格版页面渲染（简洁明了 · 一屏更多号 · 按平台分区）
# ===========================================================================
def render_html_v2(data):
    generated = data["generated_at"]
    good = data.get("good_accounts", [])
    gone = data.get("gone_accounts", [])
    all_acc = data.get("all_accounts", [])
    platforms = data["platforms"]
    rules = data.get("rules", GOOD_RULES)

    def esc(s):
        return htmlmod.escape(str(s)) if s is not None else ""

    def fmt(v, suffix=""):
        if v is None:
            return '<span class="na">—</span>'
        if isinstance(v, float):
            return f"{v:g}{suffix}"
        return f"{v}{suffix}"

    # ---- 好号表格（紧凑行） ----
    if good:
        rows = []
        for a in good:
            rows.append(f"""
            <tr data-url="{esc(a['url'])}" class="good-row">
              <td class="c-code"><button type="button" class="code-btn" onclick="copyCode('{esc(a.get('goods_code') or '')}')" title="点击复制">{(a.get('goods_code') or '')[:18]}</button></td>
              <td class="c-plat">{esc(a['platform'])}</td>
              <td>{fmt(a['pure_coins_M'],'M')}</td>
              <td>{fmt(a['awm_bullets'])}</td>
              <td>{fmt(a['insurance_grids'])}</td>
              <td>{fmt(a['weight_level'])}</td>
              <td>{fmt(a['stamina_level'])}</td>
              <td>{fmt(a.get('rent'))}</td>
              <td>{fmt(a.get('deposit'))}</td>
              <td class="c-link"><a href="{esc(a['url'])}" target="_blank" rel="noopener">查看</a></td>
            </tr>""")
        good_html = f"""
        <div class="tbl-wrap">
        <table class="tbl" id="good-table">
          <thead><tr>
            <th class="c-code">单号</th><th>平台</th><th>纯币</th><th>AWM</th><th>格</th><th>负重</th><th>体力</th><th>租金</th><th>押金</th><th class="c-link">链接</th>
          </tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table></div>"""
    else:
        good_html = '<div class="empty"><p>当前无符合条件的好号</p><p class="empty-sub">下一轮抓取 1 分钟后自动更新</p></div>'

    # ---- 候选号按平台分区（全部抓到的号，好号高亮） ----
    by_plat = {}
    for a in all_acc:
        by_plat.setdefault(a["platform"], []).append(a)
    plat_sections = []
    order = ["ysc9雅俗", "61xm星铭", "ys9d艺术", "98lj凌绝", "叮当商行", "皓星商行", "准星代售", "三一商行", "夏日商行", "泓星代售", "哈夫克基地", "阿龙商行", "虚贝", "DD373", "盼之", "换号吧"]
    plat_names = sorted(by_plat.keys(), key=lambda p: order.index(p) if p in order else 99)
    for pn in plat_names:
        accs = by_plat[pn]
        good_n = sum(1 for a in accs if a.get("is_good"))
        rows = []
        for a in accs:
            tag = '<span class="tag-ok">好</span>' if a.get("is_good") else ""
            rows.append(f"""
            <tr data-url="{esc(a['url'])}" class="{'good-row' if a.get('is_good') else ''}">
              <td class="c-code"><button type="button" class="code-btn" onclick="copyCode('{esc(a.get('goods_code') or '')}')" title="点击复制">{(a.get('goods_code') or '')[:18]}</button></td>
              <td class="c-plat">{esc(pn)} {tag}</td>
              <td>{fmt(a['pure_coins_M'],'M')}</td>
              <td>{fmt(a['awm_bullets'])}</td>
              <td>{fmt(a['insurance_grids'])}</td>
              <td>{fmt(a['weight_level'])}</td>
              <td>{fmt(a['stamina_level'])}</td>
              <td>{fmt(a.get('rent'))}</td>
              <td>{fmt(a.get('deposit'))}</td>
              <td class="c-link"><a href="{esc(a['url'])}" target="_blank" rel="noopener">查看</a></td>
            </tr>""")
        plat_sections.append(f"""
        <section class="section">
          <h2>{esc(pn)}<span class="h2-sub">{len(accs)}个 · 好号{good_n}</span></h2>
          <div class="tbl-wrap">
          <table class="tbl" data-plat="{esc(pn)}">
            <thead><tr>
              <th class="c-code">单号</th><th>平台</th><th>纯币</th><th>AWM</th><th>格</th><th>负重</th><th>体力</th><th>租金</th><th>押金</th><th class="c-link">链接</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table></div>
        </section>""")
    cand_html = "\n".join(plat_sections)

    # ---- 已失效表格 ----
    if gone:
        rows = []
        for a in gone:
            rows.append(f"""
            <tr data-url="{esc(a['url'])}" class="gone-row">
              <td class="c-code">{esc(a.get('goods_code') or '—')}</td>
              <td class="c-plat">{esc(a['platform'])}</td>
              <td>{fmt(a['pure_coins_M'],'M')}</td>
              <td>{fmt(a['awm_bullets'])}</td>
              <td>{fmt(a['insurance_grids'])}</td>
              <td>{fmt(a['weight_level'])}</td>
              <td>{fmt(a['stamina_level'])}</td>
              <td>{fmt(a.get('rent'))}</td>
              <td>{fmt(a.get('deposit'))}</td>
              <td class="c-link"><a href="{esc(a['url'])}" target="_blank" rel="noopener">查看</a></td>
            </tr>""")
        gone_html = f"""
        <section class="section" id="gone-sec">
          <h2>已失效<span class="h2-sub">{len(gone)}个 · 被租走/下架</span></h2>
          <div class="tbl-wrap">
          <table class="tbl" id="gone-table">
            <thead><tr>
              <th class="c-code">单号</th><th>平台</th><th>纯币</th><th>AWM</th><th>格</th><th>负重</th><th>体力</th><th>租金</th><th>押金</th><th class="c-link">链接</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table></div>
        </section>"""
    else:
        gone_html = ""

    # ---- 平台状态（紧凑行） ----
    p_rows = []
    for p in platforms:
        st = p["status"]
        badge = {"已接入": "ok", "部分接入": "partial", "暂未接入": "off"}.get(st, "off")
        detail = esc((p.get("status_detail") or p.get("error") or "")[:90])
        p_rows.append(f"""
        <div class="plat-line plat-{badge}">
          <span class="plat-name">{esc(p['name'])}</span>
          <span class="plat-badge b-{badge}">{esc(st)}</span>
          <span class="plat-meta">抓取 {p.get('fetched_count',0)} · 好号 {p.get('good_count',0)}</span>
          <span class="plat-detail">{detail}</span>
        </div>""")
    platforms_html = "\n".join(p_rows)

    rules_html = "".join(f"<span class='rule'>{esc(r)}</span>" for r in rules)

    # ---- 页面 JS：复制单号 + 开关 + 实时分区刷新 ----
    js_script = """<script>
function copyCode(code) {
  var done = function() {
    var el = document.createElement('div');
    el.textContent = '已复制单号 ' + code;
    el.style.cssText = 'position:fixed;left:50%;bottom:80px;transform:translateX(-50%);' +
      'background:#1f6feb;color:#fff;padding:10px 18px;border-radius:8px;font-size:14px;z-index:999;';
    document.body.appendChild(el);
    setTimeout(function() { el.remove(); }, 1600);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(code).then(done).catch(function() { fallbackCopy(code, done); });
  } else { fallbackCopy(code, done); }
}
function fallbackCopy(code, done) {
  var ta = document.createElement('textarea');
  ta.value = code; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch(e) {}
  ta.remove();
}
function fmt(v, s) { return (v===null||v===undefined||v==='') ? '—' : (v + (s||'')); }
function rowHtml(a, cls) {
  var good = a.is_good;
  var tag = good ? '<span class="tag-ok">好</span>' : '';
  var codeBtn = '<button type="button" class="code-btn" onclick="copyCode(\\'' + (a.goods_code||'') + '\\')">' + (String(a.goods_code||'').slice(0,18)) + '</button>';
  return '<tr data-url="' + a.url + '" class="' + (good?'good-row':'') + (cls?' '+cls:'') + '">' +
    '<td class="c-code">' + (good?codeBtn:(String(a.goods_code||'').slice(0,18))) + '</td>' +
    '<td class="c-plat">' + a.platform + ' ' + tag + '</td>' +
    '<td>' + fmt(a.pure_coins_M,'M') + '</td>' +
    '<td>' + fmt(a.awm_bullets) + '</td>' +
    '<td>' + fmt(a.insurance_grids) + '</td>' +
    '<td>' + fmt(a.weight_level) + '</td>' +
    '<td>' + fmt(a.stamina_level) + '</td>' +
    '<td>' + fmt(Number(a.rent)||null) + '</td>' +
    '<td>' + fmt(Number(a.deposit)||null) + '</td>' +
    '<td class="c-link"><a href="' + a.url + '" target="_blank" rel="noopener">查看</a></td>' +
  '</tr>';
}
function refreshState() {
  fetch('api/status').then(function(r) { return r.json(); }).then(function(s) {
    var btn = document.getElementById('btn-toggle');
    var st = document.getElementById('ctrl-state');
    if (s.paused) {
      btn.textContent = '▶ 恢复监控'; btn.classList.add('paused'); btn.disabled = false;
      st.textContent = '已暂停 · 不抓取不推送 · 最后抓取 ' + (s.last_run || '-');
    } else {
      btn.textContent = '⏸ 暂停监控'; btn.classList.remove('paused'); btn.disabled = false;
      st.textContent = '运行中 · 每1分钟 · 最后抓取 ' + (s.last_run || '-') + ' · 好号 ' + s.good_count;
    }
  }).catch(function() {
    var btn = document.getElementById('btn-toggle');
    btn.textContent = '控制接口未连接'; btn.disabled = true;
    document.getElementById('ctrl-state').textContent = '请通过完整版地址访问以启用开关';
  });
}
function togglePause() {
  var btn = document.getElementById('btn-toggle');
  var paused = btn.classList.contains('paused');
  btn.disabled = true; btn.textContent = '处理中…';
  fetch(paused ? 'api/resume' : 'api/pause', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(j) { if (j.ok) { refreshState(); } else { btn.disabled = false; btn.textContent = '操作失败，重试'; } })
    .catch(function() { btn.disabled = false; btn.textContent = '操作失败，重试'; });
}
var seenGood = {}, seenGone = {};
function pollData() {
  fetch('api/data').then(function(r) { return r.json(); }).then(function(d) {
    if (!d.ok) return;
    var sub = document.querySelector('.sub');
    if (sub && d.generated_at) sub.textContent = '最近抓取：' + d.generated_at + ' ｜ 每 1 分钟自动刷新';
    var cnt = document.getElementById('good-count');
    if (cnt) cnt.textContent = d.good_count;
    // 好号表：新增行插入顶部并标新
    var tb = document.querySelector('#good-table tbody');
    if (tb) {
      var i, a;
      for (i = d.good_accounts.length - 1; i >= 0; i--) {
        a = d.good_accounts[i];
        if (!seenGood[a.url]) {
          seenGood[a.url] = true;
          var tr = rowHtml(a, '');
          var wrap = document.createElement('tbody');
          wrap.innerHTML = tr;
          tb.insertBefore(wrap.firstChild, tb.firstChild);
          wrap.firstChild.classList.add('is-new');
        }
      }
    }
    // 候选分区表：按平台更新
    var platData = {};
    d.all_accounts.forEach(function(x) { (platData[x.platform] = platData[x.platform] || []).push(x); });
    document.querySelectorAll('table[data-plat]').forEach(function(t) {
      var pn = t.getAttribute('data-plat');
      var arr = platData[pn] || [];
      var tb2 = t.querySelector('tbody');
      var existing = {};
      tb2.querySelectorAll('tr').forEach(function(tr2) { existing[tr2.getAttribute('data-url')] = true; });
      arr.forEach(function(a) {
        if (!existing[a.url]) {
          var w2 = document.createElement('tbody');
          w2.innerHTML = rowHtml(a, '');
          tb2.appendChild(w2.firstChild);
          w2.firstChild.classList.add('is-new');
        }
      });
    });
    // 失效表
    var gtb = document.querySelector('#gone-table tbody');
    if (gtb) {
      d.gone_accounts.forEach(function(g) {
        if (!seenGone[g.url]) {
          seenGone[g.url] = true;
          var w3 = document.createElement('tbody');
          w3.innerHTML = rowHtml(g, 'gone-row');
          gtb.insertBefore(w3.firstChild, gtb.firstChild);
        }
      });
    }
  }).catch(function() {});
}
refreshState();
pollData();
setInterval(pollData, 30000);
</script>
"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>三角洲行动好号监控</title>
<link rel="icon" href="{FAVICON_URI}">
<style>
:root {{
  --bg:#0d1117; --bg2:#161b22; --line:#2d333b; --text:#e6edf3; --mut:#8b949e;
  --gold:#f0b429; --ok:#3fb950; --partial:#d29922; --off:#6e7681; --red:#f85149;
}}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:var(--bg); color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
  font-size:14px; line-height:1.5; padding-bottom:40px; }}
.wrap {{ max-width:1080px; margin:0 auto; padding:0 12px; }}
header {{ padding:20px 0 14px; border-bottom:1px solid var(--line); }}
h1 {{ font-size:20px; font-weight:700; }}
h1 .dot {{ color:var(--gold); }}
.sub {{ color:var(--mut); font-size:12.5px; margin-top:4px; }}
.ctrl {{ margin-top:12px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
.ctrl-btn {{ padding:10px 22px; min-height:44px; border:none; border-radius:8px;
  font-size:15px; font-weight:700; cursor:pointer; color:#0d1117; background:var(--gold); }}
.ctrl-btn.paused {{ background:var(--red); color:#fff; }}
.ctrl-btn:disabled {{ opacity:.5; cursor:wait; }}
.ctrl-state {{ font-size:12.5px; color:var(--mut); }}
.rules {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:14px; }}
.rule {{ background:var(--bg2); border:1px solid var(--line); border-radius:20px;
  padding:3px 10px; font-size:12px; color:var(--mut); }}
.section {{ margin-top:22px; }}
.section h2 {{ font-size:14px; font-weight:700; margin-bottom:10px;
  padding-left:8px; border-left:3px solid var(--gold); display:flex; align-items:baseline; gap:10px; }}
.h2-sub {{ font-size:12px; color:var(--mut); font-weight:400; }}
.tbl-wrap {{ overflow-x:auto; -webkit-overflow-scrolling:touch; background:var(--bg2);
  border:1px solid var(--line); border-radius:10px; }}
table.tbl {{ width:100%; border-collapse:collapse; min-width:640px; }}
.tbl th {{ text-align:left; font-size:11.5px; color:var(--mut); font-weight:600;
  padding:8px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }}
.tbl td {{ padding:8px; border-bottom:1px solid #22272e; font-size:13px; white-space:nowrap; }}
.tbl tbody tr:last-child td {{ border-bottom:none; }}
.tbl .c-code {{ color:var(--gold); font-weight:600; }}
.tbl .c-plat {{ color:var(--mut); }}
.tbl .c-link a {{ color:var(--gold); text-decoration:none; }}
.tbl tr.good-row td {{ background:#f0b42910; }}
.tbl tr.gone-row td {{ color:var(--off); text-decoration:line-through; }}
.tbl tr.is-new td {{ background:#3fb95014; }}
.code-btn {{ background:none; border:none; color:var(--gold); font-weight:600;
  font-size:13px; cursor:pointer; padding:4px 0; text-align:left; }}
.code-btn:hover {{ color:#ffd75e; }}
.tag-ok {{ display:inline-block; background:#3fb95022; color:var(--ok); font-size:11px;
  border-radius:4px; padding:1px 5px; margin-left:4px; }}
.na {{ color:var(--off); }}
.empty {{ text-align:center; padding:36px 16px; color:var(--mut); background:var(--bg2);
  border:1px dashed var(--line); border-radius:10px; }}
.empty p {{ font-size:14px; }}
.empty-sub {{ font-size:12px; color:var(--off); margin-top:6px; }}
.plat-line {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:10px 12px;
  background:var(--bg2); border:1px solid var(--line); border-radius:8px; margin-bottom:8px;
  border-left:3px solid var(--off); }}
.plat-line.plat-ok {{ border-left-color:var(--ok); }}
.plat-line.plat-partial {{ border-left-color:var(--partial); }}
.plat-name {{ font-weight:600; font-size:13px; }}
.plat-badge {{ font-size:11px; padding:2px 8px; border-radius:4px; }}
.b-ok {{ background:#3fb95022; color:var(--ok); }}
.b-partial {{ background:#d2992222; color:var(--partial); }}
.b-off {{ background:#6e768122; color:var(--off); }}
.plat-meta {{ font-size:12px; color:var(--mut); }}
.plat-detail {{ font-size:12px; color:var(--mut); flex-basis:100%; margin-left:auto; }}
footer {{ margin-top:26px; padding-top:14px; border-top:1px solid var(--line);
  color:var(--mut); font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>三角洲行动<span class="dot">·</span>好号监控</h1>
    <div class="sub">最近抓取：{esc(generated)} ｜ 每 1 分钟自动刷新</div>
    <div class="ctrl">
      <button id="btn-toggle" class="ctrl-btn" onclick="togglePause()" type="button">加载中…</button>
      <span id="ctrl-state" class="ctrl-state"></span>
    </div>
    <div class="rules">{rules_html}</div>
  </header>

  <section class="section">
    <h2>好号（<span id="good-count">{len(good)}</span>）</h2>
    {good_html}
  </section>

  <section class="section">
    <h2>全部在售/在租号（按平台分区）</h2>
    {cand_html}
  </section>

  {gone_html}

  <section class="section">
    <h2>平台接入状态</h2>
    {platforms_html}
  </section>

  <footer>
    数据来源：各平台公开列表页/API，实时抓取。售价/租金为挂牌口径，实际以下单页为准。
    本页仅作信息聚合，不构成交易建议。暂停按钮可随时停止抓取与推送。
  </footer>
</div>
{js_script}
</body>
</html>"""


# ===========================================================================
# 极简网格版渲染（无横向滑动 · 筛选 · 折叠）— 主流程使用
# 行布局 grid 9 列：单号|平台|纯币|AWM|保险|负重|体力|租金|押金
# ===========================================================================
def render_html_v3(data):
    generated = data["generated_at"]
    good = data.get("good_accounts", [])
    gone = data.get("gone_accounts", [])
    all_acc = data.get("all_accounts", [])
    platforms = data["platforms"]
    rules = data.get("rules", GOOD_RULES)

    def esc(s):
        return htmlmod.escape(str(s)) if s is not None else ""

    def fmt(v, suffix=""):
        if v is None:
            return '<span class="na">—</span>'
        if isinstance(v, float):
            return f"{v:g}{suffix}"
        return f"{v}{suffix}"

    def ratio_val(a):
        """哈夫币/租金比例 = 纯币M ÷ 租金（元）。rent 为 0/空 或纯币为空 → None。"""
        r = a.get("rent")
        c = a.get("pure_coins_M")
        try:
            if r in (None, 0) or c in (None, 0):
                return None
            return round(float(c) / float(r), 3)
        except (TypeError, ValueError):
            return None

    def ratio_sort_key(a):
        v = ratio_val(a)
        return (1, 0) if v is None else (0, -v)

    def row_line(a, cls="", idx=0):
        """卡片式布局（数据属性供 JS 筛选/排序）"""
        c = a.get("goods_code") or ""
        plat = esc(a.get("platform") or "")
        good_tag = '<span class="tag-ok">好</span>' if a.get("is_good") else ""
        rv = ratio_val(a)
        ratio_txt = f'{rv:g}' if rv is not None else ""
        ratio_html = ratio_txt if ratio_txt else '<span class="na">—</span>'
        return (
            f'<div class="acc-card {cls}" data-good="{1 if a.get("is_good") else 0}" '
            f'data-coins="{a.get("pure_coins_M") or ""}" data-awm="{a.get("awm_bullets") or ""}" '
            f'data-grids="{a.get("insurance_grids") or ""}" data-weight="{a.get("weight_level") or ""}" '
            f'data-stamina="{a.get("stamina_level") or ""}" data-rent="{a.get("rent") or ""}" '
            f'data-deposit="{a.get("deposit") or ""}" data-ratio="{ratio_txt}" data-idx="{idx}" '
            f'data-plat="{plat}" data-url="{esc(a.get("url") or "")}" '
            f'onclick="openUrl(this)">'
            f'<div class="acc-top">'
            f'<span class="acc-plat">{plat}{good_tag}</span>'
            f'<span class="acc-code" onclick="copyCode(event,\'{esc(c)}\')" title="点击复制">{esc(c[:12])}</span>'
            f'</div>'
            f'<div class="acc-stats">'
            f'<div class="stat"><span class="sv">{fmt(a.get("pure_coins_M"),"M")}</span><span class="sl">纯币</span></div>'
            f'<div class="stat"><span class="sv">{fmt(a.get("awm_bullets"))}</span><span class="sl">AWM</span></div>'
            f'<div class="stat"><span class="sv">{fmt(a.get("insurance_grids"))}</span><span class="sl">保险</span></div>'
            f'<div class="stat"><span class="sv">{fmt(a.get("weight_level"))}</span><span class="sl">负重</span></div>'
            f'<div class="stat"><span class="sv">{fmt(a.get("stamina_level"))}</span><span class="sl">体力</span></div>'
            f'</div>'
            f'<div class="acc-foot">'
            f'<span class="acc-price">¥{fmt(a.get("rent"))}<span class="dep">+{fmt(a.get("deposit"))}</span></span>'
            f'<span class="acc-ratio">比例 {ratio_html}</span>'
            f'</div>'
            f"</div>"
        )

    # ---- 好号区（比例高优先） ----
    if good:
        good_sorted = sorted(good, key=ratio_sort_key)
        good_rows = "\n".join(row_line(a, idx=i) for i, a in enumerate(good_sorted))
        good_html = f"""
        <section class="sec" id="sec-good" data-sec="1">
          <div class="sec-h" onclick="toggleSec('sec-good')">
            <span class="fold">▾</span><h2>好号（{len(good)}）</h2><span class="h2-sub">符合规则 · 比例高优先</span>
          </div>
          <div class="sec-b">
            <div class="head-row">
              <span>单号</span><span>平台</span><span>纯币</span><span>AWM</span><span>保险</span><span>负重</span><span>体力</span><span>租金</span><span>押金</span><span>比例</span>
            </div>
            {good_rows}
          </div>
        </section>"""
    else:
        good_html = '<div class="empty"><p>当前无符合条件的好号</p></div>'

    # ---- 候选按平台分区 ----
    by_plat = {}
    for a in all_acc:
        by_plat.setdefault(a["platform"], []).append(a)
    order = ["ysc9雅俗", "61xm星铭", "ys9d艺术", "98lj凌绝", "叮当商行", "皓星商行", "准星代售", "三一商行", "夏日商行", "泓星代售", "哈夫克基地", "阿龙商行", "虚贝", "DD373", "盼之", "换号吧"]
    plat_names = sorted(by_plat.keys(), key=lambda p: order.index(p) if p in order else 99)
    plat_sections = []
    for pn in plat_names:
        accs = by_plat[pn]
        good_n = sum(1 for a in accs if a.get("is_good"))
        accs_sorted = sorted(accs, key=ratio_sort_key)
        rows = "\n".join(row_line(a, idx=i) for i, a in enumerate(accs_sorted))
        pid = "sec-plat-" + re.sub(r"\W+", "", pn)
        plat_sections.append(f"""
        <section class="sec" id="{pid}" data-sec="1">
          <div class="sec-h" onclick="toggleSec('{pid}')">
            <span class="fold">▾</span><h2>{esc(pn)}</h2>
            <span class="h2-sub">{len(accs)}个 · 好号{good_n} · 比例高优先</span>
          </div>
          <div class="sec-b">
            <div class="head-row">
              <span>单号</span><span>平台</span><span>纯币</span><span>AWM</span><span>保险</span><span>负重</span><span>体力</span><span>租金</span><span>押金</span><span>比例</span>
            </div>
            {rows}
          </div>
        </section>""")
    cand_html = "\n".join(plat_sections)

    # ---- 已失效 ----
    if gone:
        gone_sorted = sorted(gone, key=ratio_sort_key)
        gone_rows = "\n".join(row_line(a, "gone-row", idx=i) for i, a in enumerate(gone_sorted))
        gone_html = f"""
        <section class="sec" id="sec-gone" data-sec="1">
          <div class="sec-h" onclick="toggleSec('sec-gone')">
            <span class="fold">▾</span><h2>已失效（{len(gone)}）</h2><span class="h2-sub">被租走/下架</span>
          </div>
          <div class="sec-b">
            <div class="head-row">
              <span>单号</span><span>平台</span><span>纯币</span><span>AWM</span><span>保险</span><span>负重</span><span>体力</span><span>租金</span><span>押金</span><span>比例</span>
            </div>
            {gone_rows}
          </div>
        </section>"""
    else:
        gone_html = ""

    # ---- 平台状态（简表） ----
    p_rows = []
    for p in platforms:
        st = p["status"]
        badge = {"已接入": "ok", "部分接入": "partial", "暂未接入": "off"}.get(st, "off")
        detail = esc((p.get("status_detail") or p.get("error") or "")[:80])
        p_rows.append(f"""
        <div class="plat-line plat-{badge}">
          <span class="plat-name">{esc(p['name'])}</span>
          <span class="plat-badge b-{badge}">{esc(st)}</span>
          <span class="plat-meta">{p.get('fetched_count',0)}抓 / {p.get('good_count',0)}好</span>
          <span class="plat-detail">{detail}</span>
        </div>""")
    platforms_html = "\n".join(p_rows)
    rules_html = "".join(f"<span class='rule'>{esc(r)}</span>" for r in rules)

    # ---- 页面 JS：筛选 + 折叠 + 复制 + 开关 + 轮询 ----
    js_script = """<script>
function copyCode(ev, code) {
  if (ev) ev.stopPropagation();
  var done = function() {
    var el = document.createElement('div');
    el.textContent = '已复制单号 ' + code;
    el.style.cssText = 'position:fixed;left:50%;bottom:80px;transform:translateX(-50%);' +
      'background:#1f6feb;color:#fff;padding:10px 18px;border-radius:8px;font-size:14px;z-index:999;';
    document.body.appendChild(el);
    setTimeout(function() { el.remove(); }, 1600);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(code).then(done).catch(function() { fallbackCopy(code, done); });
  } else { fallbackCopy(code, done); }
}
function fallbackCopy(code, done) {
  var ta = document.createElement('textarea');
  ta.value = code; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch(e) {}
  ta.remove();
}
function openUrl(div) {
  var u = div.getAttribute('data-url');
  if (u) window.open(u, '_blank');
}
function fmt(v, s) { return (v===null||v===undefined||v==='') ? '<span class="na">—</span>' : (v + (s||'')); }
var jsRowIdx = 900000;
function ratioOf(a) {
  var r = Number(a.rent), c = Number(a.pure_coins_M);
  if (!r || !c || isNaN(r) || isNaN(c)) return null;
  return Math.round(c / r * 1000) / 1000;
}
function rowLine(a, cls) {
  var good = a.is_good ? 1 : 0;
  var plat = a.platform || '';
  var tag = good ? '<span class="tag-ok">好</span>' : '';
  var c = a.goods_code || '';
  var rv = ratioOf(a);
  var ratioTxt = (rv === null) ? '<span class="na">—</span>' : rv;
  var idx = jsRowIdx++;
  return '<div class="row ' + (cls||'') + '" data-good="' + good + '" data-coins="' + (a.pure_coins_M||'') + '"' +
    ' data-awm="' + (a.awm_bullets||'') + '" data-grids="' + (a.insurance_grids||'') + '"' +
    ' data-weight="' + (a.weight_level||'') + '" data-stamina="' + (a.stamina_level||'') + '"' +
    ' data-rent="' + (a.rent||'') + '" data-deposit="' + (a.deposit||'') + '"' +
    ' data-ratio="' + (rv === null ? '' : rv) + '" data-idx="' + idx + '"' +
    ' data-plat="' + plat + '" data-url="' + (a.url||'') + '" onclick="openUrl(this)">' +
    '<span class="r-code" onclick="copyCode(event,\\'' + c + '\\')" title="点击复制">' + String(c).slice(0,14) + '</span>' +
    '<span class="r-plat">' + plat + ' ' + tag + '</span>' +
    '<span class="r-num">' + fmt(a.pure_coins_M,'M') + '</span>' +
    '<span class="r-num">' + fmt(a.awm_bullets) + '</span>' +
    '<span class="r-num">' + fmt(a.insurance_grids) + '</span>' +
    '<span class="r-num">' + fmt(a.weight_level) + '</span>' +
    '<span class="r-num">' + fmt(a.stamina_level) + '</span>' +
    '<span class="r-num">' + fmt(a.rent) + '</span>' +
    '<span class="r-num">' + fmt(a.deposit) + '</span>' +
    '<span class="r-num r-ratio">' + ratioTxt + '</span>' +
    '</div>';
}
function refreshState() {
  fetch('api/status').then(function(r) { return r.json(); }).then(function(s) {
    var btn = document.getElementById('btn-toggle');
    var st = document.getElementById('ctrl-state');
    if (s.paused) {
      btn.textContent = '▶ 恢复监控'; btn.classList.add('paused'); btn.disabled = false;
      st.textContent = '已暂停 · 最后抓取 ' + (s.last_run || '-');
    } else {
      btn.textContent = '⏸ 暂停监控'; btn.classList.remove('paused'); btn.disabled = false;
      st.textContent = '运行中 · 每1分钟 · 好号 ' + s.good_count;
    }
  }).catch(function() {
    var btn = document.getElementById('btn-toggle');
    btn.textContent = '控制接口未连接'; btn.disabled = true;
    document.getElementById('ctrl-state').textContent = '请用完整版地址访问';
  });
}
function togglePause() {
  var btn = document.getElementById('btn-toggle');
  var paused = btn.classList.contains('paused');
  btn.disabled = true; btn.textContent = '处理中…';
  fetch(paused ? 'api/resume' : 'api/pause', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(j) { if (j.ok) { refreshState(); } else { btn.disabled = false; btn.textContent = '操作失败，重试'; } })
    .catch(function() { btn.disabled = false; btn.textContent = '操作失败，重试'; });
}
function toggleSec(id) {
  var s = document.getElementById(id);
  var b = s.querySelector('.sec-b');
  var f = s.querySelector('.fold');
  if (b.style.display === 'none') { b.style.display = ''; f.textContent = '▾'; }
  else { b.style.display = 'none'; f.textContent = '▸'; }
  saveFilters();
}
function foldAll(v) {
  document.querySelectorAll('.sec').forEach(function(s) {
    var b = s.querySelector('.sec-b');
    var f = s.querySelector('.fold');
    if (v === 'fold') { b.style.display = 'none'; f.textContent = '▸'; }
    else { b.style.display = ''; f.textContent = '▾'; }
  });
  saveFilters();
}
function toggleFilterPanel() {
  var p = document.getElementById('filter-panel');
  var b = document.getElementById('filter-btn');
  var hidden = (p.style.display === 'none');
  p.style.display = hidden ? '' : 'none';
  if (b) b.textContent = hidden ? '筛选面板:收起' : '筛选面板:展开';
  saveFilters();
}
function saveFilters() {
  try {
    var st = { chips: [], selects: {}, sort: sortMode, folded: {}, panel: '' };
    var pn = document.getElementById('filter-panel');
    if (pn) st.panel = pn.style.display;
    document.querySelectorAll('#filter-panel .chip.on').forEach(function(c) { st.chips.push(c.dataset.f); });
    ['rent-min','rent-max','dep-min','dep-max'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) st.selects[id] = el.value;
    });
    document.querySelectorAll('.sec').forEach(function(s) {
      if (s.querySelector('.sec-b').style.display === 'none') st.folded[s.id] = 1;
    });
    localStorage.setItem('dx_filters', JSON.stringify(st));
  } catch (e) {}
}
function restoreFilters() {
  try {
    var st = JSON.parse(localStorage.getItem('dx_filters') || 'null');
    if (!st) return;
    document.querySelectorAll('#filter-panel .chip').forEach(function(c) {
      if (st.chips.indexOf(c.dataset.f) >= 0) c.classList.add('on');
    });
    for (var id in st.selects) {
      var el = document.getElementById(id);
      if (el) el.value = st.selects[id];
    }
    if (st.sort === 'default') sortMode = 'default';
    for (var sid in st.folded) {
      var s = document.getElementById(sid);
      if (s && st.folded[sid]) {
        s.querySelector('.sec-b').style.display = 'none';
        s.querySelector('.fold').textContent = '▸';
      }
    }
    if (st.panel === 'none') {
      var pn = document.getElementById('filter-panel');
      if (pn) pn.style.display = 'none';
      var fb = document.getElementById('filter-btn');
      if (fb) fb.textContent = '筛选面板:展开';
    }
    applyFilters();
    sortRows(sortMode);
  } catch (e) {}
}
function toggleChip(btn) {
  var g = btn.dataset.group;
  if (g) {
    document.querySelectorAll('#filter-panel .chip[data-group="' + g + '"]').forEach(function(c) { c.classList.remove('on'); });
    btn.classList.add('on');
  } else {
    btn.classList.toggle('on');
  }
  applyFilters();
}
function n(v) { var x = parseFloat(v); return isNaN(x) ? null : x; }
function applyFilters() {
  var f = {};
  document.querySelectorAll('#filter-panel .chip.on').forEach(function(c) { f[c.dataset.f] = 1; });
  var onlyGood = f['good'] === 1;
  var f9 = f['grid9'], fawm = f['awm50'], fw = f['wt6'], fs = f['st6'],
      fc = f['coin100'];
  var rentMin = null, rentMax = null, depMin = null, depMax = null;
  var em1 = document.getElementById('rent-min'), em2 = document.getElementById('rent-max');
  var dm1 = document.getElementById('dep-min'), dm2 = document.getElementById('dep-max');
  if (em1 && em1.value !== '') rentMin = n(em1.value);
  if (em2 && em2.value !== '') rentMax = n(em2.value);
  if (dm1 && dm1.value !== '') depMin = n(dm1.value);
  if (dm2 && dm2.value !== '') depMax = n(dm2.value);
  var ratioMin = null;
  document.querySelectorAll('#filter-panel .chip.on[data-group="ratio"]').forEach(function(c) { ratioMin = n(c.dataset.v); });
  var sumCap = null;
  document.querySelectorAll('#filter-panel .chip.on[data-group="sum"]').forEach(function(c) { sumCap = n(c.dataset.v); });
  document.querySelectorAll('.acc-card').forEach(function(r) {
    var show = true;
    if (onlyGood && r.dataset.good !== '1') show = false;
    if (show && f9 && n(r.dataset.grids) < 9) show = false;
    if (show && fawm && (n(r.dataset.awm) === null || n(r.dataset.awm) < 50)) show = false;
    if (show && fw && (n(r.dataset.weight) === null || n(r.dataset.weight) < 6)) show = false;
    if (show && fs && (n(r.dataset.stamina) === null || n(r.dataset.stamina) < 6)) show = false;
    if (show && fc && (n(r.dataset.coins) === null || n(r.dataset.coins) < 100)) show = false;
    if (show && rentMin !== null && (n(r.dataset.rent) === null || n(r.dataset.rent) < rentMin)) show = false;
    if (show && rentMax !== null && (n(r.dataset.rent) === null || n(r.dataset.rent) > rentMax)) show = false;
    if (show && depMin !== null && (n(r.dataset.deposit) === null || n(r.dataset.deposit) < depMin)) show = false;
    if (show && depMax !== null && (n(r.dataset.deposit) === null || n(r.dataset.deposit) > depMax)) show = false;
    if (show && sumCap !== null) {
      var rr = n(r.dataset.rent), dd = n(r.dataset.deposit);
      if (rr === null || (rr + (dd || 0)) > sumCap) show = false;
    }
    if (show && ratioMin !== null && (n(r.dataset.ratio) === null || n(r.dataset.ratio) < ratioMin)) show = false;
    r.style.display = show ? '' : 'none';
  });
  var cnt = 0;
  document.querySelectorAll('.acc-card').forEach(function(r) { if (r.style.display !== 'none') cnt++; });
  var el = document.getElementById('filter-count');
  if (el) el.textContent = cnt;
  saveFilters();
}
function rvNum(r) { var v = parseFloat(r.dataset.ratio); return isNaN(v) ? -1 : v; }
function idxNum(r) { var v = parseInt(r.dataset.idx, 10); return isNaN(v) ? 1e9 : v; }
var sortMode = 'ratio';
function sortRows(mode) {
  document.querySelectorAll('.sec-b').forEach(function(b) {
    var rows = Array.prototype.slice.call(b.querySelectorAll('.acc-card'));
    rows.sort(function(x, y) {
      if (mode === 'default') return idxNum(x) - idxNum(y);
      var rx = rvNum(x), ry = rvNum(y);
      if (rx < 0 && ry < 0) return idxNum(x) - idxNum(y);
      if (rx < 0) return 1;
      if (ry < 0) return -1;
      if (ry !== rx) return ry - rx;
      return idxNum(x) - idxNum(y);
    });
    rows.forEach(function(r) { b.appendChild(r); });
  });
  var btn = document.getElementById('sort-btn');
  if (btn) {
    btn.textContent = (mode === 'ratio') ? '排序:比例高优先' : '排序:默认';
    btn.classList.toggle('sort-on', mode === 'ratio');
  }
}
function toggleSort() {
  sortMode = (sortMode === 'ratio') ? 'default' : 'ratio';
  sortRows(sortMode);
  applyFilters();
  saveFilters();
}
function resetFilters() {
  document.querySelectorAll('#filter-panel .chip.on').forEach(function(c) { c.classList.remove('on'); });
  document.querySelectorAll('#filter-panel select').forEach(function(s) { s.value = ''; });
  applyFilters();
  saveFilters();
}
function manualRefresh() {
  var b = document.getElementById('refresh-btn');
  if (b) { b.textContent = '刷新中…'; b.disabled = true; }
  fetch('api/data').then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok && d.generated_at) { location.reload(); }
    else { setTimeout(function() { location.reload(); }, 800); }
  }).catch(function() { setTimeout(function() { location.reload(); }, 800); });
}
var seenGood = {}, seenGone = {};
function pollData() {
  fetch('api/data').then(function(r) { return r.json(); }).then(function(d) {
    if (!d.ok) return;
    var sub = document.querySelector('.sub');
    if (sub && d.generated_at) sub.textContent = '最近抓取：' + d.generated_at + ' ｜ 每 1 分钟自动刷新';
    var cnt = document.getElementById('good-count');
    if (cnt) cnt.textContent = d.good_count;
    var gs = document.getElementById('sec-good');
    var gb = gs ? gs.querySelector('.sec-b') : null;
    if (gb) {
      var i, a;
      for (i = d.good_accounts.length - 1; i >= 0; i--) {
        a = d.good_accounts[i];
        if (!seenGood[a.url]) {
          seenGood[a.url] = true;
          var d2 = document.createElement('div');
          d2.innerHTML = rowLine(a, 'is-new');
          var row = d2.firstChild;
          var tbody = gb.querySelector('.row:not(.head)');
          var hr = gb.querySelector('.head-row');
          if (hr && hr.nextSibling) hr.parentNode.insertBefore(row, hr.nextSibling);
          else gb.appendChild(row);
        }
      }
    }
    var platData = {};
    d.all_accounts.forEach(function(x) { (platData[x.platform] = platData[x.platform] || []).push(x); });
    document.querySelectorAll('section[data-sec] .sec-b').forEach(function(b) {
      var id = b.parentNode.id;
      if (id === 'sec-good' || id === 'sec-gone') return;
      var pn = null;
      var h2 = b.parentNode.querySelector('h2');
      if (h2) pn = h2.textContent.trim();
      var arr = platData[pn] || [];
      var existing = {};
      b.querySelectorAll('.acc-card').forEach(function(r) { existing[r.getAttribute('data-url')] = true; });
      arr.forEach(function(a) {
        if (!existing[a.url]) {
          var w2 = document.createElement('div');
          w2.innerHTML = rowLine(a, 'is-new');
          var row2 = w2.firstChild;
          var hr2 = b.querySelector('.head-row');
          if (hr2 && hr2.nextSibling) hr2.parentNode.insertBefore(row2, hr2.nextSibling);
          else b.appendChild(row2);
        }
      });
    });
    var gt = document.getElementById('sec-gone');
    if (gt) {
      var gbody = gt.querySelector('.sec-b');
      d.gone_accounts.forEach(function(g) {
        if (!seenGone[g.url]) {
          seenGone[g.url] = true;
          var w3 = document.createElement('div');
          w3.innerHTML = rowLine(g, 'gone-row is-new');
          var row3 = w3.firstChild;
          var hr3 = gbody.querySelector('.head-row');
          if (hr3 && hr3.nextSibling) hr3.parentNode.insertBefore(row3, hr3.nextSibling);
          else gbody.appendChild(row3);
        }
      });
    }
    applyFilters();
    if (sortMode === 'ratio') sortRows('ratio');
  }).catch(function() {});
}
refreshState();
restoreFilters();
pollData();
sortRows(sortMode);
setInterval(pollData, 30000);
</script>
"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>三角洲行动好号监控</title>
<link rel="icon" href="{FAVICON_URI}">
<style>
:root {{
  --bg:#0a0e1a; --bg2:rgba(22,27,46,.6); --line:rgba(255,255,255,.08);
  --text:#e8ecf4; --mut:#8b95ad; --gold:#f0b429; --ok:#3fb950;
  --partial:#d29922; --off:#6e7681; --red:#f85149;
  --gold-grad:linear-gradient(135deg, #f0b429 0%, #f7ce46 100%);
}}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:var(--bg); color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
  font-size:14px; line-height:1.5; padding-bottom:60px; min-height:100vh;
  background-image:
    radial-gradient(ellipse at top left, rgba(102,126,234,.12) 0%, transparent 50%),
    radial-gradient(ellipse at bottom right, rgba(118,75,162,.12) 0%, transparent 50%);
  background-attachment:fixed;
}}
.wrap {{ max-width:1440px; margin:0 auto; padding:0 28px; }}
header {{ padding:28px 0 20px; }}
.hero {{ background:var(--bg2); backdrop-filter:blur(20px);
  border:1px solid var(--line); border-radius:20px; padding:24px 28px;
  box-shadow:0 8px 32px rgba(0,0,0,.3); }}
h1 {{ font-size:26px; font-weight:800; letter-spacing:-.5px;
  background:var(--gold-grad); -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; }}
h1 .dot {{ -webkit-text-fill-color:var(--gold); }}
.sub {{ color:var(--mut); font-size:13px; margin-top:6px; }}
.bar {{ margin-top:14px; display:flex; gap:8px; flex-wrap:wrap; }}
.mini-btn {{ background:rgba(255,255,255,.05); border:1px solid var(--line); color:var(--text);
  border-radius:10px; padding:8px 16px; font-size:13px; cursor:pointer;
  transition:all .2s; }}
.mini-btn:hover {{ background:rgba(255,255,255,.1); border-color:rgba(240,180,41,.3); }}
.mini-btn:active {{ transform:scale(.98); }}
#filter-panel {{ margin-top:12px; background:var(--bg2); backdrop-filter:blur(20px);
  border:1px solid var(--line); border-radius:14px; padding:16px; }}
.filter-title {{ font-size:12px; color:var(--mut); margin-bottom:10px; }}
.chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
.chip {{ background:rgba(255,255,255,.05); border:1px solid var(--line); color:var(--mut);
  border-radius:20px; padding:5px 14px; font-size:12px; cursor:pointer;
  transition:all .2s; }}
.chip:hover {{ border-color:rgba(240,180,41,.3); }}
.chip.on {{ background:rgba(240,180,41,.15); border-color:var(--gold);
  color:var(--gold); font-weight:600; }}
.range-row {{ align-items:center; }}
.range-select {{ background:rgba(255,255,255,.05); color:var(--text); border:1px solid var(--line);
  border-radius:8px; padding:7px 10px; font-size:13px; min-width:120px; }}
.range-sep {{ color:var(--mut); font-size:13px; padding:0 2px; }}
.rules {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }}
.rule {{ background:rgba(255,255,255,.05); border:1px solid var(--line); border-radius:20px;
  padding:3px 10px; font-size:11px; color:var(--mut); }}
.sec {{ margin-top:20px; }}
.sec-h {{ display:flex; align-items:center; gap:8px; cursor:pointer; padding:10px 8px;
  border-bottom:1px solid var(--line); user-select:none; }}
.sec-h .fold {{ color:var(--gold); font-size:14px; width:18px; }}
.sec-h h2 {{ font-size:16px; font-weight:700; }}
.h2-sub {{ font-size:12px; color:var(--mut); font-weight:400; }}
.sec-b {{ padding-top:6px; }}
.head-row {{ display:none; }}
.sec-b {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); gap:14px; }}
.acc-card {{ background:var(--bg2); backdrop-filter:blur(20px);
  border:2px solid rgba(255,255,255,.15); border-radius:14px; padding:16px;
  cursor:pointer; transition:all .2s; position:relative;
  box-shadow:0 4px 16px rgba(0,0,0,.2); }}
.acc-card:hover {{ transform:translateY(-2px); border-color:rgba(240,180,41,.3);
  box-shadow:0 8px 24px rgba(0,0,0,.3); }}
.acc-card.good-row {{ border-left:3px solid var(--gold); }}
.acc-card.gone-row {{ opacity:.6; text-decoration:none; }}
.acc-card.is-new {{ border-left:3px solid var(--ok); }}
.acc-top {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; }}
.acc-plat {{ font-size:12px; font-weight:600; color:var(--mut); }}
.acc-code {{ font-size:12px; color:var(--gold); font-weight:600; cursor:pointer;
  border-bottom:1px dashed rgba(240,180,41,.4); }}
.acc-stats {{ display:grid; grid-template-columns:repeat(5, 1fr); gap:8px; margin-bottom:12px; }}
.stat {{ background:rgba(0,0,0,.2); border-radius:8px; padding:8px 4px; text-align:center; }}
.stat .sv {{ display:block; font-size:15px; font-weight:700; color:var(--gold); }}
.stat .sl {{ font-size:10px; color:var(--mut); margin-top:2px; }}
.acc-foot {{ display:flex; justify-content:space-between; align-items:center;
  padding-top:10px; border-top:1px solid var(--line); }}
.acc-price {{ font-size:14px; font-weight:700; color:var(--text); }}
.acc-price .dep {{ font-size:11px; color:var(--mut); font-weight:400; margin-left:4px; }}
.acc-ratio {{ font-size:12px; color:var(--gold); font-weight:600; }}
.mini-btn.sort-on {{ background:rgba(240,180,41,.15); border-color:var(--gold);
  color:var(--gold); font-weight:700; }}
.tag-ok {{ display:inline-block; background:rgba(63,185,80,.15); color:var(--ok);
  font-size:10px; border-radius:4px; padding:1px 6px; margin-left:4px; font-weight:600; }}
.na {{ color:var(--off); }}
.empty {{ text-align:center; padding:32px 16px; color:var(--mut);
  background:var(--bg2); border:1px dashed var(--line); border-radius:14px; font-size:13px; }}
.plat-line {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:12px 14px;
  background:var(--bg2); backdrop-filter:blur(20px); border:1px solid var(--line);
  border-radius:12px; margin-bottom:8px; border-left:3px solid var(--off); }}
.plat-line.plat-ok {{ border-left-color:var(--ok); }}
.plat-line.plat-partial {{ border-left-color:var(--partial); }}
.plat-name {{ font-weight:600; font-size:13px; }}
.plat-badge {{ font-size:11px; padding:2px 8px; border-radius:6px; font-weight:600; }}
.b-ok {{ background:rgba(63,185,80,.15); color:var(--ok); }}
.b-partial {{ background:rgba(210,153,34,.15); color:var(--partial); }}
.b-off {{ background:rgba(110,118,129,.15); color:var(--off); }}
.plat-meta {{ font-size:11px; color:var(--mut); }}
.plat-detail {{ font-size:11px; color:var(--mut); flex-basis:100%; }}
footer {{ margin-top:32px; padding-top:16px; border-top:1px solid var(--line);
  color:var(--mut); font-size:12px; text-align:center; }}
@media (max-width:640px) {{
  .wrap {{ padding:0 16px; }}
  .hero {{ padding:18px; border-radius:14px; }}
  h1 {{ font-size:20px; }}
  .sec-b {{ grid-template-columns:1fr; }}
}}</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="hero">
      <h1>三角洲行动<span class="dot">·</span>好号监控</h1>
      <div class="sub">最近抓取：{esc(generated)} ｜ 每 5 分钟自动更新</div>
      <div class="bar">
      <button id="filter-btn" class="mini-btn" onclick="toggleFilterPanel()" type="button">筛选面板:展开 <span id="filter-count"></span></button>
      <button id="sort-btn" class="mini-btn sort-on" onclick="toggleSort()" type="button">排序:比例高优先</button>
      <button class="mini-btn" onclick="foldAll('fold')" type="button">收起全部</button>
      <button class="mini-btn" onclick="foldAll('open')" type="button">展开全部</button>
      <button class="mini-btn" onclick="resetFilters()" type="button">重置筛选</button>
      <button id="refresh-btn" class="mini-btn" onclick="manualRefresh()" type="button">刷新</button>
    </div>
    <div id="filter-panel" style="display:none">
      <div class="filter-title">多选叠加筛选，实时过滤所有分区（含新增）</div>
      <div class="chips">
        <span class="chip" data-f="good" onclick="toggleChip(this)">仅好号</span>
        <span class="chip" data-f="grid9" onclick="toggleChip(this)">保险9格</span>
        <span class="chip" data-f="awm50" onclick="toggleChip(this)">AWM≥50</span>
        <span class="chip" data-f="wt6" onclick="toggleChip(this)">负重≥6</span>
        <span class="chip" data-f="st6" onclick="toggleChip(this)">体力≥6</span>
        <span class="chip" data-f="coin100" onclick="toggleChip(this)">纯币≥100M</span>
      </div>
      <div class="filter-title" style="margin-top:10px">租金区间（元）</div>
      <div class="chips range-row">
        <select id="rent-min" class="range-select" onchange="applyFilters()">
          <option value="">租金 ≥ 不限</option>
          <option value="100">≥100</option>
          <option value="200">≥200</option>
          <option value="300">≥300</option>
          <option value="400">≥400</option>
          <option value="500">≥500</option>
          <option value="600">≥600</option>
          <option value="700">≥700</option>
          <option value="800">≥800</option>
          <option value="900">≥900</option>
          <option value="1000">≥1000</option>
          <option value="2000">≥2000</option>
          <option value="3000">≥3000</option>
        </select>
        <span class="range-sep">至</span>
        <select id="rent-max" class="range-select" onchange="applyFilters()">
          <option value="">租金 ≤ 不限</option>
          <option value="100">≤100</option>
          <option value="200">≤200</option>
          <option value="300">≤300</option>
          <option value="400">≤400</option>
          <option value="500">≤500</option>
          <option value="600">≤600</option>
          <option value="700">≤700</option>
          <option value="800">≤800</option>
          <option value="900">≤900</option>
          <option value="1000">≤1000</option>
          <option value="2000">≤2000</option>
          <option value="3000">≤3000</option>
          <option value="5000">≤5000</option>
        </select>
      </div>
      <div class="filter-title" style="margin-top:10px">押金区间（元）</div>
      <div class="chips range-row">
        <select id="dep-min" class="range-select" onchange="applyFilters()">
          <option value="">押金 ≥ 不限</option>
          <option value="0">≥0(含免押)</option>
          <option value="100">≥100</option>
          <option value="200">≥200</option>
          <option value="300">≥300</option>
          <option value="400">≥400</option>
          <option value="500">≥500</option>
          <option value="600">≥600</option>
          <option value="700">≥700</option>
          <option value="800">≥800</option>
          <option value="900">≥900</option>
          <option value="1000">≥1000</option>
          <option value="2000">≥2000</option>
          <option value="3000">≥3000</option>
        </select>
        <span class="range-sep">至</span>
        <select id="dep-max" class="range-select" onchange="applyFilters()">
          <option value="">押金 ≤ 不限</option>
          <option value="100">≤100</option>
          <option value="200">≤200</option>
          <option value="300">≤300</option>
          <option value="400">≤400</option>
          <option value="500">≤500</option>
          <option value="600">≤600</option>
          <option value="700">≤700</option>
          <option value="800">≤800</option>
          <option value="900">≤900</option>
          <option value="1000">≤1000</option>
          <option value="2000">≤2000</option>
          <option value="3000">≤3000</option>
          <option value="5000">≤5000</option>
        </select>
      </div>
      <div class="filter-title" style="margin-top:10px">租金+押金合计（元，单选）</div>
      <div class="chips">
        <span class="chip" data-f="sum100" data-group="sum" data-v="100" onclick="toggleChip(this)">合计≤100</span>
        <span class="chip" data-f="sum200" data-group="sum" data-v="200" onclick="toggleChip(this)">合计≤200</span>
        <span class="chip" data-f="sum300" data-group="sum" data-v="300" onclick="toggleChip(this)">合计≤300</span>
        <span class="chip" data-f="sum400" data-group="sum" data-v="400" onclick="toggleChip(this)">合计≤400</span>
        <span class="chip" data-f="sum500" data-group="sum" data-v="500" onclick="toggleChip(this)">合计≤500</span>
        <span class="chip" data-f="sum600" data-group="sum" data-v="600" onclick="toggleChip(this)">合计≤600</span>
        <span class="chip" data-f="sum700" data-group="sum" data-v="700" onclick="toggleChip(this)">合计≤700</span>
        <span class="chip" data-f="sum800" data-group="sum" data-v="800" onclick="toggleChip(this)">合计≤800</span>
        <span class="chip" data-f="sum900" data-group="sum" data-v="900" onclick="toggleChip(this)">合计≤900</span>
        <span class="chip" data-f="sum1000" data-group="sum" data-v="1000" onclick="toggleChip(this)">合计≤1000</span>
        <span class="chip" data-f="sum1500" data-group="sum" data-v="1500" onclick="toggleChip(this)">合计≤1500</span>
        <span class="chip" data-f="sum2000" data-group="sum" data-v="2000" onclick="toggleChip(this)">合计≤2000</span>
      </div>
      <div class="filter-title" style="margin-top:10px">哈夫币/租金比例（每元M，单选）</div>
      <div class="chips">
        <span class="chip" data-f="ratio03" data-group="ratio" data-v="0.3" onclick="toggleChip(this)">比例≥0.3</span>
        <span class="chip" data-f="ratio035" data-group="ratio" data-v="0.35" onclick="toggleChip(this)">比例≥0.35</span>
        <span class="chip" data-f="ratio04" data-group="ratio" data-v="0.4" onclick="toggleChip(this)">比例≥0.4</span>
        <span class="chip" data-f="ratio045" data-group="ratio" data-v="0.45" onclick="toggleChip(this)">比例≥0.45</span>
        <span class="chip" data-f="ratio05" data-group="ratio" data-v="0.5" onclick="toggleChip(this)">比例≥0.50</span>
      </div>
    </div>
    <div class="rules">{rules_html}</div>
    </div>
  </header>

  {good_html}
  {cand_html}
  {gone_html}

  <section class="sec">
    <div class="sec-h"><span class="fold">▾</span><h2>平台接入状态</h2></div>
    <div class="sec-b">{platforms_html}</div>
  </section>

  <footer>
    点击单号复制，点击整行打开原页。数据来自各平台公开接口，实时抓取，仅作信息聚合。
  </footer>
</div>
{js_script}
</body>
</html>"""


# ===========================================================================
# 旧版卡片渲染（保留备用，主流程已切换至 render_html_v2）
# ===========================================================================
def render_html(data):
    generated = data["generated_at"]
    good = data["good_accounts"]
    gone = data.get("gone_accounts", [])
    platforms = data["platforms"]

    # 页面 JS（独立字符串，避免 f-string 花括号转义）：复制单号 + 暂停开关 + 实时轮询
    js_script = """<script>
function copyCode(code) {
  var done = function() {
    var el = document.createElement('div');
    el.textContent = '已复制单号 ' + code;
    el.style.cssText = 'position:fixed;left:50%;bottom:80px;transform:translateX(-50%);' +
      'background:#1f6feb;color:#fff;padding:10px 18px;border-radius:8px;font-size:14px;z-index:999;';
    document.body.appendChild(el);
    setTimeout(function() { el.remove(); }, 1600);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(code).then(done).catch(function() { fallbackCopy(code, done); });
  } else {
    fallbackCopy(code, done);
  }
}
function fallbackCopy(code, done) {
  var ta = document.createElement('textarea');
  ta.value = code; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch(e) {}
  ta.remove();
}
function fmt(v, suffix) {
  if (v === null || v === undefined || v === '') return '<span class="na">—</span>';
  return v + (suffix || '');
}
function cardHtml(a, isNew) {
  var imgHtml = a.image_url ? '<img class="acc-img" src="' + a.image_url + '" alt="号截图" loading="lazy" onerror="this.style.display=\\'none\\'">' : '';
  var newBadge = isNew ? '<span class="ptype new-badge">新</span>' : '';
  var priceLine = (a.price_type === '租金') ? ('租金 ' + fmt(a.rent) + ' 元 + 押金 ' + fmt(a.deposit) + ' 元') : ('售价 ' + fmt(a.rent) + ' 元');
  return '<article class="acc-card" data-url="' + a.url + '">' +
    '<div class="acc-head">' +
      '<span class="plat plat-' + a.platform + '">' + a.platform + '</span>' +
      '<span class="ptype">' + a.price_type + '</span>' + newBadge +
      '<a class="acc-link" href="' + a.url + '" target="_blank" rel="noopener">查看原页</a>' +
    '</div>' + imgHtml +
    '<div class="acc-title">' + a.title + '</div>' +
    '<div class="acc-stats">' +
      '<div class="stat"><span class="sv">' + fmt(a.pure_coins_M, 'M') + '</span><span class="sl">纯币</span></div>' +
      '<div class="stat"><span class="sv">' + fmt(a.awm_bullets) + '</span><span class="sl">AWM子弹</span></div>' +
      '<div class="stat"><span class="sv">' + fmt(a.insurance_grids, '格') + '</span><span class="sl">保险箱</span></div>' +
      '<div class="stat"><span class="sv">' + fmt(a.weight_level, '级') + '</span><span class="sl">负重(靶场)</span></div>' +
      '<div class="stat"><span class="sv">' + fmt(a.stamina_level, '级') + '</span><span class="sl">体力(训练)</span></div>' +
      '<div class="stat"><span class="sv">' + fmt((Number(a.rent)||0)+(Number(a.deposit)||0), '元') + '</span><span class="sl">' + (a.price_type==='租金'?'租+押':'售价') + '</span></div>' +
    '</div>' +
    '<div class="acc-foot">' +
      '<span class="acc-code" onclick="copyCode(\\'' + (a.goods_code||'') + '\\')" title="点击复制单号">单号 ' + (a.goods_code||'') + ' ⧉</span>' +
      '<span>' + priceLine + '</span>' +
      '<span>抓取 ' + a.fetch_time + '</span>' +
    '</div>' +
  '</article>';
}
function goneCardHtml(a) {
  var priceLine = (a.price_type === '租金') ? ('租金 ' + fmt(a.rent) + ' 元 + 押金 ' + fmt(a.deposit) + ' 元') : ('售价 ' + fmt(a.rent) + ' 元');
  return '<article class="acc-card gone" data-url="' + a.url + '">' +
    '<div class="acc-head">' +
      '<span class="plat plat-' + a.platform + '">' + a.platform + '</span>' +
      '<span class="ptype gone-badge">已失效</span>' +
      '<a class="acc-link" href="' + a.url + '" target="_blank" rel="noopener">查看原页</a>' +
    '</div>' +
    '<div class="acc-title">' + a.title + '</div>' +
    '<div class="acc-stats">' +
      '<div class="stat"><span class="sv">' + fmt(a.pure_coins_M, 'M') + '</span><span class="sl">纯币</span></div>' +
      '<div class="stat"><span class="sv">' + fmt(a.awm_bullets) + '</span><span class="sl">AWM子弹</span></div>' +
      '<div class="stat"><span class="sv">' + fmt(a.insurance_grids, '格') + '</span><span class="sl">保险箱</span></div>' +
      '<div class="stat"><span class="sv">' + fmt(a.weight_level, '级') + '</span><span class="sl">负重(靶场)</span></div>' +
      '<div class="stat"><span class="sv">' + fmt(a.stamina_level, '级') + '</span><span class="sl">体力(训练)</span></div>' +
      '<div class="stat"><span class="sv">' + fmt((Number(a.rent)||0)+(Number(a.deposit)||0), '元') + '</span><span class="sl">' + (a.price_type==='租金'?'租+押':'售价') + '</span></div>' +
    '</div>' +
    '<div class="acc-foot">' +
      '<span>' + priceLine + '</span>' +
      '<span>失效于 ' + (a.gone_at || a.fetch_time) + '</span>' +
    '</div>' +
  '</article>';
}
function refreshState() {
  fetch('api/status').then(function(r) { return r.json(); }).then(function(s) {
    var btn = document.getElementById('btn-toggle');
    var st = document.getElementById('ctrl-state');
    if (s.paused) {
      btn.textContent = '▶ 恢复监控'; btn.classList.add('paused'); btn.disabled = false;
      st.textContent = '已暂停 · 不抓取不推送 · 最后抓取 ' + (s.last_run || '-');
    } else {
      btn.textContent = '⏸ 暂停监控'; btn.classList.remove('paused'); btn.disabled = false;
      st.textContent = '运行中 · 每1分钟自动抓取 · 最后抓取 ' + (s.last_run || '-') + ' · 好号 ' + s.good_count;
    }
  }).catch(function() {
    var btn = document.getElementById('btn-toggle');
    btn.textContent = '控制接口未连接'; btn.disabled = true;
    document.getElementById('ctrl-state').textContent = '请通过完整版地址访问以启用开关';
  });
}
function togglePause() {
  var btn = document.getElementById('btn-toggle');
  var paused = btn.classList.contains('paused');
  btn.disabled = true; btn.textContent = '处理中…';
  fetch(paused ? 'api/resume' : 'api/pause', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(j) {
      if (j.ok) { refreshState(); }
      else { btn.disabled = false; btn.textContent = '操作失败，重试'; }
    }).catch(function() {
      btn.disabled = false; btn.textContent = '操作失败，重试';
    });
}
// 实时轮询：每30秒对比最新数据，新增好号插入顶部(标"新")，消失好号移入失效区
function pollData() {
  fetch('api/data').then(function(r) { return r.json(); }).then(function(d) {
    if (!d.ok) return;
    var goodList = document.getElementById('good-list');
    var goneSec = document.getElementById('gone-sec');
    var goneList = document.getElementById('gone-list');
    var haveGood = {};
    var haveGone = {};
    var nodes = goodList.querySelectorAll('.acc-card');
    for (var i=0;i<nodes.length;i++) haveGood[nodes[i].getAttribute('data-url')] = true;
    if (goneList) {
      nodes = goneList.querySelectorAll('.acc-card');
      for (var j=0;j<nodes.length;j++) haveGone[nodes[j].getAttribute('data-url')] = true;
    }
    var i, a;
    // 新增好号 → 插入顶部
    for (i = d.good_accounts.length - 1; i >= 0; i--) {
      a = d.good_accounts[i];
      if (!haveGood[a.url]) {
        var el = document.createElement('div');
        el.innerHTML = cardHtml(a, true);
        goodList.insertBefore(el.firstChild, goodList.firstChild);
      }
    }
    // 本轮消失的好号（上轮有、本轮无）→ 移除并移入失效区
    d.gone_accounts.forEach(function(g) {
      if (!haveGone[g.url]) {
        var old = goodList.querySelector('[data-url="' + g.url + '"]');
        if (old) old.remove();
        if (!goneSec) {
          goneSec = document.createElement('section');
          goneSec.className = 'section'; goneSec.id = 'gone-sec';
          goneSec.innerHTML = '<h2>已失效好号（上轮有、本轮消失）</h2><div class="acc-list" id="gone-list"></div>';
          var after = goodList.closest('section').nextElementSibling;
          goodList.closest('section').parentNode.insertBefore(goneSec, after);
          goneList = goneSec.querySelector('#gone-list');
        }
        if (!goneList) goneList = document.getElementById('gone-list');
        var gel = document.createElement('div');
        gel.innerHTML = goneCardHtml(g);
        goneList.insertBefore(gel.firstChild, goneList.firstChild);
      }
    });
    // 更新计数与抓取时间
    var cnt = document.getElementById('good-count');
    if (cnt) cnt.textContent = d.good_count;
    var sub = document.querySelector('.sub');
    if (sub && d.generated_at) {
      sub.textContent = '最近抓取：' + d.generated_at + ' ｜ 每 1 分钟自动刷新';
    }
  }).catch(function() {});
}
refreshState();
pollData();
setInterval(pollData, 30000);
</script>
"""

    # 好号卡片
    if good:
        cards = []
        for a in good:
            price_line = (f"租金 {fmt(a['rent'])} 元" if a["price_type"] == "租金"
                          else f"售价 {fmt(a['rent'])} 元")
            if a["price_type"] == "租金":
                price_line += f" + 押金 {fmt(a['deposit'])} 元"
            cards.append(f"""
            <article class="acc-card" data-url="{esc(a['url'])}">
              <div class="acc-head">
                <span class="plat plat-{esc(a['platform'])}">{esc(a['platform'])}</span>
                <span class="ptype">{esc(a['price_type'])}</span>
                <a class="acc-link" href="{esc(a['url'])}" target="_blank" rel="noopener">查看原页</a>
              </div>
              {('<img class="acc-img" src="' + esc(a["image_url"]) + '" alt="号截图" loading="lazy" onerror="this.style.display=&#39;none&#39;">') if a.get('image_url') else ''}
              <div class="acc-title">{esc(a['title'][:60])}</div>
              <div class="acc-stats">
                <div class="stat"><span class="sv">{fmt(a['pure_coins_M'],'M')}</span><span class="sl">纯币</span></div>
                <div class="stat"><span class="sv">{fmt(a['awm_bullets'])}</span><span class="sl">AWM子弹</span></div>
                <div class="stat"><span class="sv">{fmt(a['insurance_grids'],'格')}</span><span class="sl">保险箱</span></div>
                <div class="stat"><span class="sv">{fmt(a['weight_level'],'级')}</span><span class="sl">负重(靶场)</span></div>
                <div class="stat"><span class="sv">{fmt(a['stamina_level'],'级')}</span><span class="sl">体力(训练)</span></div>
                <div class="stat"><span class="sv">{fmt(a['rent']+a['deposit'],'元')}</span><span class="sl">{'租+押' if a['price_type']=='租金' else '售价'}</span></div>
              </div>
              <div class="acc-foot">
                <span class="acc-code" onclick="copyCode('{esc(a.get('goods_code'))}')" title="点击复制单号">单号 {esc(a.get('goods_code'))} ⧉</span>
                <span>{esc(price_line)}</span>
                <span>抓取 {esc(a['fetch_time'])}</span>
              </div>
            </article>""")
        good_html = "\n".join(cards)
    else:
        good_html = """
        <div class="empty">
          <div class="empty-icon"></div>
          <p class="empty-title">当前无符合条件的账号</p>
          <p class="empty-sub">下一轮抓取将在 5 分钟后自动更新</p>
        </div>"""

    # 已失效好号（上一轮有、本轮已消失）
    gone_html = ""
    if gone:
        gone_cards = []
        for a in gone:
            price_line = (f"租金 {fmt(a['rent'])} 元" if a["price_type"] == "租金"
                          else f"售价 {fmt(a['rent'])} 元")
            if a["price_type"] == "租金":
                price_line += f" + 押金 {fmt(a['deposit'])} 元"
            gone_cards.append(f"""
            <article class="acc-card gone" data-url="{esc(a['url'])}">
              <div class="acc-head">
                <span class="plat plat-{esc(a['platform'])}">{esc(a['platform'])}</span>
                <span class="ptype gone-badge">已失效</span>
                <a class="acc-link" href="{esc(a['url'])}" target="_blank" rel="noopener">查看原页</a>
              </div>
              <div class="acc-title">{esc(a['title'][:60])}</div>
              <div class="acc-stats">
                <div class="stat"><span class="sv">{fmt(a['pure_coins_M'],'M')}</span><span class="sl">纯币</span></div>
                <div class="stat"><span class="sv">{fmt(a['awm_bullets'])}</span><span class="sl">AWM子弹</span></div>
                <div class="stat"><span class="sv">{fmt(a['insurance_grids'],'格')}</span><span class="sl">保险箱</span></div>
                <div class="stat"><span class="sv">{fmt(a['weight_level'],'级')}</span><span class="sl">负重(靶场)</span></div>
                <div class="stat"><span class="sv">{fmt(a['stamina_level'],'级')}</span><span class="sl">体力(训练)</span></div>
                <div class="stat"><span class="sv">{fmt(a['rent']+a['deposit'],'元')}</span><span class="sl">{'租+押' if a['price_type']=='租金' else '售价'}</span></div>
              </div>
              <div class="acc-foot">
                <span>{esc(price_line)}</span>
                <span>失效于 {esc(a.get('gone_at', a['fetch_time']))}</span>
              </div>
            </article>""")
        gone_html = ("<section class=\"section\" id=\"gone-sec\"><h2>已失效好号（上轮有、本轮消失）</h2>"
                     f"<div class=\"acc-list\" id=\"gone-list\">{''.join(gone_cards)}</div></section>")

    # 平台状态
    p_rows = []
    for p in platforms:
        st = p["status"]
        badge = {"已接入": "ok", "部分接入": "partial", "暂未接入": "off"}.get(st, "off")
        detail = esc(p.get("status_detail") or p.get("error") or "")
        p_rows.append(f"""
        <div class="plat-card plat-{badge}">
          <div class="plat-head">
            <span class="plat-name">{esc(p['name'])}</span>
            <span class="plat-badge b-{badge}">{esc(st)}</span>
          </div>
          <div class="plat-meta">
            <span>口径：{esc(p.get('price_type') or '-')}</span>
            <span>抓取 {p.get('fetched_count',0)} 条</span>
            <span>好号 {p.get('good_count',0)} 个</span>
          </div>
          <div class="plat-detail">{detail}</div>
          {f'<div class="plat-err">错误：{esc(p["error"])}</div>' if p.get('error') else ''}
        </div>""")
    platforms_html = "\n".join(p_rows)

    rules_html = "".join(f"<li>{esc(r)}</li>" for r in GOOD_RULES)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>三角洲行动好号监控</title>
<link rel="icon" href="{FAVICON_URI}">
<style>
:root {{
  --bg:#0a0e1a; --bg2:rgba(22,27,46,.7); --line:rgba(255,255,255,.08);
  --text:#e8ecf4; --mut:#8b95ad; --gold:#f0b429; --ok:#3fb950;
  --partial:#d29922; --off:#6e7681; --red:#f85149;
  --grad:linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  --gold-grad:linear-gradient(135deg, #f0b429 0%, #f7ce46 100%);
}}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:var(--bg); color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
  line-height:1.6; padding-bottom:60px; min-height:100vh;
  background-image:
    radial-gradient(ellipse at top left, rgba(102,126,234,.15) 0%, transparent 50%),
    radial-gradient(ellipse at bottom right, rgba(118,75,162,.15) 0%, transparent 50%);
  background-attachment:fixed;
}}
.wrap {{ max-width:1440px; margin:0 auto; padding:0 28px; }}
header {{ padding:36px 0 24px; }}
.hero {{ background:var(--bg2); backdrop-filter:blur(20px);
  border:1px solid var(--line); border-radius:20px; padding:28px 32px;
  box-shadow:0 8px 32px rgba(0,0,0,.3); }}
h1 {{ font-size:28px; font-weight:800; letter-spacing:-.5px;
  background:var(--gold-grad); -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; }}
h1 .dot {{ -webkit-text-fill-color:var(--gold); }}
.sub {{ color:var(--mut); font-size:13px; margin-top:8px; }}
.ctrl {{ margin-top:16px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }}
.ctrl-btn {{ padding:12px 28px; min-height:44px; border:none; border-radius:12px;
  font-size:14px; font-weight:700; cursor:pointer; color:#0a0e1a;
  background:var(--gold-grad); box-shadow:0 4px 16px rgba(240,180,41,.3);
  transition:all .2s; }}
.ctrl-btn:hover {{ transform:translateY(-1px); box-shadow:0 6px 20px rgba(240,180,41,.4); }}
.ctrl-btn.paused {{ background:linear-gradient(135deg, #f85149 0%, #ff6b6b 100%); color:#fff; }}
.ctrl-btn:disabled {{ opacity:.5; cursor:wait; transform:none; }}
.ctrl-state {{ font-size:12.5px; color:var(--mut); }}
.section {{ margin-top:32px; }}
.section h2 {{ font-size:17px; font-weight:700; margin-bottom:16px;
  padding-left:14px; border-left:3px solid; border-image:var(--gold-grad) 1; }}
.rules {{ background:var(--bg2); backdrop-filter:blur(20px);
  border:1px solid var(--line); border-radius:16px; padding:20px 24px; }}
.rules ul {{ list-style:none; }}
.rules li {{ padding:5px 0; font-size:14px; color:var(--text); }}
.rules .caliber {{ margin-top:12px; padding-top:12px; border-top:1px dashed var(--line);
  color:var(--mut); font-size:12.5px; }}
.acc-list {{ display:grid; gap:18px; grid-template-columns:repeat(auto-fill, minmax(380px, 1fr)); }}
.acc-card {{ background:var(--bg2); backdrop-filter:blur(20px);
  border:1px solid var(--line); border-radius:16px; padding:18px;
  transition:all .3s; position:relative; overflow:hidden; }}
.acc-card::before {{ content:''; position:absolute; top:0; left:0; right:0; height:2px;
  background:var(--grad); opacity:0; transition:opacity .3s; }}
.acc-card:hover {{ transform:translateY(-2px); border-color:rgba(240,180,41,.3);
  box-shadow:0 12px 40px rgba(0,0,0,.4); }}
.acc-card:hover::before {{ opacity:1; }}
.acc-head {{ display:flex; align-items:center; gap:8px; margin-bottom:10px; }}
.plat {{ font-size:11px; font-weight:700; padding:3px 10px; border-radius:6px;
  text-transform:uppercase; letter-spacing:.5px; }}
.plat-虚贝 {{ background:rgba(31,111,235,.2); color:#58a6ff; }}
.plat-DD373 {{ background:rgba(63,185,80,.2); color:#3fb950; }}
.plat-盼之 {{ background:rgba(210,153,34,.2); color:#e3b341; }}
.plat-换号吧 {{ background:rgba(188,140,255,.2); color:#bc8cff; }}
.ptype {{ font-size:11px; color:var(--mut); }}
.acc-link {{ margin-left:auto; font-size:12px; color:var(--gold); text-decoration:none;
  padding:8px 16px; border:1px solid rgba(240,180,41,.3); border-radius:8px;
  transition:all .2s; }}
.acc-link:hover {{ background:rgba(240,180,41,.1); border-color:var(--gold); }}
.acc-title {{ font-size:12px; color:var(--mut); margin-bottom:12px;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
.acc-img {{ width:100%; max-height:260px; object-fit:cover; border-radius:12px;
  margin-bottom:12px; background:#0a0e1a; border:1px solid var(--line); }}
.acc-stats {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }}
.stat {{ background:rgba(10,14,26,.6); border:1px solid var(--line);
  border-radius:10px; padding:10px 8px; text-align:center; }}
.stat .sv {{ display:block; font-size:17px; font-weight:800;
  background:var(--gold-grad); -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; }}
.stat .sl {{ font-size:11px; color:var(--mut); margin-top:2px; }}
.acc-code {{ font-size:11px; color:var(--gold); font-weight:600; cursor:pointer;
  border-bottom:1px dashed rgba(240,180,41,.4); }}
.acc-code:hover {{ color:#ffd75e; }}
.acc-foot {{ display:flex; justify-content:space-between; align-items:center;
  margin-top:12px; font-size:11px; color:var(--mut); }}
.na {{ color:var(--off); }}
.acc-card.gone {{ opacity:.5; border-color:rgba(110,118,129,.3); }}
.acc-card.gone .sv {{ -webkit-text-fill-color:var(--off); }}
.gone-badge {{ color:var(--red); font-weight:700; font-size:10px; }}
.new-badge {{ color:var(--ok); font-weight:700; font-size:10px;
  background:rgba(63,185,80,.15); padding:2px 8px; border-radius:4px; margin-left:4px; }}
.empty {{ text-align:center; padding:60px 20px; color:var(--mut); }}
.empty-icon {{ width:60px; height:60px; margin:0 auto 16px; border-radius:50%;
  border:2px dashed var(--off); display:flex; align-items:center; justify-content:center;
  font-size:24px; }}
.empty-title {{ font-size:17px; color:var(--text); margin-bottom:8px; }}
.empty-sub {{ font-size:13px; }}
.plat-list {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fill, minmax(340px, 1fr)); }}
.plat-card {{ background:var(--bg2); backdrop-filter:blur(20px);
  border:1px solid var(--line); border-radius:14px; padding:16px 18px;
  border-left:3px solid var(--off); }}
.plat-card.plat-ok {{ border-left-color:var(--ok); }}
.plat-card.plat-partial {{ border-left-color:var(--partial); }}
.plat-card.plat-off {{ border-left-color:var(--off); opacity:.7; }}
.plat-head {{ display:flex; align-items:center; gap:8px; }}
.plat-name {{ font-weight:700; font-size:14px; }}
.plat-badge {{ font-size:11px; padding:3px 10px; border-radius:6px; margin-left:auto;
  font-weight:600; }}
.b-ok {{ background:rgba(63,185,80,.15); color:var(--ok); }}
.b-partial {{ background:rgba(210,153,34,.15); color:var(--partial); }}
.b-off {{ background:rgba(110,118,129,.15); color:var(--off); }}
.plat-meta {{ display:flex; gap:16px; font-size:12px; color:var(--mut); margin:8px 0; }}
.plat-detail {{ font-size:11.5px; color:var(--mut); line-height:1.5; }}
.plat-err {{ font-size:11.5px; color:var(--red); margin-top:6px; }}
footer {{ margin-top:40px; padding-top:20px; border-top:1px solid var(--line);
  color:var(--mut); font-size:12px; text-align:center; }}
@media (min-width:640px) {{
  .acc-stats {{ grid-template-columns:repeat(6,1fr); }}
  h1 {{ font-size:32px; }}
}}
@media (max-width:640px) {{
  .acc-list {{ grid-template-columns:1fr; }}
  .plat-list {{ grid-template-columns:1fr; }}
  .wrap {{ padding:0 16px; }}
  .hero {{ padding:20px; border-radius:14px; }}
  h1 {{ font-size:22px; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>三角洲行动<span class="dot">·</span>好号监控</h1>
    <div class="sub">最近抓取：{esc(generated)} ｜ 每 5 分钟自动更新</div>
  </header>

  <section class="section">
    <h2>好号筛选规则（同时满足）</h2>
    <div class="rules">
      <ul>{rules_html}</ul>
      <div class="caliber">
        筛选口径：9格保险 / 靶场≥6级(负重) / 训练中心≥6级(体力) / AWM≥50发；
        已取消租金+押金≤500元、纯币≥80M 限制。纯币单位 M=百万哈夫币，1M=100万；
        售号平台标题中"万/w"÷100→M、"亿"×100→M。
      </div>
    </div>
  </section>

  <section class="section">
    <h2>符合条件的账号（<span id="good-count">{len(good)}</span>）</h2>
    <div class="acc-list" id="good-list">{good_html}</div>
  </section>

  {gone_html}

  <section class="section">
    <h2>平台接入状态</h2>
    <div class="plat-list">{platforms_html}</div>
  </section>

  <footer>
    数据来源：各平台公开列表页/API，全部来自实时抓取，不登录不注册。
    售价/租金为平台挂牌口径，实际以下单页为准。本页仅作信息聚合，不构成交易建议。
  </footer>
</div>
{js_script}
</body>
</html>"""


# ---------------------------------------------------------------------------
# 通道自愈：每次运行自动确保所有服务存活（防止云沙箱回收后失联）
# ---------------------------------------------------------------------------
def ensure_services():
    """系统定时任务是"根心跳"，每次触发都重建整个服务栈：
    watchdog_delta.sh → uvicorn → cloudflared → run_every_5min.sh。
    即使云沙箱回收了所有进程，下一次定时触发时自动恢复。
    """
    import subprocess
    TUNNEL_ID = "2121cad5-261f-45bd-8861-cfe2e69d9fad"
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)

    def _pgrep(pattern):
        try:
            r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=3)
            return bool(r.stdout.strip())
        except Exception:
            return False

    def _port_listening(port):
        try:
            r = subprocess.run(["ss", "-tln"], capture_output=True, text=True, timeout=3)
            return f":{port} " in r.stdout
        except Exception:
            return False

    def _spawn(args, logfile=None):
        try:
            if logfile:
                out = open(os.path.join(BASE_DIR, logfile), "ab")
                subprocess.Popen(args, cwd=BASE_DIR, env=env,
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True)
            else:
                subprocess.Popen(args, cwd=BASE_DIR, env=env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        except Exception:
            pass

    # 1. watchdog_delta.sh（超级守护，每20秒自检子进程）
    if not _pgrep("watchdog_delta[.]sh"):
        _spawn(["setsid", "./watchdog_delta.sh"])
        log("[自愈] 拉起 watchdog_delta.sh")

    # 2. uvicorn (8777)
    if not _port_listening(8777):
        _spawn(["python3", "-m", "uvicorn", "control_server:app",
                "--host", "127.0.0.1", "--port", "8777"], logfile="server.log")
        log("[自愈] 拉起 uvicorn:8777")

    # 3. cloudflared 固定隧道
    if not _pgrep("[c]loudflared tunnel"):
        _spawn(["./cloudflared", "tunnel", "--url", "http://127.0.0.1:8777", "run", TUNNEL_ID],
               logfile="fixed_tunnel.log")
        log("[自愈] 拉起 cloudflared 固定隧道")

    # 4. run_every_5min.sh（5分钟循环抓取）
    if not _pgrep("run_every_5[m]in"):
        _spawn(["./run_every_5min.sh"])
        log("[自愈] 拉起 run_every_5min.sh")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    # 通道自愈：先确保所有服务存活，再开始抓取
    ensure_services()

    log("监控轮次开始")
    platforms = []
    all_accounts = []

    # 上一轮好号（用于已失效标注）
    prev_good_map = load_prev_good()
    if prev_good_map:
        log(f"上一轮好号 {len(prev_good_map)} 个（用于失效对比）")

    # 1. 虚贝主源
    try:
        info, accs = fetch_xubei()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_xubei 未捕获异常: {e}")

    # 2. 虚贝租号大厅（兜底，主源失败时仍记录）
    try:
        info, accs = fetch_xubei_renthall()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_xubei_renthall 未捕获异常: {e}")

    # 3. DD373
    try:
        info, accs = fetch_dd373()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_dd373 未捕获异常: {e}")

    # 4. 盼之
    try:
        info, accs = fetch_pzds()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_pzds 未捕获异常: {e}")

    # 5. 换号吧
    try:
        info, accs = fetch_hhb()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_hhb 未捕获异常: {e}")

    # 6. ysc9 雅俗（结构化 API，字段齐全）
    try:
        info, accs = fetch_ysc9()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_ysc9 未捕获异常: {e}")

    # 7. 61xm 星铭代售（结构化 API，与 ysc9 同构）
    try:
        info, accs = fetch_61xm()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_61xm 未捕获异常: {e}")

    # 8. ys9d 艺术（哈夫租售：租金+押金，字段齐全）
    try:
        info, accs = fetch_ys9d()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_ys9d 未捕获异常: {e}")

    # 9. 98lj 凌绝（三角洲租号专区：租金+押金，与 ys9d 同套系统）
    try:
        info, accs = fetch_98lj()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_98lj 未捕获异常: {e}")

    # 10. 叮当商行（公开接口：租金+押金，字段齐全）
    try:
        info, accs = fetch_dingdang()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_dingdang 未捕获异常: {e}")

    # 11. 皓星商行（公开接口：租金+押金，字段齐全）
    try:
        info, accs = fetch_xinhe()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_xinhe 未捕获异常: {e}")

    # 12. 准星代售（公开接口：资产号租赁，租金+押金）
    try:
        info, accs = fetch_zxfps()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_zxfps 未捕获异常: {e}")

    # 13. 三一商行（服务端渲染租号大厅：租金+押金）
    try:
        info, accs = fetch_3f1()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_3f1 未捕获异常: {e}")

    # 14. 夏日商行（Nuxt API 租号：租金+押金）
    try:
        info, accs = fetch_xiar()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_xiar 未捕获异常: {e}")

    # 15. 泓星代售（SPA API 三角洲租号：租金+押金）
    try:
        info, accs = fetch_hxfps()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_hxfps 未捕获异常: {e}")

    # 16. 哈夫克基地（服务端渲染卡片JSON 租号：租金+押金）
    try:
        info, accs = fetch_hafuke()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_hafuke 未捕获异常: {e}")

    # 17. 阿龙商行（REST API 按天租：租金+押金）
    try:
        info, accs = fetch_along888()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_along888 未捕获异常: {e}")

    # 18. 安易游（H5 公开接口 /api/client/product/list 租号：租金+押金）
    try:
        info, accs = fetch_ayy66()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_ayy66 未捕获异常: {e}")

    # 19. 胖猫商行（公开接口 /server/api/missions.php?action=list 租号：租金+押金）
    try:
        info, accs = fetch_pangmao()
        platforms.append(info)
        all_accounts.extend(accs)
    except Exception as e:
        log(f"fetch_pangmao 未捕获异常: {e}")

    # 8. 暂未接入（页面登记；未接入平台已全部移除）
    platforms.extend(build_not_connected())

    # 好号（跨平台去重：同 url 只留一条）
    seen = set()
    good_accounts = []
    for a in all_accounts:
        if is_good(a) and a["url"] not in seen:
            seen.add(a["url"])
            good_accounts.append(a)
    good_accounts.sort(key=lambda x: (x.get("rent") or 0) + (x.get("deposit") or 0))

    # 全量账号分区（供网页展示）：标记 is_good，跨平台去重
    seen_all = set()
    listed_accounts = []
    for a in all_accounts:
        if a["url"] in seen_all:
            continue
        seen_all.add(a["url"])
        b = dict(a)
        b["is_good"] = is_good(a)
        listed_accounts.append(b)
    listed_accounts.sort(key=lambda x: (not x.get("is_good"),
                                        (x.get("rent") or 0) + (x.get("deposit") or 0)))

    # 已失效标注：上一轮有、本轮没有的好号
    gone_accounts = mark_gone(good_accounts, prev_good_map)

    # 新好号（本轮出现、上一轮没有的）→ PushPlus 微信推送（有变化才推）
    prev_urls = set(prev_good_map.keys())
    new_good = [a for a in good_accounts if a["url"] not in prev_urls]
    if new_good or gone_accounts:
        send_pushplus(new_good, gone_accounts)
    send_wechat_webhook(new_good, gone_accounts)

    data = {
        "generated_at": now_str(),
        "rules": GOOD_RULES,
        "platforms": platforms,
        "good_accounts": good_accounts,
        "gone_accounts": gone_accounts,
        "all_accounts": listed_accounts,
        "meta": {
            "total_listed": len(all_accounts),
            "good_count": len(good_accounts),
            "gone_count": len(gone_accounts),
            "listed_count": len(listed_accounts),
            "schedule": "every 1 minute",
        },
    }

    # 先备份上一轮，再写本轮（下一轮用本轮做失效对比）
    try:
        if os.path.exists(RESULTS_JSON):
            import shutil
            shutil.copy(RESULTS_JSON, PREV_JSON)
    except Exception as e:
        log(f"备份上一轮 results.json 失败: {e}")

    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"results.json 写入：好号 {len(good_accounts)} / 候选 {len(all_accounts)} / 已失效 {len(gone_accounts)}")

    html = render_html_v3(data)
    with open(RESULT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"result.html 写入：{RESULT_HTML}")
    log("监控轮次结束")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"[FATAL] {e}")
        traceback.print_exc()
    finally:
        sys.exit(0)
