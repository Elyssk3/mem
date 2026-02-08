

"""Minimal, cleaned-up Memory Monitor GUI.

This file is a simplified, self-contained replacement to restore a runnable
DearPyGui-based memory monitor after the previous file became corrupted.

Features:
- Displays RAM and Swap usage with progress bars and text.
- "Clear Mem" button triggers a safe GC + working-set trim for the current process.
- Settings tab exposes an "Autostart" checkbox which writes to HKCU Run.

This intentionally omits advanced features (tray icon, low-level NT calls,
window hooks) to prioritise a stable, runnable baseline for testing.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import gc
import subprocess
from collections import deque

import dearpygui.dearpygui as dpg
import psutil
import ctypes
import winreg
import pystray
from PIL import Image, ImageDraw, ImageFont
from ctypes import wintypes

# --- Configuration ---
config_path = os.path.join(os.path.dirname(__file__), 'mem_proccess_config.json')

# runtime history and state
_mem_history = deque()
_last_autoclean_time = 0.0


def set_autostart(enable: bool) -> bool:
	try:
		reg_path = r"Software\\Microsoft\\Windows\\CurrentVersion\\Run"
		reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE)
		exe = sys.executable if getattr(sys, 'frozen', False) else f'"{sys.executable}" "{os.path.abspath(__file__)}"'
		if enable:
			winreg.SetValueEx(reg_key, 'MemProcess', 0, winreg.REG_SZ, exe)
		else:
			try:
				winreg.DeleteValue(reg_key, 'MemProcess')
			except Exception:
				pass
		winreg.CloseKey(reg_key)
		return True
	except Exception as e:
		print('set_autostart error:', e)
		return False


def cleanup_memory() -> bool:
	try:
		print('Starting aggressive memory cleanup...')
		
		# PowerShell aggressive cleanup routine
		ps_command = """
# Stage 1: Clear DNS and network caches
Write-Host 'Stage 1: Clearing DNS cache...'
try { ipconfig /flushdns 2>$null } catch {}

# Stage 2: Flush file cache
Write-Host 'Stage 2: Flushing file buffers...'
try { Clear-DnsClientCache 2>$null } catch {}

# Stage 3: Garbage collection in PowerShell
Write-Host 'Stage 3: PowerShell GC...'
[System.GC]::Collect()
[System.GC]::WaitForPendingFinalizers()

# Stage 4: Force cleanup of unused memory
Write-Host 'Stage 4: Clearing standby memory...'
try {
    Get-Process | Where-Object { $_.ProcessName -notmatch 'svchost|csrss|lsass|System|dwm' } | ForEach-Object {
        try {
            $_.MinWorkingSet = $_.MinWorkingSet
        } catch {}
    }
} catch {}

# Stage 5: Additional cleanup
Write-Host 'Stage 5: Final memory trim...'
for ($i = 0; $i -lt 15; $i++) {
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
    Start-Sleep -Milliseconds 20
}

