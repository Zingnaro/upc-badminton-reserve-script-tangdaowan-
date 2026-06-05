#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""羽毛球场地自动预订脚本"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8")
    import msvcrt

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "booking_config.json")
SNAPSHOT_DIR = os.path.join(SCRIPT_DIR, "snapshots")

ALL_TIME_OPTIONS = [
    ("A", "08:00-09:30 (1.5h)", ["08:00-08:30", "08:30-09:00", "09:00-09:30"], 7.5, "weekend"),
    ("B", "08:30-10:00 (1.5h)", ["08:30-09:00", "09:00-09:30", "09:30-10:00"], 7.5, "weekend"),
    ("C", "09:30-11:00 (1.5h)", ["09:30-10:00", "10:00-11:00"],                  7.5, "weekend"),
    ("D", "11:00-12:00 (1h)",   ["11:00-12:00"],  5,  "weekend"),
    ("E", "12:00-13:00 (1h)",   ["12:00-13:00"],  5,  "weekend"),
    ("F", "13:00-14:00 (1h)",   ["13:00-14:00"],  5,  "weekend"),
    ("G", "14:00-15:00 (1h)",   ["14:00-15:00"],  5,  "weekend"),
    ("H", "15:00-16:00 (1h)",   ["15:00-16:00"],  5,  "weekend"),
    ("I", "16:00-17:00 (1h)",   ["16:00-17:00"],  5,  "all"),
    ("J", "17:00-18:00 (1h)",   ["17:00-18:00"], 10,  "all"),
    ("K", "18:00-19:00 (1h)",   ["18:00-19:00"], 10,  "all"),
    ("L", "19:00-20:00 (1h)",   ["19:00-20:00"], 10,  "all"),
    ("M", "20:00-21:00 (1h)",   ["20:00-21:00"], 10,  "all"),
]

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

HALLS = [
    {"name": "主馆羽毛球", "lxbh": "Y"},
    {"name": "副馆羽毛球", "lxbh": "A"},
]

COURT_PRIORITY = {"A": ["1", "6", "5", "7"]}

BASE_URL = "http://26501.koksoft.com"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 2
RETRY_DELAY = 1.5


def ts():
    return time.strftime("%H:%M:%S")

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def pout(msg):
    print(f"[{ts()}] {msg}")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        pout(f"[错误] 配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 如果配置了 wxkey_url，自动提取 wxkey 并覆盖 wxkey_Y / wxkey_A
    url = cfg.get("wxkey_url", "").strip()
    if url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        key = params.get("wxkey", [None])[0]
        if key:
            cfg["wxkey_Y"] = key
            cfg["wxkey_A"] = key
            pout(f"[自动提取] wxkey 从 URL 解析成功")
        else:
            pout("[警告] wxkey_url 中未找到 wxkey 参数，使用手动配置的 key")

    return cfg

def write_log(msg, log_file=None):
    if log_file:
        log_path = os.path.join(SCRIPT_DIR, log_file)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{now()}] {msg}\n")


def _json_request(method, url, data=None, params=None):
    if params:
        query = urllib.parse.urlencode(params)
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}{query}"
    else:
        full_url = url
    req = urllib.request.Request(full_url)
    req.add_header("User-Agent",
                   "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36")
    if method == "POST" and data:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        req.data = encoded
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def http_get_with_retry(url, params=None, label=""):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return _json_request("GET", url, params=params)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            last_err = e
            if attempt < MAX_RETRIES:
                pout(f"    [{label}] 加载失败，{RETRY_DELAY}s 后重试({attempt}/{MAX_RETRIES})...")
                time.sleep(RETRY_DELAY)
    raise last_err


def http_post_with_retry(url, data=None, label=""):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return _json_request("POST", url, data=data)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            last_err = e
            if attempt < MAX_RETRIES:
                pout(f"    [{label}] 提交失败，{RETRY_DELAY}s 后重试({attempt}/{MAX_RETRIES})...")
                time.sleep(RETRY_DELAY)
    raise last_err


