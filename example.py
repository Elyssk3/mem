import threading
import dearpygui.dearpygui as dpg
import pystray
from PIL import Image, ImageDraw
import ctypes

# --- Трей и иконка ---
def create_image():
    image = Image.new("RGB", (64, 64), color=(255, 255, 255))
    dc = ImageDraw.Draw(image)
    dc.ellipse((16, 16, 48, 48), fill=(0, 0, 255))
    return image

def show_window():
    dpg.show_viewport()

def quit_app(icon, item):
    icon.stop()
    dpg.stop_dearpygui()

def hide_window():
    dpg.hide_viewport()
    icon = pystray.Icon("app", create_image(), menu=pystray.Menu(
        pystray.MenuItem("Открыть", lambda: show_window()),
        pystray.MenuItem("Выход", quit_app)
    ))
    threading.Thread(target=icon.run, daemon=True).start()

# --- Перехват WM_CLOSE ---
def hook_close_event():
    user32 = ctypes.windll.user32
    hwnd = dpg.get_viewport_platform_handle()  # получаем HWND напрямую
    if hwnd:
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.c_uint, ctypes.c_int, ctypes.c_int)

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == 0x0010:  # WM_CLOSE
                hide_window()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        # Устанавливаем новый оконный обработчик
        user32.SetWindowLongPtrW(hwnd, -4, WNDPROC(wndproc))

# --- Dear PyGui интерфейс ---
dpg.create_context()
dpg.create_viewport(title="Приложение", width=400, height=200)

with dpg.window(label="Главное окно"):
    dpg.add_text("Пример Dear PyGui окна")
    dpg.add_button(label="Скрыть в трей", callback=lambda: hide_window())

dpg.setup_dearpygui()
dpg.show_viewport()

# Подключаем хук на закрытие
hook_close_event()

dpg.start_dearpygui()
dpg.destroy_context()