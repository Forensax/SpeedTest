'''
直链获取地址
https://www.i4.cn/firmware.html

环境
pip install requests
'''

import requests
import threading
import time
import sys

# --- 配置区 ---

# 1. 要同时下载的URL列表
URLS = [
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/093-39795/6A1A4D22-A7DC-4C2C-A147-D4D9D4EB7D1F/iPhone18,4_26.0_23A341_Restore.ipsw",
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/089-02043/59201911-1F46-4505-A090-20ED7B1239F2/iPhone18,4_26.1_23B82_Restore.ipsw",
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/093-44415/65359B2E-9997-4686-B335-CEEA4524A334/iPhone18,4_26.0.1_23A355_Restore.ipsw",
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/089-14211/A9EC7D63-0F1D-49B9-A57B-1D0C85EE98F8/iPhone18,4_26.1_23B85_Restore.ipsw",
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/089-02032/9FC73E4F-1EE3-452D-A18C-117484A33863/iPhone17,1_26.1_23B82_Restore.ipsw",
    "https://updates.cdn-apple.com/2025SummerFCS/fullrestores/093-21361/F0033AA8-4016-4030-BA3A-5C6E3E1FE7D4/iPhone17,1_18.6.2_22G100_Restore.ipsw",
    "https://updates.cdn-apple.com/2025WinterFCS/fullrestores/072-68203/37420C35-16EC-4466-850B-8F755C43FC73/iPhone17,1_18.3_22D63_Restore.ipsw",
    "https://updates.cdn-apple.com/2024FallFCS/fullrestores/062-77773/01645362-ACAC-4B29-BF73-A5397FA1034A/iPhone17,1_18.0_22A3354_Restore.ipsw"
]

# 2. 从套接字读取的块大小（字节）
#    数据被读取后会立即丢弃
DOWNLOAD_CHUNK_SIZE = 1024 * 1024 # 1 MB

# --- 全局状态 ---
total_bytes_downloaded = 0
data_lock = threading.Lock()

# --- 核心下载逻辑 ---

def download_file_worker(url):
    """
    一个工作线程，在一个无限循环中下载单个文件（使用单线程）。
    """
    global total_bytes_downloaded
    
    while True:
        try:
            # stream=True 是关键：它不会一次性把所有内容读入内存
            with requests.get(url, stream=True, timeout=10) as r:
                r.raise_for_status() # 如果http状态码是4xx或5xx，则抛出异常
                
                # iter_content 会迭代下载的数据
                # 我们不把 chunk 写入文件，它在循环结束时被丢弃
                for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        chunk_len = len(chunk)
                        # 更新全局计数器（必须加锁保证线程安全）
                        with data_lock:
                            total_bytes_downloaded += chunk_len
                            
        except requests.RequestException as e:
            # 吞掉异常并重试，以免一个失败的请求中断整个循环
            # print(f"\n[Error] {url} 下载失败: {e}. 5秒后重试...")
            time.sleep(5) # 发生错误时等待5秒再重试
        except Exception as e:
            # print(f"\n[Critical] {url} 发生未知错误: {e}. 5秒后重试...")
            time.sleep(5)


def monitor_bandwidth():
    """
    一个监控线程，每秒计算并打印一次实时带宽。
    """
    global total_bytes_downloaded
    last_total_bytes = 0
    last_time = time.time()
    
    while True:
        time.sleep(1) # 每秒报告一次
        
        current_time = time.time()
        time_delta = current_time - last_time
        
        if time_delta == 0:
            continue

        # 读取全局计数器（加锁）
        with data_lock:
            current_total_bytes = total_bytes_downloaded
        
        # 计算增量
        bytes_delta = current_total_bytes - last_total_bytes
        
        # 核心计算：
        # (字节 * 8 得到 比特) / (时间差) = bits/sec
        # (bits/sec) / 1,000,000 = Mbps (Megabits per second)
        speed_mbps = (bytes_delta * 8) / (time_delta * 1_000_000)
        
        # (字节) / (时间差) / (1024 * 1024) = MB/s (Megabytes per second)
        speed_mBps = bytes_delta / time_delta / (1024 * 1024)
        
        # 更新状态以便下次计算
        last_total_bytes = current_total_bytes
        last_time = current_time
        
        # \r = 回到行首, end='' = 不换行。实现单行刷新
        sys.stdout.write(f"\r当前带宽: {speed_mbps:,.2f} Mbps  ({speed_mBps:,.2f} MB/s)      ")
        sys.stdout.flush()


# --- 主程序入口 ---
if __name__ == "__main__":
    total_threads = len(URLS)
    print(f"--- 带宽压力测试 ---")
    print(f"总计 {total_threads} 个并发下载线程")
    print("正在启动...\n")

    try:
        # 1. 启动带宽监控线程
        monitor = threading.Thread(target=monitor_bandwidth, daemon=True)
        monitor.start()

        # 2. 为每个URL启动一个文件下载线程
        worker_threads = []
        for url in URLS:
            worker = threading.Thread(target=download_file_worker, args=(url,), daemon=True)
            worker_threads.append(worker)
            worker.start()

        # 3. 保持主线程存活
        #    因为所有工作线程都是守护线程 (daemon=True), 
        #    如果主线程退出，它们将全部被强行终止。
        while True:
            time.sleep(10)
            
    except KeyboardInterrupt:
        print("\n\n测试停止。")
        sys.exit(0)