def query_one_hall(wxkey, lxbh, hall_name, target_date_str):
    params = {
        "pagesize": "0",
        "pagenum": "0",
        "searchparam": f"orderdate={target_date_str}|lxbh={lxbh}",
        "wxkey": wxkey,
    }
    url = f"{BASE_URL}/GetForm.aspx?datatype=viewchangdi4weixinv"
    try:
        resp = http_get_with_retry(url, params, label=hall_name)
    except Exception as e:
        return (hall_name, lxbh, None, f"网络异常: {e}")
    if not resp[0]:
        return (hall_name, lxbh, None, resp[1] if len(resp) > 1 else "未知错误")
    inner = json.loads(resp[1])
    rows = inner["rows"]
    grid = {}
    for row in rows:
        timespan = f"{row['timemc']}-{row['endtimemc']}"
        cdcount = int(row["cdcount"])
        courts = {}
        for j in range(1, cdcount + 1):
            cdbh = row.get(f"cdbh{j}", "")
            cdmc = row.get(f"cdmc{j}", "")
            status = row.get(f"c{j}", "")
            lx = row.get(f"lxbh{j}", "")
            courts[f"{lx}:{cdbh}"] = {"name": cdmc, "status": status}
        grid[timespan] = courts
    return (hall_name, lxbh, grid, None)


def query_both_halls(cfg, target_date_str):
    tasks = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        for hall in HALLS:
            wxkey = cfg.get(f"wxkey_{hall['lxbh']}", "")
            if not wxkey:
                pout(f"  跳过 {hall['name']}：缺少 wxkey")
                continue
            tasks.append(executor.submit(
                query_one_hall, wxkey, hall["lxbh"], hall["name"], target_date_str
            ))
    results = []
    for fut in as_completed(tasks):
        name, lxbh, grid, err = fut.result()
        if err:
            pout(f"  [失败] {name}: {err}")
        elif grid is None:
            pout(f"  [失败] {name}: 返回数据为空")
        else:
            results.append((name, lxbh, grid))
    return results