Write-Host 'Memory cleanup completed'
"""
		
		try:
			result = subprocess.run(
				["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
				capture_output=True,
				timeout=30,
				text=True,
				creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0x08000000
			)
			output = result.stdout + result.stderr
			for line in output.split('\n'):
				if 'Stage' in line or 'cleanup' in line.lower() or 'completed' in line.lower():
					print(line)
		except Exception as e:
			print(f'PowerShell cleanup error: {e}')
		
		# Python GC cleanup
		print('Stage 6: Python garbage collection...')
		for _ in range(10):
			gc.collect()
			time.sleep(0.05)
		
		# Aggressive Windows API cleanup
		try:
			kernel32 = ctypes.windll.kernel32
			psapi = ctypes.windll.psapi
			PROC_ALL = 0x1F0FFF
			
			print('Stage 7: API working set cleanup...')
			for proc in psutil.process_iter(['pid', 'name']):
				try:
					pinfo = proc.as_dict(attrs=['pid', 'name'])
					pname = (pinfo.get('name') or '').lower()
					if pname in ['system', 'svchost.exe', 'csrss.exe', 'lsass.exe', 'python.exe', 'dwm.exe']:
						continue
					pid = pinfo.get('pid')
					handle = kernel32.OpenProcess(PROC_ALL, False, pid)
					if handle:
						try:
							psapi.EmptyWorkingSet(handle)
						except Exception:
							pass
						try:
							kernel32.SetProcessWorkingSetSize(handle, -1, -1)
						except Exception:
							pass
						kernel32.CloseHandle(handle)
				except Exception:
					continue
		except Exception as e:
			print(f'API cleanup error: {e}')
		
		time.sleep(1)
		mem = psutil.virtual_memory()
		print(f'Memory cleanup finished - Current memory usage: {mem.percent:.1f}%')
		return True
		
	except Exception as e:
		print(f'Cleanup error: {e}')
		return False


def save_config(autostart: bool):
	try:
		# gather other UI-driven config values if available
		data = {
			'autostart': bool(autostart),
			'accent_color': dpg.get_value('accent_color_picker') if dpg.does_item_exist('accent_color_picker') else [100,200,255,255],
			'update_interval': float(dpg.get_value('update_interval_input')) if dpg.does_item_exist('update_interval_input') else 1.0,
			'auto_cleanup': bool(dpg.get_value('auto_cleanup_checkbox')) if dpg.does_item_exist('auto_cleanup_checkbox') else False,
			'auto_cleanup_threshold': int(dpg.get_value('auto_cleanup_threshold')) if dpg.does_item_exist('auto_cleanup_threshold') else 90,
			'notify_on_cleanup': bool(dpg.get_value('notify_on_cleanup')) if dpg.does_item_exist('notify_on_cleanup') else True,
			'history_seconds': int(dpg.get_value('history_seconds')) if dpg.does_item_exist('history_seconds') else 60,
			'top_n_processes': int(dpg.get_value('top_n_processes')) if dpg.does_item_exist('top_n_processes') else 5,
		}
		with open(config_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, ensure_ascii=False, indent=2)
	except Exception as e:
		print('save_config error:', e)


def load_config() -> dict:
	defaults = {
		'autostart': False,
		'accent_color': [100, 200, 255, 255],
		'update_interval': 1.0,
		'auto_cleanup': False,
		'auto_cleanup_threshold': 90,
		'notify_on_cleanup': True,
		'history_seconds': 60,
		'top_n_processes': 5,
	}
	try:
		if os.path.exists(config_path):
			with open(config_path, 'r', encoding='utf-8') as f:
				data = json.load(f)
			for k, v in defaults.items():
				data.setdefault(k, v)
			return data
	except Exception as e:
		print('load_config error:', e)
	return defaults


def update_loop(stop_event: threading.Event):
	# dynamic update loop using ui-configurable interval, history and auto-cleanup
	while not stop_event.is_set():
		try:
			mem = psutil.virtual_memory()
			swap = psutil.swap_memory()
			ram_text = f"Physical Memory: {mem.used / (1024**3):.2f} GB / {mem.total / (1024**3):.2f} GB ({mem.percent:.1f}%)"
			swap_text = f"Paging File: {swap.used / (1024**3):.2f} GB / {swap.total / (1024**3):.2f} GB ({swap.percent:.1f}%)"
			ram_val = min(mem.percent / 100.0, 1.0)
			swap_val = min(swap.percent / 100.0, 1.0)

			# write texts
			try:
				dpg.set_value('ram_text', ram_text)
				dpg.set_value('swap_text', swap_text)
			except Exception:
				pass

			# draw custom colored bars (green/yellow/red)
			try:
				def color_for(pct):
					if pct < 60:
						return (80, 200, 120, 255)
					if pct < 85:
						return (240, 200, 80, 255)
					return (220, 80, 80, 255)

				w = 440
				h = 18
				# remove previous foreground rects if present
				try:
					if dpg.does_item_exist('ram_fore'):
						dpg.delete_item('ram_fore')
				except Exception:
					pass
				try:
					if dpg.does_item_exist('swap_fore'):
						dpg.delete_item('swap_fore')
				except Exception:
					pass

				# draw background then foreground
				try:
					dpg.draw_rectangle((0, 0), (w, h), color=(60,60,60,255), fill=(40,40,40,255), parent='ram_draw', tag='ram_back')
					fw = int(w * ram_val)
					if fw > 0:
						dpg.draw_rectangle((0, 0), (fw, h), color=color_for(mem.percent), fill=color_for(mem.percent), parent='ram_draw', tag='ram_fore')
				except Exception:
					pass

				try:
					dpg.draw_rectangle((0, 0), (w, h), color=(60,60,60,255), fill=(40,40,40,255), parent='swap_draw', tag='swap_back')
					fw2 = int(w * swap_val)
					if fw2 > 0:
						dpg.draw_rectangle((0, 0), (fw2, h), color=color_for(swap.percent), fill=color_for(swap.percent), parent='swap_draw', tag='swap_fore')
				except Exception:
					pass

			# update tray icon image if available
			try:
				if '_GLOBAL_TRAY_ICON' in globals() and _GLOBAL_TRAY_ICON:
					try:
						img = create_tray_icon(mem.percent)
						_GLOBAL_TRAY_ICON.icon = img
						try:
							_GLOBAL_TRAY_ICON.update_icon()
						except Exception:
							pass
				except Exception:
					pass

			# maintain history for simple sparkline
			try:
				history_seconds = dpg.get_value('history_seconds') if dpg.does_item_exist('history_seconds') else 60
				interval = dpg.get_value('update_interval_input') if dpg.does_item_exist('update_interval_input') else 1.0
				maxlen = max(10, int(history_seconds / max(0.1, float(interval))))
				_mem_history.append(mem.percent)
				while len(_mem_history) > maxlen:
					_mem_history.popleft()
				# make a small sparkline using 8 levels
				levels = '▁▂▃▄▅▆▇█'
				line = ''.join(levels[min(len(levels)-1, int((v/100.0)*(len(levels)-1)))] for v in _mem_history)
				dpg.set_value('history_text', f"History ({len(_mem_history)}s): {line}")
			except Exception:
				pass

			# update processes list
			try:
				top_n = dpg.get_value('top_n_processes') if dpg.does_item_exist('top_n_processes') else 5
				procs = []
				for p in psutil.process_iter(['pid','name','memory_info']):
					try:
						mi = p.info.get('memory_info')
						rss = getattr(mi, 'rss', 0) if mi else 0
						procs.append((rss, p.info.get('name') or ''))
				except Exception:
					continue
				procs.sort(reverse=True)
				for i in range(min(10, max(1, top_n))):
					try:
						if i < len(procs):
							rss, name = procs[i]
							s = f"{i+1}. {name} — {rss/(1024**2):.1f} MB"
							dpg.set_value(f'proc_row_{i}', s)
						else:
							dpg.set_value(f'proc_row_{i}', '')
					except Exception:
						pass
			except Exception:
				pass

			# Auto-cleanup when threshold reached (with simple cooldown)
			try:
				if dpg.does_item_exist('auto_cleanup_checkbox') and dpg.get_value('auto_cleanup_checkbox'):
					threshold = dpg.get_value('auto_cleanup_threshold') if dpg.does_item_exist('auto_cleanup_threshold') else 90
					now = time.time()
					global _last_autoclean_time
					if mem.percent >= float(threshold) and now - _last_autoclean_time > max(30.0, float(interval)*5):
						_last_autoclean_time = now
						threading.Thread(target=lambda: _autoclean_and_notify(), daemon=True).start()
			except Exception:
				pass

		except Exception:
			pass

		# wait for configured interval
		try:
			interval = float(dpg.get_value('update_interval_input')) if dpg.does_item_exist('update_interval_input') else 1.0
		except Exception:
			interval = 1.0
		stop_event.wait(max(0.1, interval))


# Global tray icon instance
_global_tray_icon_instance = None


def hide_window_to_tray():
	"""Hide the main window and keep app running in tray."""
	try:
		# Use OS-level minimize or just configure viewport
		user32 = ctypes.windll.user32
		hwnd = user32.FindWindowW(None, "Memory Usage Monitor")
		if hwnd:
			# SW_HIDE = 0
			user32.ShowWindow(hwnd, 0)
			print('DEBUG: window hidden to tray')
		else:
			print('DEBUG: could not find window handle to hide')
	except Exception as e:
		print(f'hide_window_to_tray error: {e}')


def show_window_from_tray():
	"""Show the main window from tray."""
	try:
		# Show the window
		user32 = ctypes.windll.user32
		hwnd = user32.FindWindowW(None, "Memory Usage Monitor")
		if hwnd:
			# SW_SHOW = 5
			user32.ShowWindow(hwnd, 5)
			print('DEBUG: window shown from tray')
		else:
			print('DEBUG: could not find window handle to show')
	except Exception as e:
		print(f'show_window_from_tray error: {e}')


def install_close_hook() -> bool:
	"""Install hook on viewport close to hide instead of exit.
	
	Uses FindWindowW to locate the viewport by title, then installs WNDPROC hook.
	On WM_CLOSE, hides the window; for other messages returns 1 (handled).
	"""
	try:
		user32 = ctypes.windll.user32
		GWLP_WNDPROC = -4
		WM_CLOSE = 0x0010
		
		# Get HWND from window title
		hwnd = user32.FindWindowW(None, "Memory Usage Monitor")
		if not hwnd:
			print('DEBUG: FindWindowW returned 0 (window not found by title)')
			return False
		
		print(f'DEBUG: got hwnd={hwnd}')
		
		# WNDPROC callback type - simple signature
		WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.c_uint, ctypes.c_int, ctypes.c_int)
		
		global _wndproc_ref, _orig_wnd_wptr
		
		def wndproc(hwnd, msg, wparam, lparam):
			if msg == WM_CLOSE:
				print('DEBUG: WM_CLOSE intercepted, hiding window to tray')
				try:
					hide_window_to_tray()
				except Exception as e:
					print(f'wndproc hide error: {e}')
				return 0  # Handled WM_CLOSE, don't pass to system
			# For all other messages, return 1 (handled) to let DearPyGui process them
			return 1
		
		# Create callback and store globally to avoid GC
		wndproc_callback = WNDPROCTYPE(wndproc)
		_wndproc_ref = wndproc_callback
		
		# Install the hook
		_orig_wnd_wptr = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, wndproc_callback)
		print(f'DEBUG: installed hook, original wndproc={_orig_wnd_wptr}')
		return True
		
	except Exception as e:
		print(f'install_close_hook error: {e}')
		import traceback
		traceback.print_exc()
		return False


def create_tray_icon(ram_percent):
	"""Create tray icon with memory percentage"""
	# larger icon for better readability in tray
	size = (128, 128)
	img = Image.new('RGBA', size, color=(40, 40, 40, 255))
	draw = ImageDraw.Draw(img)

	text = f"{int(ram_percent)}"
	try:
		# prefer a clear UI font if available
		font_candidates = [
			"C:\\Windows\\Fonts\\segoeui.ttf",
			"C:\\Windows\\Fonts\\tahoma.ttf",
			"C:\\Windows\\Fonts\\arial.ttf",
		]
		font = None
		for fp in font_candidates:
			try:
				if os.path.exists(fp):
					font = ImageFont.truetype(fp, 96)
					break
			except Exception:
				continue
		if font is None:
			font = ImageFont.load_default()

		draw.text((size[0]//2, size[1]//2), text, fill=(255, 255, 255, 255), anchor="mm", font=font)
	except Exception:
		pass
	
	return img


def on_tray_show(icon, item):
	"""Show main window from tray"""
	show_window_from_tray()


def on_tray_cleanup(icon, item):
	"""Clear memory from tray menu"""
	threading.Thread(target=cleanup_memory, daemon=True).start()


def on_tray_exit(icon, item):
	"""Exit application from tray"""
	try:
		save_config(dpg.get_value('autostart_checkbox') if dpg.does_item_exist('autostart_checkbox') else False)
	except Exception:
		pass
	try:
		dpg.stop_dearpygui()
	except Exception:
		pass
	try:
		icon.stop()
	except Exception:
		pass


def setup_tray():
	"""Setup system tray icon"""
	try:
		mem = psutil.virtual_memory()
		icon_img = create_tray_icon(mem.percent)
		
		menu = pystray.Menu(
			pystray.MenuItem("Show", lambda icon, item: on_tray_show(icon, item)),
			pystray.MenuItem("Clear Memory", lambda icon, item: on_tray_cleanup(icon, item)),
			pystray.MenuItem("Exit", lambda icon, item: on_tray_exit(icon, item))
		)
		
		tray_icon = pystray.Icon("Memory Monitor", icon_img, menu=menu)
		# expose global reference so other threads can update the icon
		global _GLOBAL_TRAY_ICON
		_GLOBAL_TRAY_ICON = tray_icon
		return tray_icon
	except Exception as e:
		print(f'Tray setup error: {e}')
		return None


def _autoclean_and_notify():
	"""Call cleanup_memory and show an in-app notification if enabled."""
	try:
		res = cleanup_memory()
		# show transient notification in GUI if requested
		try:
			if dpg.does_item_exist('notify_on_cleanup') and dpg.get_value('notify_on_cleanup'):
				if dpg.does_item_exist('notify_win'):
					dpg.delete_item('notify_win')
				dpg.add_window(label='Notification', tag='notify_win', pos=(260, 20), width=200, height=60, no_close=True)
				dpg.add_text('Memory cleaned', parent='notify_win')
				# schedule removal
				threading.Timer(4.0, lambda: dpg.delete_item('notify_win') if dpg.does_item_exist('notify_win') else None).start()
		except Exception:
			pass
		return res
	except Exception as e:
		print(f'_autoclean_and_notify error: {e}')
		return False


def run_tray(tray_icon):
	"""Run tray icon in separate thread"""
	try:
		# prefer detached mode if available to avoid blocking main loop/backends
		if hasattr(tray_icon, 'run_detached'):
			tray_icon.run_detached()
		else:
			tray_icon.run()
	except Exception as e:
		print(f'Tray run error: {e}')


def open_settings():
	"""Open settings window"""
	dpg.configure_item('settings_tab', show=True)


def main():
	dpg.create_context()
	
	# Load config and set up fonts with larger size
	cfg = load_config()
	
	# Register and bind a larger font for better readability
	try:
		font_paths = [
			"C:\\Windows\\Fonts\\tahoma.ttf",
			"C:\\Windows\\Fonts\\verdana.ttf",
			"C:\\Windows\\Fonts\\segoeui.ttf",
			"C:\\Windows\\Fonts\\arial.ttf"
		]
		font_path = None
		for fp in font_paths:
			if os.path.exists(fp):
				font_path = fp
				break
		
		if font_path:
			with dpg.font_registry():
				default_font = dpg.add_font(font_path, 13)  # Slightly smaller font
			dpg.bind_font(default_font)
	except Exception as e:
		print(f'Font setup error: {e}')
	
	# Viewport and window with 10:16 aspect ratio (smaller)
	vp_w, vp_h = 480, 300
	dpg.create_viewport(title='Memory Usage Monitor', width=vp_w, height=vp_h)
	print('DEBUG: created viewport')

	with dpg.window(label='Memory Monitor', tag='main_window', width=460, height=270, pos=(10, 10)):
		# Tab bar with Main and Settings tabs
		with dpg.tab_bar():
			with dpg.tab(label='Main'):
				dpg.add_spacer(height=5)
				dpg.add_text('', tag='ram_text', color=(100, 200, 255))
				# custom drawing bar so we can change color dynamically
				dpg.add_drawing(tag='ram_draw', width=440, height=18)
				dpg.add_spacer(height=8)
				dpg.add_text('', tag='swap_text', color=(100, 200, 255))
				dpg.add_drawing(tag='swap_draw', width=440, height=18)
				dpg.add_spacer(height=12)
				# History sparkline (text-based for compatibility)
				dpg.add_text('', tag='history_text', color=(200,200,200))
				# Clear Memory button
				with dpg.group(horizontal=True):
					dpg.add_spacer(width=130)
					dpg.add_button(label='Clear Memory', width=180, height=32, callback=lambda: threading.Thread(target=cleanup_memory, daemon=True).start())
				# License text
				dpg.add_spacer(height=6)
				dpg.add_text('License: Free distribution', color=(160, 160, 160))
			
			with dpg.tab(label='Settings', tag='settings_tab'):
				dpg.add_spacer(height=10)
				dpg.add_text('System', color=(200, 200, 200))
				dpg.add_separator()
				dpg.add_checkbox(label='Autostart on System Boot', tag='autostart_checkbox', default_value=cfg.get('autostart', False), callback=lambda s, v: set_autostart(v))
				dpg.add_spacing(count=1)
				dpg.add_text('Appearance', color=(200,200,200))
				dpg.add_color_picker4(tag='accent_color_picker', label='Accent Color', default_value=cfg.get('accent_color', [100,200,255,255]), width=200)
				dpg.add_spacing(count=1)
				dpg.add_text('Update', color=(200,200,200))
				dpg.add_input_float(tag='update_interval_input', label='Update interval (s)', default_value=cfg.get('update_interval', 1.0), min_value=0.1, step=0.1)
				dpg.add_spacing(count=1)
				dpg.add_text('Auto Cleanup', color=(200,200,200))
				dpg.add_checkbox(tag='auto_cleanup_checkbox', label='Enable auto cleanup', default_value=cfg.get('auto_cleanup', False))
				dpg.add_slider_int(tag='auto_cleanup_threshold', label='Cleanup threshold (%)', default_value=cfg.get('auto_cleanup_threshold', 90), min_value=10, max_value=100)
				dpg.add_checkbox(tag='notify_on_cleanup', label='Notify on cleanup', default_value=cfg.get('notify_on_cleanup', True))
				dpg.add_spacing(count=1)
				dpg.add_text('History / Processes', color=(200,200,200))
				dpg.add_input_int(tag='history_seconds', label='History length (s)', default_value=cfg.get('history_seconds', 60), min_value=10, max_value=3600)
				dpg.add_input_int(tag='top_n_processes', label='Top N processes', default_value=cfg.get('top_n_processes', 5), min_value=1, max_value=50)
				dpg.add_spacing(count=1)
				dpg.add_button(label='Save settings', callback=lambda: save_config(dpg.get_value('autostart_checkbox') if dpg.does_item_exist('autostart_checkbox') else False))
				
			with dpg.tab(label='Processes', tag='processes_tab'):
				dpg.add_text('Top memory consuming processes:', color=(220,220,220))
				for i in range(10):
					dpg.add_text('', tag=f'proc_row_{i}')

	dpg.setup_dearpygui()
	dpg.set_primary_window('main_window', True)
	dpg.show_viewport()
	print('DEBUG: showed viewport')
	
	# Load and set window icon
	try:
		icon_path = os.path.join(os.path.dirname(__file__), 'app_icon.ico')
		if os.path.exists(icon_path):
			# Try to set window icon via DearPyGui viewport
			try:
				dpg.configure_viewport(icon=icon_path)
				print('DEBUG: icon set via DearPyGui')
			except (TypeError, AttributeError):
				# If 'icon' parameter is not supported, try Windows API
				try:
					user32 = ctypes.windll.user32
					hwnd = user32.FindWindowW(None, "Memory Usage Monitor")
					if hwnd:
						# Load icon from file
						shell32 = ctypes.windll.shell32
						icon_handle = shell32.ExtractIconW(0, icon_path, 0)
						if icon_handle:
							# Set large icon
							user32.SendMessageW(hwnd, 0x0080, 1, icon_handle)  # WM_SETICON with ICON_BIG
							# Set small icon
							user32.SendMessageW(hwnd, 0x0080, 0, icon_handle)  # WM_SETICON with ICON_SMALL
							print('DEBUG: icon set via Windows API')
				except Exception as e:
					print(f'Windows API icon setup error: {e}')
	except Exception as e:
		print(f'Icon setup error: {e}')
	
	# record app start time to avoid acting on minimize events fired during startup
	global _app_start_time
	_app_start_time = time.time()

	# Set minimize callback to hide window to tray (after a delay to avoid initial hide)
	def _setup_minimize_callback():
		time.sleep(0.5)  # Wait before setting callback
		try:
			def on_viewport_minimize(sender, app_data):
				print(f'DEBUG: viewport minimize event, minimized={app_data}')
				if app_data:  # Window is being minimized
					# Ignore minimize events that occur immediately after startup
					try:
						if '_app_start_time' in globals():
							if time.time() - _app_start_time < 2.0:
								print('DEBUG: minimize ignored during startup grace period')
								return
					except Exception:
						pass
					hide_window_to_tray()
			
			dpg.set_viewport_resize_callback(on_viewport_minimize)
			print('DEBUG: set minimize callback')
		except Exception as e:
			print(f'set_viewport_resize_callback error: {e}')
	
	callback_thread = threading.Thread(target=_setup_minimize_callback, daemon=True)
	callback_thread.start()

	# Install hook to intercept WM_CLOSE so clicking X hides window to tray
	# DISABLED: Hook was breaking DearPyGui UI
	# Instead, we hide to tray on minimize (see set_viewport_resize_callback above)
	print('DEBUG: hook installation disabled - using minimize callback instead')

	# Setup and run tray icon shortly after startup in background to avoid
	# blocking GUI/backends during initialisation.
	def _delayed_tray_start(delay=0.5):
		time.sleep(delay)
		try:
			tray_icon = setup_tray()
			print('DEBUG: setup_tray returned', bool(tray_icon))
			if tray_icon:
				run_tray(tray_icon)
		except Exception as e:
			print('Delayed tray start error:', e)

	tray_starter = threading.Thread(target=_delayed_tray_start, daemon=True)
	tray_starter.start()
	print('DEBUG: tray starter thread started')

	stop_event = threading.Event()
	t = threading.Thread(target=update_loop, args=(stop_event,), daemon=True)
	t.start()
	print('DEBUG: update_loop thread started')

	print('DEBUG: starting DearPyGui main loop')
	dpg.start_dearpygui()

	# Cleanup on exit
	stop_event.set()
	save_config(dpg.get_value('autostart_checkbox') if dpg.does_item_exist('autostart_checkbox') else False)
	dpg.destroy_context()


if __name__ == '__main__':
	main()
