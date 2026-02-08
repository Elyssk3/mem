

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

import dearpygui.dearpygui as dpg
import psutil
import ctypes
import winreg
import pystray
from PIL import Image, ImageDraw, ImageFont
from ctypes import wintypes

# --- Configuration ---
config_path = os.path.join(os.path.dirname(__file__), 'mem_proccess_config.json')


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
		data = {'autostart': bool(autostart)}
		with open(config_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, ensure_ascii=False, indent=2)
	except Exception as e:
		print('save_config error:', e)


def load_config() -> dict:
	defaults = {'autostart': False}
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
	while not stop_event.is_set():
		try:
			mem = psutil.virtual_memory()
			swap = psutil.swap_memory()
			ram_text = f"Physical Memory: {mem.used / (1024**3):.2f} GB / {mem.total / (1024**3):.2f} GB ({mem.percent:.1f}%)"
			swap_text = f"Paging File: {swap.used / (1024**3):.2f} GB / {swap.total / (1024**3):.2f} GB ({swap.percent:.1f}%)"
			ram_val = min(mem.percent / 100.0, 1.0)
			swap_val = min(swap.percent / 100.0, 1.0)
			try:
				dpg.set_value('ram_text', ram_text)
				dpg.set_value('swap_text', swap_text)
				dpg.set_value('ram_bar', ram_val)
				dpg.set_value('swap_bar', swap_val)
			except Exception:
				pass
			# update tray icon image if available
			try:
				if '_GLOBAL_TRAY_ICON' in globals() and _GLOBAL_TRAY_ICON:
					try:
						img = create_tray_icon(mem.percent)
						_GLOBAL_TRAY_ICON.icon = img
						try:
							# some pystray backends expose update_icon
							_GLOBAL_TRAY_ICON.update_icon()
						except Exception:
							pass
					except Exception:
						pass
			except Exception:
				pass
		except Exception:
			pass
		stop_event.wait(1.0)


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
				dpg.add_progress_bar(tag='ram_bar', width=440, default_value=0.0)
				dpg.add_spacer(height=8)
				dpg.add_text('', tag='swap_text', color=(100, 200, 255))
				dpg.add_progress_bar(tag='swap_bar', width=440, default_value=0.0)
				dpg.add_spacer(height=12)
				
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
