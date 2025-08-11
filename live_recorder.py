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
from datetime import datetime
from typing import Optional, Dict, Any, List


class BilibiliLiveRecorder:
    def __init__(self, config_file: str = "config.ini"):
        """
        初始化录播器
        
        Args:
            config_file: 配置文件路径
        """
        self.config_file = config_file
        self.config = self.load_config()
        
        # 从配置文件读取设置
        self.room_ids = self.config.get('rooms', 'room_ids', fallback='').split(',')
        self.room_ids = [rid.strip() for rid in self.room_ids if rid.strip()]
        
        self.output_dir = self.config.get('settings', 'output_dir', fallback='./recordings')
        self.cookies = self.config.get('auth', 'cookies', fallback='')
        
        # 录制相关设置
        self.check_interval = self.config.getint('settings', 'check_interval', fallback=30)
        self.retry_delay = self.config.getint('settings', 'retry_delay', fallback=60)
        self.quality = self.config.getint('settings', 'quality', fallback=25000)
        
        # 活动录制进程
        self.recording_processes = {}  # room_id -> process info
        
        # 设置ffmpeg路径
        self.ffmpeg_path = self.get_ffmpeg_path()
        
        # 创建输出目录
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 设置信号处理器
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def get_ffmpeg_path(self) -> str:
        """
        获取ffmpeg可执行文件路径
        优先使用同目录下的ffmpeg，然后使用系统PATH中的ffmpeg
        
        Returns:
            ffmpeg可执行文件路径
        """
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
        # 根据操作系统确定可执行文件名
        if os.name == 'nt':  # Windows
            local_ffmpeg = os.path.join(script_dir, 'ffmpeg.exe')
        else:  # Linux/macOS
            local_ffmpeg = os.path.join(script_dir, 'ffmpeg')
        
        # 检查同目录下是否有ffmpeg
        if os.path.isfile(local_ffmpeg) and os.access(local_ffmpeg, os.X_OK):
            print(f"使用本地ffmpeg: {local_ffmpeg}")
            return local_ffmpeg
        
        # 检查系统PATH中是否有ffmpeg
        try:
            # 使用which命令（Linux/macOS）或where命令（Windows）查找ffmpeg
            if os.name == 'nt':
                result = subprocess.run(['where', 'ffmpeg'], capture_output=True, text=True)
            else:
                result = subprocess.run(['which', 'ffmpeg'], capture_output=True, text=True)
            
            if result.returncode == 0 and result.stdout.strip():
                system_ffmpeg = result.stdout.strip().split('\n')[0]  # 取第一个结果
                print(f"使用系统ffmpeg: {system_ffmpeg}")
                return system_ffmpeg
        except Exception as e:
            print(f"检查系统ffmpeg时出错: {e}")
        
        # 如果都找不到，返回默认的ffmpeg（让系统报错）
        print("警告: 未找到ffmpeg可执行文件")
        print("请确保:")
        print("1. 将ffmpeg可执行文件放在脚本同目录下，或")
        print("2. 安装ffmpeg并确保在系统PATH中")
        return 'ffmpeg'
    
    def load_config(self) -> configparser.ConfigParser:
        """加载配置文件"""
        # 禁用插值功能，避免cookie中的%符号被误解析
        config = configparser.ConfigParser(interpolation=None)
        
        if not os.path.exists(self.config_file):
            print(f"配置文件 {self.config_file} 不存在，正在创建默认配置...")
            self.create_default_config()
        
        config.read(self.config_file, encoding='utf-8')
        return config
    
    def create_default_config(self):
        """创建默认配置文件"""
        # 同样禁用插值功能
        config = configparser.ConfigParser(interpolation=None)
        
        # 认证配置
        config.add_section('auth')
        config.set('auth', 'cookies', '')
        
        # 直播间配置
        config.add_section('rooms')
        config.set('rooms', 'room_ids', '30931147')
        
        # 设置配置
        config.add_section('settings')
        config.set('settings', 'output_dir', './recordings')
        config.set('settings', 'check_interval', '30')
        config.set('settings', 'retry_delay', '60')
        config.set('settings', 'quality', '25000')
        
        # 质量说明
        config.add_section('quality_info')
        config.set('quality_info', '# 画质选项说明')
        config.set('quality_info', '# 30000 - 杜比')
        config.set('quality_info', '# 25000 - 原画真彩/4K')
        config.set('quality_info', '# 20000 - 4K')
        config.set('quality_info', '# 10000 - 原画/1080P高码率')
        config.set('quality_info', '# 400 - 蓝光/1080P')
        config.set('quality_info', '# 250 - 超清/720P')
        config.set('quality_info', '# 150 - 高清')
        config.set('quality_info', '# 80 - 流畅')
        
        with open(self.config_file, 'w', encoding='utf-8') as f:
            config.write(f)
        
        print(f"已创建默认配置文件: {self.config_file}")
        print("请编辑配置文件填入必要信息后重新运行")
        print("\n重要提示:")
        print("1. 在 [auth] 部分填入B站cookies（用于获取高画质流）")
        print("2. 在 [rooms] 部分填入要录制的直播间号，多个用逗号分隔")
        print("3. 可选：调整 [settings] 部分的其他参数")
        print("\n注意：cookies中包含%符号是正常的，无需转义")
        sys.exit(0)
    
    def _signal_handler(self, signum, frame):
        """处理中断信号"""
        print("\n收到退出信号，正在停止所有录制...")
        self.stop_all_recordings()
        sys.exit(0)
    
    def parse_cookies(self, cookie_string: str) -> Dict[str, str]:
        """解析cookie字符串"""
        cookies = {}
        if not cookie_string:
            return cookies
        
        for item in cookie_string.split(';'):
            item = item.strip()
            if '=' in item:
                key, value = item.split('=', 1)
                cookies[key.strip()] = value.strip()
        
        return cookies
    
    def get_live_stream_url(self, room_id: str) -> Optional[str]:
        """
        获取直播流URL
        
        Args:
            room_id: 直播间ID
            
        Returns:
            直播流URL，如果获取失败返回None
        """
        api_url = f"https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
        params = {
            'room_id': room_id,
            'no_playurl': 0,
            'mask': 1,
            'qn': self.quality,
            'platform': 'web',
            'protocol': '0,1',
            'format': '0,1,2',
            'codec': '0,1,2',
            'dolby': 5,
            'panorama': 1,
            'hdr_type': '0,1'
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': f'https://live.bilibili.com/{room_id}',
            'Origin': 'https://live.bilibili.com'
        }
        
        # 添加cookies
        cookies = self.parse_cookies(self.cookies)
        
        try:
            print(f"正在获取直播间 {room_id} 的流信息...")
            response = requests.get(
                api_url, 
                params=params, 
                headers=headers, 
                cookies=cookies,
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            
            if data['code'] != 0:
                print(f"API返回错误 (房间{room_id}): {data.get('message', '未知错误')}")
                return None
            
            # 检查直播状态
            live_status = data['data'].get('live_status', 0)
            if live_status != 1:
                print(f"直播间 {room_id} 当前未开播 (状态: {live_status})")
                return None
            
            # 解析流URL
            playurl_info = data['data'].get('playurl_info')
            if not playurl_info:
                print(f"房间 {room_id}: 未找到播放信息")
                return None
            
            playurl = playurl_info.get('playurl')
            if not playurl or not playurl.get('stream'):
                print(f"房间 {room_id}: 未找到流信息")
                return None
            
            # 获取第一个可用的流
            stream = playurl['stream'][0]
            format_info = stream['format'][0]
            codec_info = format_info['codec'][0]
            
            # 拼接完整的流URL
            host = codec_info['url_info'][0]['host']
            base_url = codec_info['base_url']
            extra = codec_info['url_info'][0]['extra']
            
            stream_url = f"{host}{base_url}{extra}"
            
            # 获取画质信息
            current_qn = codec_info.get('current_qn', 'unknown')
            qn_desc = next((q['desc'] for q in playurl.get('g_qn_desc', []) if q['qn'] == current_qn), str(current_qn))
            
            print(f"房间 {room_id} 获取到流URL (画质: {qn_desc}): {stream_url}")
            return stream_url
            
        except requests.RequestException as e:
            print(f"房间 {room_id} 网络请求失败: {e}")
            return None
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            print(f"房间 {room_id} 解析响应数据失败: {e}")
            return None
    
    def start_recording(self, room_id: str, stream_url: str) -> bool:
        """
        开始录制
        
        Args:
            room_id: 直播间ID
            stream_url: 直播流URL
            
        Returns:
            是否成功启动录制
        """
        if room_id in self.recording_processes:
            print(f"房间 {room_id} 已经在录制中")
            return False
        
        # 生成输出文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(self.output_dir, f"live_{room_id}_{timestamp}.flv")
        
        # ffmpeg命令
        ffmpeg_cmd = [
            self.ffmpeg_path,  # 使用检测到的ffmpeg路径
            '-i', stream_url,
            '-c', 'copy',  # 直接复制流，不重新编码
            '-f', 'flv',   # 输出格式为flv
            '-y',          # 覆盖输出文件
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '30',
            output_file
        ]
        
        try:
            print(f"开始录制房间 {room_id} 到文件: {output_file}")
            
            # 启动ffmpeg进程
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            self.recording_processes[room_id] = {
                'process': process,
                'output_file': output_file,
                'start_time': datetime.now()
            }
            
            print(f"房间 {room_id} 录制已开始 (PID: {process.pid})")
            return True
            
        except FileNotFoundError:
            print(f"错误: 未找到ffmpeg可执行文件: {self.ffmpeg_path}")
            print("请确保ffmpeg已正确安装或放置在脚本同目录下")
            return False
        except Exception as e:
            print(f"房间 {room_id} 启动录制失败: {e}")
            return False
    
    def stop_recording(self, room_id: str):
        """停止指定房间的录制"""
        if room_id not in self.recording_processes:
            return
        
        process_info = self.recording_processes[room_id]
        process = process_info['process']
        
        print(f"正在停止房间 {room_id} 的录制...")
        
        try:
            # 发送SIGINT信号给ffmpeg进程
            process.send_signal(signal.SIGINT)
            
            # 等待进程结束
            process.wait(timeout=10)
            
        except subprocess.TimeoutExpired:
            print(f"强制终止房间 {room_id} 的录制进程...")
            process.kill()
            process.wait()
        except Exception as e:
            print(f"停止房间 {room_id} 录制时出错: {e}")
        
        # 计算录制时长
        duration = datetime.now() - process_info['start_time']
        print(f"房间 {room_id} 录制已停止，持续时间: {duration}")
        
        del self.recording_processes[room_id]
    
    def stop_all_recordings(self):
        """停止所有录制"""
        room_ids = list(self.recording_processes.keys())
        for room_id in room_ids:
            self.stop_recording(room_id)
    
    def check_recording_status(self, room_id: str) -> bool:
        """
        检查指定房间的录制状态
        
        Args:
            room_id: 直播间ID
            
        Returns:
            录制是否正在进行
        """
        if room_id not in self.recording_processes:
            return False
        
        process_info = self.recording_processes[room_id]
        process = process_info['process']
        
        # 检查进程是否还在运行
        poll = process.poll()
        if poll is not None:
            # 进程已结束
            print(f"房间 {room_id} 录制进程已结束 (返回码: {poll})")
            
            # 打印stderr信息
            if process.stderr:
                stderr_output = process.stderr.read()
                if stderr_output:
                    print(f"房间 {room_id} ffmpeg错误信息: {stderr_output}")
            
            del self.recording_processes[room_id]
            return False
        
        return True
    
    def get_room_info(self, room_id: str) -> Dict[str, Any]:
        """获取房间信息"""
        api_url = "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom"
        params = {'room_id': room_id}
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': f'https://live.bilibili.com/{room_id}'
        }
        cookies = self.parse_cookies(self.cookies)
        
        try:
            response = requests.get(api_url, params=params, headers=headers, cookies=cookies, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data['code'] == 0:
                room_info = data['data']['room_info']
                anchor_info = data['data']['anchor_info']
                return {
                    'title': room_info.get('title', '未知'),
                    'uname': anchor_info.get('base_info', {}).get('uname', '未知'),
                    'live_status': room_info.get('live_status', 0)
                }
        except Exception as e:
            print(f"获取房间 {room_id} 信息失败: {e}")
        
        return {'title': '未知', 'uname': '未知', 'live_status': 0}
    
    def check_ffmpeg_version(self):
        """检查ffmpeg版本"""
        try:
            result = subprocess.run(
                [self.ffmpeg_path, '-version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # 提取版本信息
                first_line = result.stdout.split('\n')[0]
                print(f"ffmpeg版本: {first_line}")
            else:
                print(f"警告: 无法获取ffmpeg版本信息")
        except subprocess.TimeoutExpired:
            print("警告: ffmpeg版本检查超时")
        except Exception as e:
            print(f"警告: 检查ffmpeg版本时出错: {e}")
        print()
    
    def run(self):
        """运行录制循环"""
        if not self.room_ids:
            print("错误: 配置文件中未设置直播间号")
            return
        
        # 检查ffmpeg版本
        self.check_ffmpeg_version()
        
        print(f"开始监控 {len(self.room_ids)} 个直播间")
        print(f"录制文件将保存到: {os.path.abspath(self.output_dir)}")
        print(f"检查间隔: {self.check_interval}秒")
        print(f"重试延迟: {self.retry_delay}秒")
        print("按Ctrl+C停止录制\n")
        
        # 显示房间信息
        for room_id in self.room_ids:
            info = self.get_room_info(room_id)
            status_text = "直播中" if info['live_status'] == 1 else "未开播"
            print(f"房间 {room_id}: {info['uname']} - {info['title']} [{status_text}]")
        print()
        
        while True:
            try:
                # 检查每个房间
                for room_id in self.room_ids:
                    # 如果当前没在录制该房间，尝试获取流URL并开始录制
                    if room_id not in self.recording_processes:
                        stream_url = self.get_live_stream_url(room_id)
                        if stream_url:
                            self.start_recording(room_id, stream_url)
                    else:
                        # 检查录制状态
                        if not self.check_recording_status(room_id):
                            print(f"房间 {room_id} 录制已停止，准备重新开始...")
                
                # 显示当前录制状态
                if self.recording_processes:
                    print(f"当前录制中的房间: {', '.join(self.recording_processes.keys())}")
                
                # 等待下次检查
                time.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"运行时出错: {e}")
                time.sleep(self.retry_delay)
        
        # 清理
        self.stop_all_recordings()
        print("录制器已停止")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='B站直播录制工具')
    parser.add_argument('-c', '--config', default='config.ini',
                       help='配置文件路径 (默认: config.ini)')
    parser.add_argument('--show-config', action='store_true',
                       help='显示当前配置')
    
    args = parser.parse_args()
    
    if args.show_config:
        if os.path.exists(args.config):
            with open(args.config, 'r', encoding='utf-8') as f:
                print(f"配置文件 {args.config} 内容:")
                print(f.read())
        else:
            print(f"配置文件 {args.config} 不存在")
        return
    
    # 创建录制器
    recorder = BilibiliLiveRecorder(args.config)
    
    # 开始录制
    recorder.run()


if __name__ == '__main__':
    main()
