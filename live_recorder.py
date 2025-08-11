#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站直播录制脚本
支持自动获取直播流并使用ffmpeg录制
支持配置文件管理cookie和直播间设置
"""

import requests
import subprocess
import time
import json
import os
import signal
import sys
import configparser
import threading
from datetime import datetime
from typing import Optional, Dict, Any

class BilibiliLiveRecorder:
    def __init__(self, config_file: str = "config.ini"):
        self.config_file = config_file
        self.config = self.load_config()
        self.room_ids = self.config.get('rooms', 'room_ids', fallback='').split(',')
        self.room_ids = [rid.strip() for rid in self.room_ids if rid.strip()]
        self.output_dir = self.config.get('settings', 'output_dir', fallback='./recordings')
        self.cookies = self.config.get('auth', 'cookies', fallback='')
        self.check_interval = self.config.getint('settings', 'check_interval', fallback=30)
        self.retry_delay = self.config.getint('settings', 'retry_delay', fallback=60)
        self.quality = self.config.getint('settings', 'quality', fallback=25000)
        self.recording_processes = {}
        self.process_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.ffmpeg_path = self.get_ffmpeg_path()
        os.makedirs(self.output_dir, exist_ok=True)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def get_ffmpeg_path(self) -> str:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        local_ffmpeg = os.path.join(script_dir, 'ffmpeg')
        if os.path.isfile(local_ffmpeg) and os.access(local_ffmpeg, os.X_OK):
            print(f"使用本地ffmpeg: {local_ffmpeg}")
            return local_ffmpeg
        try:
            if os.name == 'nt':
                result = subprocess.run(['where', 'ffmpeg'], capture_output=True, text=True, check=True)
            else:
                result = subprocess.run(['which', 'ffmpeg'], capture_output=True, text=True, check=True)
            system_ffmpeg = result.stdout.strip().split('\n')[0]
            print(f"使用系统ffmpeg: {system_ffmpeg}")
            return system_ffmpeg
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("警告: 未找到ffmpeg可执行文件")
            print("请确保:\n1. 将ffmpeg可执行文件放在脚本同目录下，或\n2. 安装ffmpeg并确保在系统PATH中")
            return 'ffmpeg'

    def load_config(self) -> configparser.ConfigParser:
        config = configparser.ConfigParser(interpolation=None)
        if not os.path.exists(self.config_file):
            print(f"配置文件 {self.config_file} 不存在，正在创建默认配置...")
            self.create_default_config()
        config.read(self.config_file, encoding='utf-8')
        return config

    def create_default_config(self):
        config = configparser.ConfigParser(interpolation=None)
        config.add_section('auth'); config.set('auth', 'cookies', '')
        config.add_section('rooms'); config.set('rooms', 'room_ids', '30931147')
        config.add_section('settings'); config.set('settings', 'output_dir', './recordings'); config.set('settings', 'check_interval', '30'); config.set('settings', 'retry_delay', '60'); config.set('settings', 'quality', '25000')
        config.add_section('quality_info'); config.set('quality_info', '# 画质选项说明', '# 30000 - 杜比\n# 25000 - 原画真彩/4K\n# 20000 - 4K\n# 10000 - 原画/1080P高码率\n# 400 - 蓝光/1080P\n# 250 - 超清/720P\n# 150 - 高清\n# 80 - 流畅')
        with open(self.config_file, 'w', encoding='utf-8') as f: config.write(f)
        print(f"已创建默认配置文件: {self.config_file}"); print("请编辑配置文件填入必要信息后重新运行"); sys.exit(0)

    def _signal_handler(self, signum, frame):
        print("\n收到退出信号，正在停止所有录制...")
        self.stop_event.set()
        self.stop_all_recordings()
        sys.exit(0)

    def parse_cookies(self, cookie_string: str) -> Dict[str, str]:
        cookies = {};
        if not cookie_string: return cookies
        for item in cookie_string.split(';'):
            item = item.strip()
            if '=' in item: key, value = item.split('=', 1); cookies[key.strip()] = value.strip()
        return cookies
    
    def get_live_stream_url(self, room_id: str) -> Optional[str]:
        api_url = f"https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
        params = {'room_id': room_id, 'no_playurl': 0, 'mask': 1, 'qn': self.quality, 'platform': 'web', 'protocol': '0,1', 'format': '0,1,2', 'codec': '0,1,2', 'dolby': 5, 'panorama': 1, 'hdr_type': '0,1'}
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36', 'Referer': f'https://live.bilibili.com/{room_id}', 'Origin': 'https://live.bilibili.com'}
        cookies = self.parse_cookies(self.cookies)
        try:
            print(f"正在获取直播间 {room_id} 的流信息...")
            response = requests.get(api_url, params=params, headers=headers, cookies=cookies, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data['code'] != 0: print(f"API返回错误 (房间{room_id}): {data.get('message', '未知错误')}"); return None
            if data['data'].get('live_status', 0) != 1: print(f"直播间 {room_id} 当前未开播"); return None
            playurl_info = data['data'].get('playurl_info')
            if not playurl_info or not playurl_info.get('playurl') or not playurl_info['playurl'].get('stream'): print(f"房间 {room_id}: 未找到流信息"); return None
            stream = playurl_info['playurl']['stream'][1]
            codec_info = stream['format'][0]['codec'][0]
            stream_url = f"{codec_info['url_info'][0]['host']}{codec_info['base_url']}{codec_info['url_info'][0]['extra']}"
            current_qn = codec_info.get('current_qn', 'unknown'); qn_desc = next((q['desc'] for q in playurl_info['playurl'].get('g_qn_desc', []) if q['qn'] == current_qn), str(current_qn))
            print(f"房间 {room_id} 获取到流URL (画质: {qn_desc})")
            return stream_url
        except Exception as e: print(f"房间 {room_id} 网络或解析失败: {e}"); return None

    def _stderr_reader_thread(self, room_id: str, process: subprocess.Popen):
        """
        [MODIFIED] 这是一个专门用于读取并丢弃单个ffmpeg进程的stderr的线程。
        它的唯一目的是防止管道缓冲区被填满导致进程阻塞。
        """
        # 持续从管道中读取数据，但不对其做任何处理（pass）
        for line in process.stderr:
            line.strip()
        # for line in process.stderr:
        #     # 简单打印，可以根据需要进行更复杂的处理，如解析进度
        #     print(f"[ffmpeg][{room_id}]: {line.strip()}", flush=True)
        # 当循环结束（进程退出），这条信息可选，用于确认线程已正常退出
        # print(f"房间 {room_id} 的ffmpeg日志读取线程已正常结束。")





    def start_recording(self, room_id: str, stream_url: str) -> bool:
        print(f"DEBUG: Entering start_recording for room {room_id}", flush=True) # 强制刷新
        with self.process_lock:
            if room_id in self.recording_processes:
                print(f"房间 {room_id} 已经在录制中")
                return False
    
    
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(self.output_dir, f"live_{room_id}_{timestamp}.flv")
    
        
        # 即使不打印，-loglevel quiet 也能减少ffmpeg进程的IO负载，是个好习惯
        ffmpeg_cmd = [
            self.ffmpeg_path, '-hide_banner', 
            '-i', stream_url, 
            '-c', 'copy', 
            '-y', output_file,
        ]
        
        try:
            print(f"开始录制房间 {room_id} 到文件: {output_file}")
            process = subprocess.Popen(
                ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, universal_newlines=True
            )
            
            reader_thread = threading.Thread(
                target=self._stderr_reader_thread, 
                args=(room_id, process), 
                daemon=True
            )
            reader_thread.start()
            
            with self.process_lock:
                self.recording_processes[room_id] = {
                    'process': process, 'output_file': output_file, 'start_time': datetime.now()
                }
            
            print(f"房间 {room_id} 录制已开始 (PID: {process.pid})")
            return True
        except FileNotFoundError:
            print(f"错误: 未找到ffmpeg可执行文件: {self.ffmpeg_path}")
            return False
        except Exception as e:
            print(f"房间 {room_id} 启动录制失败: {e}")
            return False

    def stop_recording(self, room_id: str):
        with self.process_lock:
            if room_id not in self.recording_processes: return
            process_info = self.recording_processes.pop(room_id)
        
        process = process_info['process']
        print(f"正在停止房间 {room_id} 的录制...")
        try:
            process.send_signal(signal.SIGINT)
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print(f"强制终止房间 {room_id} 的录制进程...")
            process.kill(); process.wait()
        except Exception as e: print(f"停止房间 {room_id} 录制时出错: {e}")
        
        duration = datetime.now() - process_info['start_time']
        print(f"房间 {room_id} 录制已停止，持续时间: {duration}")

    def stop_all_recordings(self):
        with self.process_lock:
            room_ids = list(self.recording_processes.keys())
        for room_id in room_ids:
            self.stop_recording(room_id)

    def _status_monitor_thread(self):
        print("录制状态监控线程已启动。")
        while not self.stop_event.is_set():
            with self.process_lock:
                if not self.recording_processes:
                    self.stop_event.wait(1)
                    continue
                room_ids_to_check = list(self.recording_processes.keys())

            for room_id in room_ids_to_check:
                process = None
                with self.process_lock:
                    if room_id in self.recording_processes:
                        process = self.recording_processes[room_id]['process']
                
                if process and process.poll() is not None:
                    print(f"监控线程发现房间 {room_id} 录制已停止 (返回码: {process.returncode})")
                    with self.process_lock:
                        if room_id in self.recording_processes:
                            del self.recording_processes[room_id]
            
            self.stop_event.wait(1)
        print("录制状态监控线程已停止。")

    def get_room_info(self, room_id: str) -> Dict[str, Any]:
        api_url = "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom"
        params = {'room_id': room_id}; headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Referer': f'https://live.bilibili.com/{room_id}'}
        cookies = self.parse_cookies(self.cookies)
        try:
            response = requests.get(api_url, params=params, headers=headers, cookies=cookies, timeout=10); response.raise_for_status(); data = response.json()
            if data['code'] == 0:
                room_info = data['data']['room_info']; anchor_info = data['data']['anchor_info']
                return {'title': room_info.get('title', '未知'), 'uname': anchor_info.get('base_info', {}).get('uname', '未知'), 'live_status': room_info.get('live_status', 0)}
        except Exception as e: print(f"获取房间 {room_id} 信息失败: {e}")
        return {'title': '未知', 'uname': '未知', 'live_status': 0}

    def check_ffmpeg_version(self):
        try:
            result = subprocess.run([self.ffmpeg_path, '-version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0: print(f"ffmpeg版本: {result.stdout.splitlines()[0]}")
            else: print(f"警告: 无法获取ffmpeg版本信息")
        except Exception as e: print(f"警告: 检查ffmpeg版本时出错: {e}")
        print()

    def run(self):
        if not self.room_ids:
            print("错误: 配置文件中未设置直播间号"); return
        
        self.check_ffmpeg_version()
        monitor = threading.Thread(target=self._status_monitor_thread, daemon=True); monitor.start()
        
        print(f"开始监控 {len(self.room_ids)} 个直播间...")
        
        while not self.stop_event.is_set():
            try:
                for room_id in self.room_ids:
                    with self.process_lock:
                        is_recording = room_id in self.recording_processes
                    if not is_recording:
                        stream_url = self.get_live_stream_url(room_id)
                        if stream_url:
                            self.start_recording(room_id, stream_url)
                
                self.stop_event.wait(self.check_interval)
            except Exception as e:
                print(f"主循环运行时出错: {e}")
                self.stop_event.wait(self.retry_delay)
        
        print("主循环已退出，等待所有进程结束...")
        self.stop_all_recordings()
        monitor.join(timeout=5)
        print("录制器已停止")

def main():
    import argparse
    parser = argparse.ArgumentParser(description='B站直播录制工具')
    parser.add_argument('-c', '--config', default='config.ini', help='配置文件路径 (默认: config.ini)')
    args = parser.parse_args()
    recorder = BilibiliLiveRecorder(args.config)
    recorder.run()

if __name__ == '__main__':
    main()