def save_snapshot(hall_name, grid, target_date_str):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{target_date_str}_{hall_name}_{timestamp}.txt"
    filepath = os.path.join(SNAPSHOT_DIR, filename)

    STATUS_CN = {"i": "可订", "u": "未放", "o": "已售"}

    all_courts = {}
    for courts in grid.values():
        for key, info in courts.items():
            all_courts[key] = info["name"]
    sorted_courts = sorted(all_courts.items(),
                           key=lambda x: (x[0].split(":")[0], int(x[0].split(":")[1])))

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"场馆: {hall_name}\n")
        f.write(f"日期: {target_date_str}\n")
        f.write(f"查询时间: {now()}\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"{'时段':<20}")
        for _, cname in sorted_courts:
            f.write(f"{cname:<8}")
        f.write("\n")
        f.write("-" * (20 + 8 * len(sorted_courts)) + "\n")
        for slot in sorted(grid.keys()):
            f.write(f"{slot:<20}")
            for ckey, _ in sorted_courts:
                info = grid[slot].get(ckey)
                status = STATUS_CN.get(info["status"], info["status"]) if info else "-"
                f.write(f"{status:<8}")
            f.write("\n")

    pout(f"  场地快照已保存: snapshots/{filename}")
    return filepath


def try_book_on_hall(hname, lxbh, grid, required_slots, label, cost,
                     cfg, target_str_short, log_file):
    all_courts = find_all_courts_for_slots(grid, required_slots)
    if not all_courts:
        return "none"

    pout(f"  {hname} {len(all_courts)} 块空场，立即尝试...")
    for court_key, court_name in all_courts:
        _, cdbh = court_key.split(":")
        cdstring = build_cdstring(lxbh, cdbh, required_slots)
        write_log(f"尝试: {hname} {court_name} {label}", log_file)

        try:
            ok, msg = book_court(cfg.get(f"wxkey_{lxbh}", ""), lxbh, cdbh,
                                 target_str_short, cdstring, hname)
        except Exception as e:
            pout(f"  [网络错误] {hname} {court_name}: {e}")
            write_log(f"提交网络错误: {e}", log_file)
            continue

        if ok:
            pout(f"  ✓ 预订成功！{hname} {court_name} {label}")
            write_log(f"预订成功: {hname} {court_name} {label}", log_file)
            return "ok"
        else:
            pout(f"  ✗ {court_name}: {msg}")
            write_log(f"失败: {msg}", log_file)
            if "余额" in msg:
                import re
                m = re.search(r'[\d.]+', msg)
                need = m.group() if m else "?"
                pout(f"  ⚠ 余额不足（需 ¥{need}），视为放弃。")
                write_log(f"余额不足 需{need}元，放弃", log_file)
                return "no_balance"
            if "锁定" in msg:
                pout(f"    → 已被抢，换下一块...")
                continue
    return "locked"


def find_all_courts_for_slots(grid, required_slots):
    if not required_slots or not grid:
        return []
    common_keys = None
    for slot in required_slots:
        if slot not in grid:
            return []
        keys = set(grid[slot].keys())
        common_keys = keys if common_keys is None else common_keys & keys
    if not common_keys:
        return []
    result = []
    for key in _sort_by_priority(common_keys):
        if all(grid[slot][key]["status"] == "i" for slot in required_slots):
            result.append((key, grid[required_slots[0]][key]["name"]))
    return result


def _sort_by_priority(keys):
    def rank(key):
        lx, num = key.split(":")
        prio = COURT_PRIORITY.get(lx, [])
        try:
            return prio.index(num)
        except ValueError:
            return len(prio)
    return sorted(keys, key=rank)


def build_cdstring(lxbh, cdbh, required_slots):
    parts = [f"{lxbh}:{cdbh},{slot}" for slot in required_slots]
    return ";".join(parts) + ";"


def book_court(wxkey, lxbh, cdbh, datestring, cdstring, hall_name):
    params_search = json.dumps({
        "datestring": datestring,
        "cdstring": cdstring,
        "paytype": "M",
    })
    data = {
        "searchparam": params_search,
        "wxkey": wxkey,
        "classname": "saasbllclass.CommonFuntion",
        "funname": "MemberOrderfromWx",
    }
    url = f"{BASE_URL}/HomefuntionV2json.aspx"
    resp = http_post_with_retry(url, data, label=hall_name)
    if resp[0] is True:
        return True, "预订成功"
    else:
        err_msg = str(resp[1]) if len(resp) > 1 else "未知错误"
        return False, err_msg


def get_valid_options(target_date):
    wd = target_date.weekday()
    if wd < 5:
        label = f"工作日（{WEEKDAY_NAMES[wd]}），仅 16:00 后开放"
        options = [o for o in ALL_TIME_OPTIONS if o[4] in ("all", "weekday")]
    else:
        label = f"周末（{WEEKDAY_NAMES[wd]}），全天开放"
        options = list(ALL_TIME_OPTIONS)
    return options, label


def print_options(valid_options):
    print()
    print("=" * 55)
    print("  可选时段（字母+回车选择，输入 0 放弃）")
    print("=" * 55)
    for code, label, slots, cost, _ in valid_options:
        print(f"  [{code}]  {label:<22} {cost:>5}元")
    print("=" * 55)


def pick_choices(valid_options, weekday_label):
    print(f"\n  {weekday_label}")
    print_options(valid_options)

    choices = []
    for i, nth in enumerate(["第一", "第二", "第三"]):
        hint = "输入字母如 L" if i == 0 else "输入字母如 L，或 0 放弃"
        print(f"\n  选择【{nth}志愿】（{hint}）:")
        while True:
            sel = input("  > ").strip().upper()
            if sel == "0":
                choices.append(None)
                break
            matched = None
            for code, label, slots, cost, _ in valid_options:
                if sel == code:
                    matched = (code, label, slots, cost)
                    break
            if matched:
                choices.append(matched)
                pout(f"已选择 [{matched[0]}] {matched[1]}")
                break
            print("  无效输入，请重新输入:")
    return [c for c in choices if c is not None]


def verify_key(wxkey, lxbh, label):
    """验证 wxkey 是否有效。返回 (ok, msg)"""
    url = f"{BASE_URL}/GetForm.aspx?datatype=viewchangdi4weixinv&pagesize=0&pagenum=0&searchparam=orderdate%3D{date.today().strftime('%Y-%m-%d')}%7Clxbh%3D{lxbh}&wxkey={wxkey}"
    try:
        resp = _json_request("GET", url, params={})
        if resp[0]:
            return True, "有效"
        else:
            return False, resp[1] if len(resp) > 1 else "未知错误"
    except Exception as e:
        return False, str(e)


def main():
    pout("=== 羽毛球场地自动预订 ===")
    cfg = load_config()
    log_file = cfg.get("log_file", "")

    # ── key 有效性校验 ──
    for hall in HALLS:
        wxkey = cfg.get(f"wxkey_{hall['lxbh']}", "")
        ok, msg = verify_key(wxkey, hall["lxbh"], hall["name"])
        if not ok:
            pout(f"[错误] {hall['name']} wxkey 已失效: {msg}")
            pout(f"       请重新进入公众号底部菜单，获取新链接后更新配置文件。")
            sys.exit(1)
    pout("wxkey 校验通过")

    now_dt = time.localtime()
    if now_dt.tm_hour < 8:
        target = date.today() + timedelta(days=1)
        release_label = "今天"
    else:
        target = date.today() + timedelta(days=2)
        release_label = "明天"

    target_str_iso = target.strftime("%Y-%m-%d")
    target_str_short = f"{target.year}-{target.month}-{target.day}"
    pout(f"目标预订日期: {target_str_iso}（{release_label} 8:00 释放）")
    write_log(f"启动 — 目标 {target_str_iso}", log_file)

    valid_options, weekday_label = get_valid_options(target)
    choices = pick_choices(valid_options, weekday_label)
    if not choices:
        pout("未选择任何志愿，退出。")
        write_log("未选择志愿，退出", log_file)
        return
    write_log(f"志愿: {' → '.join(c[1] for c in choices)}", log_file)

    max_cost = max(c[3] for c in choices)
    print()
    print("  ╔══════════════════════════════╗")
    print(f"  ║  本次最大费用: ¥{max_cost}          ║")
    print(f"  ║  请确保会员余额 ≥ ¥{max_cost}      ║")
    print("  ╚══════════════════════════════╝")

    next_8am = time.mktime((
        now_dt.tm_year, now_dt.tm_mon, now_dt.tm_mday,
        8, 0, 0, 0, 0, -1
    ))
    if now_dt.tm_hour >= 8:
        next_8am += 86400
    wait_sec = next_8am - time.time()
    if wait_sec > 0:
        wait_min = int(wait_sec // 60)
        pout(f"等待至 {release_label} 08:00:00（约 {wait_min} 分钟）")
        pout(f"输入 c + 回车 可随时取消等待")
        write_log(f"等待 {wait_min} 分钟至 {release_label} 08:00", log_file)

        while time.time() < next_8am:
            time.sleep(0.3)
            if sys.platform == "win32" and msvcrt.kbhit():
                ch = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                if ch == "c":
                    pout("收到取消指令，退出。")
                    write_log("用户取消等待", log_file)
                    return
    pout("到点！立即并行查询主馆 + 副馆...")
    write_log("到点开始查询", log_file)

    for retry in range(1, 4):
        if retry > 1:
            wait_s = 1.5 * retry
            pout(f"  第 {retry} 轮重试（{wait_s:.1f}s 后）...")
            time.sleep(wait_s)

        fut_to_hall = {}
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                for hall in HALLS:
                    wxkey = cfg.get(f"wxkey_{hall['lxbh']}", "")
                    if not wxkey:
                        pout(f"  [跳过] {hall['name']}：缺少 wxkey")
                        write_log(f"跳过 {hall['name']}：缺少 wxkey", log_file)
                        continue
                    fut = executor.submit(query_one_hall, wxkey, hall["lxbh"],
                                          hall["name"], target_str_iso)
                    fut_to_hall[fut] = hall

                if not fut_to_hall:
                    pout("[错误] 没有任何可用的 wxkey，无法查询。")
                    write_log("无可用 wxkey", log_file)
                    return

                results_by_lxbh = {}
                for fut in as_completed(fut_to_hall):
                    hall = fut_to_hall[fut]
                    name, lxbh, grid, err = fut.result()
                    if err:
                        pout(f"  [失败] {name}: {err}")
                        write_log(f"查询失败 {name}: {err}", log_file)
                        continue
                    if grid is None:
                        pout(f"  [失败] {name}: 返回数据为空")
                        write_log(f"查询失败 {name}: 返回数据为空", log_file)
                        continue
                    results_by_lxbh[lxbh] = (name, lxbh, grid)
                    pout(f"  {name} 查询完成")
                    write_log(f"查询完成 {name}", log_file)
                    save_snapshot(name, grid, target_str_iso)

                if not results_by_lxbh and retry < 3:
                    write_log(f"第{retry}轮两馆查询失败，重试", log_file)
                    continue
                if not results_by_lxbh:
                    pout("两馆均查询失败，放弃。")
                    write_log("两馆均查询失败", log_file)
                    return

                remaining = list(choices)
                for lxbh in ("A", "Y"):
                    if lxbh not in results_by_lxbh:
                        continue
                    name, lxbh, grid = results_by_lxbh[lxbh]
                    pout(f"  尝试在 {name} 预订...")
                    write_log(f"尝试 {name}", log_file)
                    for choice_idx, (code, label, required_slots, cost) in enumerate(remaining):
                        result = try_book_on_hall(name, lxbh, grid, required_slots,
                                                  label, cost, cfg, target_str_short, log_file)
                        if result == "ok":
                            return
                        if result == "no_balance":
                            return

                break  # 查询+预订都执行完了，退出重试循环

        except Exception as e:
            pout(f"  [异常] {e}")
            write_log(f"异常(第{retry}轮): {e}", log_file)
            import traceback
            write_log(traceback.format_exc(), log_file)
            if retry >= 3:
                pout("  已达最大重试次数，放弃。")
                return

    pout("所有志愿均无空场或被抢。")
    write_log("所有志愿无空场", log_file)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pout("用户中断。")
    except Exception as e:
        pout(f"[异常] {e}")
